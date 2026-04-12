# NodalArc Architecture

## What NodalArc Is

NodalArc is an orbital network emulation platform. It deploys real routing stacks (FRRouting with IS-IS, OSPF, SR-MPLS, BGP, or any combination) on containerized satellite and ground station nodes whose connectivity is driven by real orbital mechanics. Satellites orbit, links appear and disappear as line-of-sight geometry changes, latencies shift as ranges change, and the routing protocols running inside each node react exactly as they would on real hardware. The result is a full-fidelity emulation environment where you can observe, test, and measure how routing protocols behave on satellite constellation topologies.

This is not a simulator. Every satellite and every ground station is a real Linux network namespace running a real FRR routing daemon. Packets traverse real kernel interfaces with real tc netem latency shaping. IS-IS hello packets, OSPF LSAs, and MPLS label operations happen in the kernel, not in a model. When you exec into a satellite pod and run `show isis neighbor`, you see the same adjacency table you would see on a physical router.

## System Overview

NodalArc runs on Kubernetes (K3s). A single `make all` command builds all container images, deploys the platform, and starts a constellation session. The platform supports single-node deployments (a laptop running K3s) up through multi-node clusters where satellite pods are distributed across physical machines with VXLAN tunnels connecting cross-node links.

The system has six core components, a messaging backbone, and a set of session pods that represent the emulated network:

```
                        +---------------------------------+
                        |  Visualization Frontend (VF)    |
                        |  React 19 + Three.js            |
                        |  3D globe, topology graph       |
                        +----------|----------------------+
                                   | WebSocket ~1Hz
                        +----------|----------------------+
                        |  VS-API                         |
                        |  FastAPI REST + WebSocket        |
                        |  Constellation state aggregator  |
                        +-----|----------|---------|-------+
                              |          |         |
                     NATS Sub |  NATS Sub|  NATS Sub|
                              |          |         |
              +------------+  | +--------|-+  +----|-------+
              |  OME       |  | | Scheduler|  | NATS       |
              |  Orbital   |--+ | Topology |  | JetStream  |
              |  Mechanics |    | Dispatch |  | Message    |
              |  Engine    |    |          |  | Backbone   |
              +------------+    +----||----+  +------------+
                                     ||
                              NATS request/reply
                              (BatchLinkUp/Down)
                                     ||
                    +----------------||----------------+
                    |         Node Agent (DaemonSet)   |
                    |  Runs on every K3s node          |
                    |  pyroute2 kernel operations      |
                    +-----------|---|------------------+
                                |   |
                      veth pairs|   |tc netem/tbf
                                v   v
    +----------+ +----------+ +----------+ +----------+
    | sat-P00S00| sat-P00S01| sat-P01S00| gs-hawthorne|
    |  FRR     | |  FRR     | |  FRR     | |  FRR     |
    |  IS-IS   | |  IS-IS   | |  IS-IS   | |  IS-IS   |
    +----------+ +----------+ +----------+ +----------+
        Session Pods - one per satellite and ground station
```

### K8s Operator

A kopf-based operator watches for `ConstellationSpec` custom resources. When you create a session (via the browser wizard or `make session`), the Operator:

1. Parses the session YAML (constellation geometry, ground stations, routing stack)
2. Renders per-node FRR configuration from Jinja2 templates
3. Creates FRR config ConfigMaps for each node
4. Computes pod placement across available K3s nodes using the configured placement policy
5. Measures baseline network latency between K3s nodes (for substrate compensation)
6. Creates session pods with the FRR container and ConfigMap volume mounts
7. Execs into each pod to copy configs and touch the FRR startup sentinel
8. Writes the topology wiring manifest ConfigMap
9. Monitors wiring progress and advances the session to Ready

Session teardown is handled by deleting the ConstellationSpec CR. Kubernetes garbage collection (via ownerReferences) cascades the deletion to all session pods and ConfigMaps.

## Component Deep Dives

### OME - Orbital Mechanics Engine

The OME is the physics engine. It takes a constellation definition (number of orbital planes, satellites per plane, altitude, inclination) and a set of ground stations (latitude, longitude, elevation mask), propagates satellite positions using Keplerian orbital mechanics (via SGP4/skyfield), and determines which satellites can see each other and which ground stations are overhead.

The OME computes in windows. One window covers one orbital period (~95 minutes for a 550 km LEO constellation). For each window, the OME precomputes every visibility event (every ISL that becomes possible or impossible, every ground station pass) along with the continuously changing range and latency for each visible pair. This precomputed timeline is then paced out at simulation speed.

The OME publishes events to three NATS JetStream streams:

**NODALARC_OME stream:**

**VisibilityEvent** - A link becoming visible or invisible. Contains the satellite pair (or satellite-ground station pair), the scheduling decision (whether the Scheduler should activate this link), and the current range/latency. VisibilityEvents carry scheduling semantics: the OME decides which ground station gets which satellite based on elevation angle, tracking capacity, and scheduling policy.

**ClockTick** - Simulation time heartbeat with `epoch_id`. Consumers use this to detect stale data, synchronize displays, and resume from epoch transitions.

**NODALARC_LINKS stream:**

**LinkStateSnapshot** - The complete admin/carrier/latency state for every link in the constellation, with `epoch_id`. Published every 5 simulation seconds. This is a replace-not-merge snapshot. The Scheduler uses it to reconcile its internal state, eliminating accumulation bugs at orbital window boundaries. If the OME says a link exists, the Scheduler activates it. If the OME doesn't mention a link, the Scheduler deactivates it. There is no other path.

**NODALARC_SESSION stream (MaxMsgsPerSubject=1):**

**SessionEphemeris** - Keplerian orbital elements for all satellites and fixed positions for all ground stations. Published once per epoch (session start and on seek). Downstream consumers (Scheduler, VS-API, VF) run local Keplerian propagation from these elements to compute positions on demand. No per-tick position data is published — NATS bandwidth for position data is effectively zero regardless of constellation size.

**PlaybackState** - Playback control state (`seeking`, `playing`, `paused`) with `epoch_id`. Late-joining subscribers instantly know the current playback state. The `seeking` state is a mutex — pause/set_speed commands are rejected during a seek.

The OME does not publish per-tick position data. Edges propagate locally from SessionEphemeris.

The OME uses a producer-consumer threading model. A synchronous pacing thread calls `time.sleep()` for wall-clock precision and puts events into a Python queue. An async NATS publisher thread drains the queue and publishes to JetStream. This separation keeps the pacing thread free from network I/O blocking and prevents the publisher from affecting timing accuracy.

### Scheduler - Topology Dispatcher

The Scheduler is the bridge between the OME's orbital model and the kernel's network interfaces. It subscribes to NATS JetStream for OME events and translates visibility changes into concrete kernel operations that the Node Agent executes.

The Scheduler maintains a single data structure: `_active_links`, a dict of currently active link pairs and their properties (latency, bandwidth, interface names). When new OME events arrive (either individual VisibilityEvents or a full LinkStateSnapshot), the Scheduler builds a "desired" link state and calls `_reconcile_links()`. This function computes the delta between desired and current state, sends `BatchLinkDown` for links that should no longer exist, then `BatchLinkUp` for links that should now exist, then updates `_active_links` with the results.

`_reconcile_links` is the single path to the Node Agent for all link state changes. There is no other mechanism for creating or destroying links. This invariant eliminates an entire class of state synchronization bugs that plagued earlier architectures with multiple dispatch paths.

For each link pair, the Scheduler determines locality: LOCAL (both endpoints on the same K3s node) or CROSS_NODE (endpoints on different nodes). LOCAL links use host-mediated veth pairs with tc mirred redirect (pod-side interfaces are always admin UP; carrier is controlled by host-side veth admin state). CROSS_NODE links use VXLAN tunnels. The locality is set per-interface on the protobuf message, not per-batch, because a single batch to one Node Agent may contain both LOCAL and CROSS_NODE interfaces.

The Scheduler also handles latency updates. As satellites move, the range between connected pairs changes continuously. The Scheduler loads `SessionEphemeris` orbital elements once per epoch and propagates active link endpoints locally via Keplerian propagation on its 10-second update interval. It computes the new one-way latency (`range_km / 299792.458 * 1000` ms) and sends `SetLatency` commands to the Node Agent, which updates tc netem on each interface. For CROSS_NODE links, the Scheduler applies substrate compensation: `netem_ms = max(0, orbital_latency - physical_substrate_latency)`. The physical substrate latency is measured by the Operator at session start via ICMP ping between K3s nodes. The total packet delay (netem + physical network) then equals the orbital latency regardless of the physical network between nodes.

The Scheduler communicates with the Node Agent via NATS request/reply. Each K3s node has a NATS subject (`nodalarc.agent.{hostname}`). The Scheduler serializes protobuf messages, sends them as NATS requests, and waits for the Node Agent's protobuf response. The timeout is 60 seconds to accommodate the initial batch of VXLAN tunnel creation on cold start.

### Node Agent - DaemonSet

The Node Agent runs on every K3s node as a DaemonSet pod with `hostPID: true` and `hostNetwork: true`. It is the only component that touches the Linux kernel's network stack. It receives commands from the Scheduler and executes them using pyroute2 (a pure-Python netlink library).

When the Node Agent starts, it reads the topology wiring manifest from a ConfigMap and creates the base network infrastructure for every pod on its node:

- **ISL interfaces**: host-mediated veth pairs with tc mirred redirect (v0.70 carrier-gated model). Each satellite has 2-4 ISL interfaces (isl0/isl1 for intra-plane, isl2/isl3 for cross-plane). Pod-side interfaces are always admin UP; host-side veth admin state controls carrier. This faithfully models real satellite hardware: transceivers are always powered, link signal comes and goes.
- **Ground interfaces**: A `gnd0` interface inside each satellite and ground station pod, connected via a host-side bridge with tc mirred redirect rules. Ground links are dynamic. The bridge attachment changes as satellites pass over ground stations.
- **Sysctls**: IPv4/IPv6 forwarding, rp_filter disabled, MPLS input enabled on each interface.
- **Default route removal**: The K8s default route (via eth0 to the CNI network) is removed so FRR's IGP-learned routes are the only forwarding paths.

The Node Agent enters pod network namespaces using the `setns()` syscall directly via ctypes. It does NOT use pyroute2's `NetNS()` which forks a child process. The fork approach caused signal handler inheritance, port conflicts, and silent complete data plane failures (documented in `docs/node-agent-fork-issue.md`). The `setns()` approach is a single syscall: enter the target namespace, perform the operation with `IPRoute()`, return to the host namespace. No fork, no child process, no fd leakage.

For CROSS_NODE links (pods on different K3s nodes), the Node Agent creates VXLAN tunnels in the host network namespace. Each VXLAN tunnel consists of:
1. A VXLAN interface (`vxNNNNN`) in the host namespace, configured with the local and remote node IPs and a deterministic VNI
2. A veth pair (`vhNNNNN` host-side, `vpNNNNN` pod-side) connecting the VXLAN to the pod
3. Bidirectional tc mirred redirect rules between the VXLAN interface and the veth host-end
4. The veth pod-end is moved into the target pod's network namespace and renamed to the interface name (e.g., `isl2`)

VXLAN creation is idempotent. If the target interface already exists inside the pod namespace (from a prior Scheduler retry after timeout), the creation is skipped. If host-side VXLAN interfaces exist from a partial prior attempt, they are cleaned up before recreation.

The Node Agent's NATS server does not subscribe until the wiring thread has completed and populated the PID map. This startup gate prevents the Scheduler from dispatching link operations to a Node Agent that doesn't yet know where pods are.

### NATS JetStream - Message Backbone

All inter-component messaging uses NATS JetStream. There are no other transports: no ZMQ, no gRPC (except for NodalPath sessions), no direct HTTP between backend components.

Three JetStream streams handle all traffic:

**NODALARC_OME** - OME events (VisibilityEvent, ClockTick, HeartbeatTick). Limits-based retention, 128 MB. Multiple consumers (Scheduler, VS-API) each maintain independent consumer positions.

**NODALARC_LINKS** - Link state events (LinkUp, LinkDown, LatencyUpdate, LinkStateSnapshot). Limits-based retention with MaxMsgsPerSubject=1 for the snapshot subject, so the latest snapshot is always available for catch-up on Scheduler restart.

**NODALARC_SESSION** - Session-level state (SessionEphemeris, PlaybackState). MaxMsgsPerSubject=1 ensures late-joining subscribers always receive exactly the current ephemeris and playback state — no history replay, no application-level filtering. Separate from NODALARC_OME because OME needs unlimited per-subject retention for VisibilityEvent history, while session state needs single-message-per-subject semantics.

Subject definitions are centralized in `lib/nodalarc/nats_channels.py`. No component uses literal subject strings. All subjects are imported from this single file.

NATS request/reply (not JetStream) is used for Scheduler-to-Node Agent communication. This is synchronous RPC: the Scheduler sends a protobuf-encoded request and blocks until the Node Agent responds with the result. The request/reply pattern provides backpressure. The Scheduler cannot dispatch faster than the Node Agent can process.

### VS-API - Visualization State API

The VS-API is a FastAPI server that aggregates constellation state from all other components and serves it to visualization clients. It subscribes to NATS JetStream streams (NODALARC_OME, NODALARC_LINKS, NODALARC_SESSION) and maintains an in-memory representation of the current constellation state: active links, link latencies, recent events. On WebSocket connect, the VS-API sends the cached `SessionEphemeris` so the browser can run local Keplerian propagation for satellite positions. Node positions are computed locally by each consumer from the ephemeris — the VS-API does not proxy per-tick position data.

The VS-API provides:
- **WebSocket** at `ws://host:8080/ws/v1/state` - full constellation state snapshot at ~1 Hz
- **REST** at `GET /api/v1/state` - current snapshot on demand
- **Session management** - list, create, switch sessions
- **Path trace** - forwarding path lookup between any two nodes

The VS-API also writes periodic snapshots to SQLite for historical playback.

### VF - Visualization Frontend

A React 19 + Three.js single-page application served by nginx. Connects to the VS-API WebSocket and renders:
- 3D globe with satellite positions orbiting in real-time
- ISL links (colored by latency) and ground station connections
- 2D topology graph
- Real-time latency readouts on each link
- Event log showing link state changes, GS handoffs, convergence events
- Session wizard for creating new constellation deployments
- Satellite and ground station detail panels

### Session Pods

Each satellite and ground station in the constellation runs as a K8s pod containing an FRR container. The FRR container runs the official FRRouting image (`quay.io/frrouting/frr:10.3.1`) with a NodalArc entrypoint that waits for configuration delivery before starting daemons via FRR's built-in `watchfrr` supervisor.

The Operator delivers FRR configuration in two steps: first, it creates a ConfigMap containing the rendered FRR config files (frr.conf, daemons) and mounts it into the pod at `/etc/frr-config/`. Then, after the pod reaches Running, the Operator execs into the FRR container to copy the configs to `/etc/frr/` and touch the startup sentinel. This two-step process is compatible with the stock FRR entrypoint which waits for the sentinel before starting daemons.

FRR configuration is generated from Jinja2 templates at session creation time. The template system supports multiple routing stacks (IS-IS, OSPF, IS-IS+SR-MPLS, OSPF+MPLS-TE, static SR, BGP, LDP) selected per session. Each template receives the full addressing scheme (loopback IPs, interface IPs, area assignments, SR SID indexes) computed from the constellation geometry.

Ground station pods also have:
- A `terr0` interface representing the terrestrial network connection
- Terrestrial prefix advertisements redistributed into the IGP
- Default route origination (`0.0.0.0/0`) via IS-IS `default-information originate` so that satellites connected to a ground station prefer the direct ground path for internet-bound traffic

## Data Flow

A complete cycle from orbital mechanics to user-visible routing behavior:

1. The OME propagates satellite positions forward in time, computes line-of-sight visibility between all pairs, and publishes events to NATS JetStream. A LinkStateSnapshot every 5 simulation seconds provides the complete desired link state.

2. The Scheduler receives the LinkStateSnapshot, builds a desired state dict (which links should exist with what latency/bandwidth), and calls `_reconcile_links()`. This computes the delta against `_active_links` and dispatches `BatchLinkDown` for removed links, then `BatchLinkUp` for added links, to the appropriate Node Agent(s) via NATS request/reply.

3. The Node Agent on each K3s node receives the batch commands and executes kernel operations: creating/destroying veth pairs or VXLAN tunnels, applying tc netem for latency and tc tbf for bandwidth shaping, managing ground station bridge attachments. For ground link handoffs, the Node Agent detaches the old satellite from the ground bridge and attaches the new one. Carrier state on `gnd0` transitions through LOWERLAYERDOWN, triggering FRR to tear down the old adjacency and form a new one.

4. FRR inside each pod detects the interface state changes. When an ISL interface comes UP with carrier, FRR sends IS-IS hellos (or OSPF hellos), forms an adjacency with the peer, floods LSPs/LSAs, and runs SPF to recompute the routing table. When an interface goes DOWN, FRR tears down the adjacency and reconverges. This is real protocol behavior, the same code that runs on production routers.

5. The VS-API subscribes to NATS JetStream, caches the latest `SessionEphemeris`, and pushes it to connected WebSocket clients on connect and on epoch changes. The VF runs local Keplerian propagation from the ephemeris to render satellite positions at 60fps. Link state, latencies, and events are pushed via WebSocket at ~1 Hz from ClockTick and LinkStateSnapshot.

6. As satellites continue orbiting, latencies change continuously. The Scheduler propagates active link endpoints locally from the SessionEphemeris orbital elements and sends `SetLatency` updates to the Node Agent, which adjusts tc netem delay values. FRR sees the metric change (IS-IS wide metrics are derived from bandwidth, not latency) and may recompute SPF if the topology changes are significant enough.

## Multi-Node Architecture

On a single-node K3s cluster, all satellite pods run on one machine and ISL links are host-mediated veth pairs with tc mirred redirect: fast, no encapsulation overhead beyond the mirred indirection. This is the default and covers the majority of deployments.

When the constellation exceeds what a single node can handle (roughly 500 satellites on a 32 GB machine), pods are distributed across multiple K3s nodes using placement policies:

- **allOnOne** - all pods on one node. No cross-node traffic. Default.
- **planePerNode** - each orbital plane on a separate K3s node. Intra-plane ISLs (isl0, isl1) are local veth pairs. Cross-plane ISLs (isl2, isl3) traverse VXLAN tunnels between nodes.
- **planeGroupPerNode** - groups of adjacent planes per node. Minimizes tunnel count.

Cross-node links use point-to-point VXLAN tunnels created by the Node Agent in the host network namespace. Each tunnel has a deterministic VNI computed from the endpoint node IDs and interface names. The Scheduler sets per-interface locality (LOCAL or CROSS_NODE) on every BatchLinkUp/Down message so the Node Agent knows whether to create a veth pair or a VXLAN tunnel.

Substrate latency compensation keeps cross-node link latency accurate. The physical network between K3s nodes adds real latency to every VXLAN-encapsulated packet. The Operator measures this baseline latency at session start (ICMP ping between nodes), stores it in a ConfigMap, and the Scheduler subtracts it from the orbital latency when setting tc netem: `netem_ms = max(0, orbital_ms - substrate_ms)`. The total packet delay (netem plus physical network) equals the orbital latency. The UI shows the orbital latency (what the user cares about), not the tc netem value.

For multi-node deployments, images must be available on all nodes. Single-node deployments import images directly into K3s containerd (`imagePullPolicy: Never`). Multi-node deployments push images to a container registry and nodes pull from it (`imagePullPolicy: IfNotPresent`). The registry configuration is set via `REGISTRY_PREFIX` and `HELM_EXTRA_ARGS` in `config.mk`, not hardcoded in the platform.

## Ground Station Handoffs

Ground station connectivity is fundamentally different from ISL connectivity. ISLs are symmetric point-to-point links between two satellites. Ground links are asymmetric, dynamic, and involve carrier state signaling that the routing protocol must detect.

When a satellite passes over a ground station and the elevation angle exceeds the minimum threshold, the OME publishes a VisibilityEvent with a scheduling decision. The Scheduler dispatches a `BatchLinkUp` to the Node Agent. The Node Agent:

1. Attaches the satellite's host-side veth (`_gnd_P{plane}S{slot}`) to the ground station's bridge via tc mirred redirect
2. Brings the satellite's `gnd0` interface UP. Carrier arrives, transitioning from LOWERLAYERDOWN to UP
3. FRR detects carrier on gnd0, sends IS-IS hellos, and forms an adjacency within 1-2 seconds
4. Applies tc netem latency shaping on both the satellite and ground station gnd0 interfaces

When the satellite moves below the elevation threshold, the process reverses:

1. The Scheduler dispatches `BatchLinkDown`
2. The Node Agent removes tc mirred redirect and brings the satellite host-side veth DOWN
3. Carrier drops on `gnd0`. FRR immediately tears down the adjacency (no hold timer wait)
4. IS-IS reconverges, traffic reroutes through other paths

For cross-node ground links (satellite on one K3s node, ground station on another), the Node Agent creates a VXLAN tunnel between the satellite's host-side ground veth and the ground station's bridge port, using the same VXLAN mechanism as cross-node ISLs.

Ground stations originate a default route (`0.0.0.0/0`) into IS-IS at metric 100. When a satellite has an active ground link, IS-IS prefers the direct ground path (metric ~110) over ISL hops to a distant ground station (metric 500+). This models real satellite network behavior where ground-connected satellites are preferred gateways for internet-bound traffic.

## Teardown

The teardown process is handled by `tools/na-teardown.sh`, the only permitted teardown mechanism. It executes a 9-step sequence:

1. Strip kopf finalizers from ConstellationSpec CRs and delete them
2. Wait for session pods to terminate (force-delete stuck pods after timeout)
3. Exec into Node Agent DaemonSet pods to clean host-side kernel state (VXLAN, veth, bridges) on all nodes. No SSH, pure kubectl exec with hostNetwork
4. Helm uninstall (removes DaemonSet, Deployments, Services, ConfigMaps)
5. Wait for Node Agent pods to terminate
6. Delete namespace (force-remove finalizers if stuck in Terminating)
7. Delete cluster-scoped resources (CRD, ClusterRoles, ClusterRoleBindings)
8. Final local kernel state catch-all cleanup
9. Verify clean state. Fail if any nodalarc resources or kernel interfaces remain

The teardown must succeed regardless of the system's state: running session, crashed pods, stuck image pulls, partially deployed, or already clean.
