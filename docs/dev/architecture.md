# System Architecture

A simulator can keep the whole universe in one process.

NodalArc cannot.

The point of the system is to let real routing implementations react to a
moving orbital network. That means the pieces have to stay honest. Orbital
mechanics should not know how to configure FRR. The Scheduler should not know
how to draw a globe. The Node Agent should not know why a link exists; it should
only know how to make the kernel obey.

That separation is the architecture.

Kubernetes gives us the rooms. NATS carries the facts between them. The OME
moves the sky. The Scheduler turns visibility into desired link state. The Node
Agent touches the host kernel. FRR routes inside the session pods. VS-API
gathers what happened. VF shows it to a human.

```
                        +---------------------------------+
                        |  Visualization Frontend (VF)    |
                        |  React 19 + R3F + Three.js      |
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
                              (fenced BatchLinkUp/Down/SetLatency)
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
    | space-sat-p00s00| space-sat-p00s01| space-sat-p01s00| ground-gs-hawthorne|
    |  FRR     | |  FRR     | |  FRR     | |  FRR     |
    |  IS-IS   | |  IS-IS   | |  IS-IS   | |  IS-IS   |
    +----------+ +----------+ +----------+ +----------+
        Session pods - one per satellite and ground station
```

## How One Link Becomes Real

Start with geometry.

The OME decides two satellites can see each other. That fact goes onto NATS.
The Scheduler receives it, compares the desired topology against what is already
active, and emits only the delta. The Node Agent gets the request and does the
host work: veths, VXLAN, carrier state, `tc` shaping.

FRR sees an interface come up. Not a fake event. A real interface. It sends
hellos, forms an adjacency, floods state, runs SPF, and changes the forwarding
table.

By the time the browser paints the link, the network has already had to live
with it.

The full cycle looks like this:

1. **OME** propagates satellite positions, computes line-of-sight visibility,
   and publishes visibility, clock, heartbeat, ephemeris, and playback events.

2. **Scheduler** consumes visibility and snapshot state, builds desired link
   state, reconciles it against active link state, and dispatches
   fenced `BatchLinkUp`, `BatchLinkDown`, and `SetLatency` requests to Node
   Agents. It updates active state only after exact verified ACKs.

3. **Node Agent** performs kernel operations on the host: veth creation, VXLAN
   tunnel setup, carrier changes, `tc netem` latency, `tc tbf` bandwidth
   shaping, and ground bridge attachment. It reports success only after
   checking the kernel postcondition for each requested entry.

4. **FRR** inside each session pod reacts to interface state. IS-IS sends
   hellos, OSPF floods LSAs, BGP updates neighbors, MPLS labels enter the
   kernel. The behavior is protocol implementation behavior, not a model of it.

5. **VS-API** subscribes to NATS, aggregates state for clients, serves REST
   requests, and pushes WebSocket updates to browsers.

6. **VF** renders satellite positions locally from `SessionEphemeris`, draws
   live link state, and gives the user a way to inspect what the network is
   doing.

## Component Responsibilities

Each component owns a narrow piece of the machine. If a change needs one
component to reach through another component's boundary, stop and rethink it.

| Component | Owns | Publishes | Subscribes |
| --- | --- | --- | --- |
| OME | Orbital state, visibility, clock | VisibilityEvent, ClockTick, HeartbeatTick, LinkStateSnapshot, SessionEphemeris, PlaybackState | Playback control requests |
| Scheduler | Desired links, active links, dispatch ordering | LinkUp, LinkDown, LatencyUpdate | VisibilityEvent, LinkStateSnapshot, SessionEphemeris |
| Node Agent | Host kernel network state | Request replies, wiring progress, substrate measurements, OpsEvents | Scheduler requests, wiring manifest |
| VS-API | Aggregated state for clients | WebSocket broadcasts | OME, link, session, ops, and debug streams |
| Operator | Session pod lifecycle and generated configs | Kubernetes resources | ConstellationSpec CR watch |
| VF | Rendering and user interaction | None | VS-API WebSocket |

## NATS Streams

NATS is the spine. Components publish facts and subscribe to the facts they
need. They do not discover each other, call each other by service name, or
smuggle state through side channels.

| Stream | Contents | Retention model |
| --- | --- | --- |
| `NODALARC_OME` | VisibilityEvent, ClockTick, HeartbeatTick | Limits-based history for two orbital periods |
| `NODALARC_LINKS` | LinkUp, LinkDown, LatencyUpdate, LinkStateSnapshot, GroundLinkDecisionSnapshot, ActualLinkSnapshot | Replace-not-merge latest state per subject; bounded link-event history |
| `NODALARC_SESSION` | SessionEphemeris, PlaybackState | Latest value per subject |
| `NODALARC_MI` | Measurement and instrumentation events | Bounded event history |
| `NODALARC_OPS` | Operational events, including OME MBB terminal lifecycle events | Four-hour transient transport; terminal lifecycle events that matter after the run are persisted by VS-API into SQLite/session artifacts |
| `NODALARC_DEBUG` | On-demand debug events | Five-minute transient history |


MBB lifecycle terminal facts do not live only in `NODALARC_LINKS`.
`LinkStateSnapshot`, `GroundLinkDecisionSnapshot`, and `ActualLinkSnapshot` are
current-state, replace-not-merge subjects; later ticks overwrite them. `ActualLinkSnapshot`
is Scheduler-published forwarding-plane proof (`_actual_links` plus pending actuation), not
OME authority. When an MBB teardown
completes, aborts, or is invalidated by a seek, the OME publishes a typed
`MBB_TEARDOWN_TERMINAL` `OpsEvent` on `NODALARC_OPS`, and VS-API persists it
into `ome_lifecycle_events` for post-run analysis.

Core NATS request/reply is used where persistence would be wrong:

- Scheduler to Node Agent: `BatchLinkUp`, `BatchLinkDown`, `SetLatency`
- VS-API to OME: pause, resume, set speed, seek
- VS-API to services: debug control

## Why The Boundaries Exist

### Why NATS, not direct HTTP

Direct calls make services know too much. The OME should publish visibility
because visibility is a fact. It should not know whether the Scheduler, VS-API,
a recorder, or a future measurement service is listening.

That gives us a pattern: publish facts, consume facts, keep ownership local.
Adding a consumer should mean subscribing to a subject, not changing the
producer.

### Why JetStream, not only core NATS

Some facts need memory. If the Scheduler restarts, it has to recover the latest
session ephemeris and link snapshot. If VS-API reconnects, it needs the current
playback state. JetStream gives those facts a shelf life without making
application code invent replay logic.

Request/reply still uses core NATS. A Node Agent command is an operation, not
history. It should succeed with proof, fail loudly, or time out.

### Why reconcile-based dispatch

The Scheduler does not treat each visibility event as a tiny imperative command.
It builds desired state, compares that against actual state, and reconciles.

That matters. Missed events self-heal on the next snapshot. Epoch boundaries do
not accumulate stale edges. Running the same reconcile twice with the same
desired state does nothing.

The dispatch worker is the actuator. It is the only writer of active link state
and the only code path that sends link up/down operations to Node Agents.
If a Node Agent reports a stale generation, unverified success, or dirty kernel,
the worker stops dispatching that generation instead of manufacturing active
state from partial evidence.

### Why host-mediated networking

The emulated topology cannot be handed to the CNI and wished into existence.
NodalArc needs carrier control, per-interface latency shaping, bandwidth limits,
ground bridge attachment, and cross-node links that look like local interfaces
inside the pods.

The Node Agent owns that work. It creates the host-side plumbing, moves the pod
ends into namespaces, drives carrier state from the host, and shapes the link.
FRR sees ordinary interfaces. That is the point.

### Why position propagation is distributed

The OME does not publish per-tick satellite positions. It publishes ephemeris
for the epoch. Consumers propagate positions locally.

That keeps NATS bandwidth from growing with frame rate. The browser can render
at 60fps without asking the backend for 60 position updates per second. The
Scheduler can compute visibility when it needs it. VS-API can answer state
requests without turning OME into a position server.

Build the pattern once. Let each component use it where it stands.

## Multi-Node Model

On one Kubernetes node, an ISL is host-mediated veth plumbing with `tc` redirect
and shaping. Across Kubernetes nodes, it becomes a VXLAN tunnel with a pod-side
interface at each end.

The Scheduler marks every interface operation with locality:

- `LOCAL` - both session pods live on the same Kubernetes node
- `CROSS_NODE` - session pods live on different Kubernetes nodes

The Node Agent does not query the cluster to decide what to build. The Scheduler
already resolved placement and put the answer in the dispatch message.

Physical lab networks still have latency. NodalArc compensates for it. The
Operator declares manifest-required Kubernetes-node substrate pairs from actual
pod placement. Each Node Agent measures its local required pairs before serving
commands, writes a generation-scoped `nodalarc-substrate-status-<node>`
ConfigMap, and refreshes it periodically. The Scheduler gates startup and
runtime dispatch on those durable status documents, rejects stale, failed, or
generation-mismatched measurements, and subtracts the verified substrate delay
from the orbital delay before setting `tc netem`. Unknown cross-node substrate
latency is a dispatch blocker, not a zero-delay default.

## Session Lifecycle

A session joins the primitives: constellation geometry, satellite type, ground
station set, routing stack, placement policy, and time model.

The path from a button click to routing looks like this:

```text
User clicks Deploy in the wizard
    -> VS-API validates config and creates a ConstellationSpec CR
    -> Operator watches the CR and creates session pods
    -> Operator renders FRR configs from templates
    -> Operator writes the topology wiring manifest
    -> Node Agent detects the manifest and wires interfaces
    -> Node Agent writes typed wiring status for the same session/generation
    -> Operator advances the session to Ready
    -> Scheduler opens its wiring gate for that session/generation
    -> OME begins publishing orbital events
    -> Scheduler activates visible links
    -> FRR forms adjacencies and converges
    -> VS-API pushes state to VF
    -> User sees the live constellation
```

The important part is not the number of steps. The important part is ownership.
The Operator owns pod lifecycle and generated config. The Node Agent owns
kernel wiring. The OME owns orbital truth. The Scheduler owns reconciliation.
FRR owns routing.

Keep those boundaries intact.

For deeper detail, read the component documents in [components/](components/)
and the non-negotiable rules in [Architectural Invariants](invariants.md).
