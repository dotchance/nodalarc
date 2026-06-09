# OME - Orbital Mechanics Engine

**Location:** `services/ome/`
**Deployment:** Kubernetes Deployment (1 replica)
**Entry point:** `services/ome/main.py`

## Responsibility

The OME is the physics engine. It consumes the resolved session, propagates
space nodes in their body frames, resolves body ephemeris when present,
determines visibility for the declared link-rule candidate universe, and paces
events out at simulation speed.

For Earth-only LEO sessions this behaves like the original single-body orbital
engine. For multi-segment sessions, the OME still owns the same truth: which
candidate links are geometrically and physically valid at the current simulation
time.

## Threading Model

```
┌─────────────────────┐        queue.Queue         ┌─────────────────────┐
│   Pacing Thread     │ ─────────────────────────→ │  Publisher Thread    │
│   time.sleep()      │                            │  asyncio event loop  │
│   orbital mechanics │                            │  NATS JetStream pub  │
└─────────────────────┘                            └─────────────────────┘
```

**Pacing thread** (synchronous):
- Calls `time.sleep()` for wall-clock precision
- Computes visibility events from precomputed timeline
- Puts events into a bounded `queue.Queue`
- Must never use `asyncio.sleep()` - causes satellite motion jitter

**Publisher thread** (async):
- Runs its own asyncio event loop in a separate thread
- Drains the queue and publishes to NATS JetStream
- Non-blocking queue get with 10-second timeout
- If queue full for >10s, calls `SystemExit(1)` (prevents zombie)

## What It Publishes

| Subject | Stream | Content | Frequency |
|---------|--------|---------|-----------|
| `nodalarc.ome.visibility` | NODALARC_OME | VisibilityEvent (link visible/invisible) | Per event |
| `nodalarc.ome.clock` | NODALARC_OME | ClockTick (sim_time, epoch_id) | Every tick |
| `nodalarc.ome.heartbeat` | NODALARC_OME | HeartbeatTick | Periodic |
| `nodalarc.links.state` | NODALARC_LINKS | LinkStateSnapshot (complete forwarding state) | Snapshot interval and every terminal MBB lifecycle tick |
| `nodalarc.session.ephemeris` | NODALARC_SESSION | SessionEphemeris (orbital elements) | Once per epoch |
| `nodalarc.session.playback` | NODALARC_SESSION | PlaybackState (playing/paused/seeking) | On state change |
| `nodalarc.ops.{session}.ome.MBB_TEARDOWN_TERMINAL` | NODALARC_OPS | Typed terminal MBB lifecycle OpsEvent | On teardown completion, successor abort, or epoch invalidation |

## Window Computation

The OME computes in windows. One window covers one orbital period (~95 minutes at 550 km LEO). For each window:

1. Propagate body positions and all space-node positions at discrete time steps
2. Check line-of-sight, range, body occlusion, terminal geometry, and ground
   scheduling for the resolved candidate links
3. Compute the exact time each link becomes visible and invisible
4. Build a timeline of all events in chronological order
5. Pace the timeline out at simulation speed

Window computation happens once, then events are paced in real time. For the
176-satellite LEO session, computation takes roughly tens of seconds on the
reference development hardware. This is startup/epoch-boundary cost, not
runtime cost.

## Segment and Body Model

The OME does not infer cross-segment connectivity from proximity alone.
`link_rules` declare the candidate universe. Each link's class is derived from
the roles at its endpoints — authors never write a class:

- access — body-local ground-to-space access
- inter-constellation — space-to-space within the same body frame
- inter-body relay — space-to-space across body frames, carrying an explicit
  protocol boundary

Earth/Luna sessions use a local BSP ephemeris kernel for body positions. The
session resolver rejects unsupported body/frame grammar before OME startup
instead of letting OME approximate it silently.

## Playback Controls

The OME handles pause/resume/set_speed/seek via NATS core request/reply on `nodalarc.ome_control.playback` (not JetStream - this is synchronous RPC).

- **Pause:** Pacing thread stops sleeping, sim_time frozen
- **Resume:** Pacing thread resumes sleeping, sim_time advances
- **Set speed:** Changes the `time.sleep()` divisor (2x speed = half the sleep time)
- **Seek:** Discontinuous sim_time jump. Resets epoch state, forces new LinkStateSnapshot, implies resume

## Checkpoint and Recovery

The OME writes a `SchedulingCheckpoint` periodically with:
- Current sim_time
- Playback state (paused, time_accel)
- `written_at` wall clock timestamp

On restart, if a checkpoint exists and `written_at` is within 30 seconds, the OME resumes from that point. If older than 30 seconds, discard and start fresh at wall time.

## Init Container

The OME has an init container that creates all NATS JetStream streams before the main process starts. This ensures streams exist before any publisher or subscriber starts.

## Key Files

| File | Content |
|------|---------|
| `main.py` | Entry point, thread setup, checkpoint logic |
| `pacing.py` | Pacing thread, time controls, event timeline |
| `publisher.py` | Async NATS publisher thread |
| `propagator.py` / `propagation_engine.py` | Mean-element orbital propagation and common-frame transforms |
| `lib/nodalarc/ephemeris_runtime.py` | Body ephemeris loading and validation for local BSP kernels |
| `visibility.py` | Line-of-sight computation, ground scheduling |
| `event_stream.py` | Timeline builder, window computation |
