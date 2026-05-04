# System Architecture

## Overview

NodalArc is a distributed system deployed on Kubernetes. Six backend services, a messaging backbone, and a visualization frontend work together to emulate satellite constellation networks with real routing stacks.

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

## Data Flow

A complete cycle from orbital mechanics to user-visible routing behavior:

1. **OME** propagates satellite positions, computes line-of-sight visibility between all pairs, publishes events to NATS JetStream.

2. **Scheduler** receives events, builds desired link state, calls `_reconcile_links()` to compute the delta, dispatches `BatchLinkUp/Down` to the appropriate Node Agent(s) via NATS request/reply.

3. **Node Agent** executes kernel operations: creates/destroys veth pairs or VXLAN tunnels, applies tc netem for latency and tc tbf for bandwidth shaping, manages ground station bridge attachments.

4. **FRR** inside each pod detects interface state changes. When an ISL comes UP with carrier, FRR sends hellos, forms an adjacency, floods LSPs, runs SPF. Real protocol behavior, same code as production routers.

5. **VS-API** subscribes to NATS, aggregates state, pushes to WebSocket clients at ~1 Hz. Sends `SessionEphemeris` on connect so clients run local Keplerian propagation.

6. **VF** renders satellite positions at 60fps from ephemeris, shows link state and events from VS-API WebSocket.

## NATS JetStream Streams

| Stream | Contents | Retention |
|--------|----------|-----------|
| `NODALARC_OME` | VisibilityEvent, ClockTick, HeartbeatTick | Limits-based, 128 MB |
| `NODALARC_LINKS` | LinkUp, LinkDown, LatencyUpdate, LinkStateSnapshot | MaxMsgsPerSubject=1 for snapshot |
| `NODALARC_SESSION` | SessionEphemeris, PlaybackState | MaxMsgsPerSubject=1 |

Additionally, NATS core request/reply (not JetStream) is used for:
- Scheduler → Node Agent: BatchLinkUp, BatchLinkDown, SetLatency
- VS-API → OME: playback control (pause, resume, set_speed, seek)

## Component Responsibilities

| Component | Owns | Publishes | Subscribes |
|-----------|------|-----------|------------|
| OME | Orbital state, visibility, clock | VisibilityEvent, ClockTick, LinkStateSnapshot, SessionEphemeris, PlaybackState | Playback control requests |
| Scheduler | Active link set, dispatch | LinkUp, LinkDown, LatencyUpdate | VisibilityEvent, LinkStateSnapshot, SessionEphemeris |
| Node Agent | Kernel network state | (responds to requests) | Scheduler requests, wiring manifest |
| VS-API | Aggregated state for clients | WebSocket broadcasts | All NATS streams |
| Operator | Session pod lifecycle | (K8s resources) | ConstellationSpec CR watch |
| VF | Rendering, user interaction | (none - receives only) | VS-API WebSocket |

## Key Design Decisions

### Why NATS, not direct HTTP

Services don't know about each other. The OME doesn't know the Scheduler exists. The Scheduler doesn't know the VS-API exists. They all publish to subjects and subscribe to subjects. Adding a new consumer (a monitoring system, a recording service) is zero-cost - subscribe to the subject, done.

### Why JetStream, not core NATS

Events can't be lost between component restarts. JetStream provides:
- Message persistence and replay
- Consumer position tracking (each subscriber resumes where it left off)
- MaxMsgsPerSubject=1 for latest-value semantics (ephemeris, playback state)

### Why reconcile-based dispatch

The Scheduler doesn't track individual VisibilityEvents. It builds the complete desired state and reconciles against its current state. This means:
- Missed events self-heal on the next snapshot
- No accumulation bugs across epoch boundaries
- Idempotent - running reconcile twice with the same desired state is a no-op

### Why host-mediated networking (not CNI plugins)

Session pod networking is entirely custom. The Node Agent creates veth pairs, VXLAN tunnels, and tc rules in the host network namespace, then moves interface ends into pod namespaces. This gives us:
- Carrier-state control (admin UP/DOWN the host-side veth to control link state)
- Precise latency shaping per interface
- VXLAN tunnels for cross-node links
- Full control over the network topology without fighting CNI

### Why position propagation is distributed

The OME doesn't publish per-tick satellite positions. It publishes Keplerian orbital elements (SessionEphemeris) once per epoch. Every consumer (Scheduler, VS-API, VF) runs local Keplerian propagation to compute positions on demand. This means:
- NATS bandwidth for position data is zero regardless of constellation size
- 60fps rendering without 60 network messages per second
- No clock synchronization issues between services

## Multi-Node Architecture

On a single node, ISL links are host-mediated veth pairs with tc mirred redirect. On multi-node:

- **Local links** (same node): veth pair + tc mirred
- **Cross-node links** (different nodes): VXLAN tunnel + veth pair into pod

The Scheduler sets per-interface locality (LOCAL or CROSS_NODE) on every dispatch message. The Node Agent knows whether to create a veth or a VXLAN without querying anything.

Substrate latency compensation: the Node Agent measures physical inter-node latency continuously and publishes it. The Scheduler subtracts physical latency from orbital latency when setting tc netem values. Total packet delay always equals orbital latency.

## Session Lifecycle

```
User clicks Deploy in wizard
    → VS-API validates config, creates ConstellationSpec CR
    → Operator watches CR, creates pods with placement policy
    → Operator renders FRR configs from templates, delivers to pods
    → Operator writes topology wiring manifest ConfigMap
    → Node Agent detects manifest, wires all interfaces
    → Node Agent signals wiring complete
    → Operator advances session to Ready
    → OME begins publishing events
    → Scheduler activates links
    → FRR forms adjacencies, routing converges
    → VS-API pushes state to VF
    → User sees live constellation
```

For detailed component documentation, see the [Components](components/) directory.
