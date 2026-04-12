# OME — Orbital Mechanics Engine

Computes satellite positions via Keplerian propagation, determines ISL visibility
and ground station access using line-of-sight geometry, schedules terminals, and
publishes events to NATS JetStream.

## Published Events

**NODALARC_OME stream:**
- **VisibilityEvent** — ISL and ground link state changes (1Hz per active pair)
- **ClockTick** — sim-time heartbeat with epoch_id (1Hz)
- **HeartbeatTick** — wall-clock liveness signal

**NODALARC_LINKS stream:**
- **LinkStateSnapshot** — complete admin/carrier/latency state with epoch_id, replace-not-merge (every 5 sim-seconds)

**NODALARC_SESSION stream (MaxMsgsPerSubject=1):**
- **SessionEphemeris** — orbital elements for all satellites + fixed positions for ground stations, published once per epoch (session start and seek). Edges propagate locally.
- **PlaybackState** — playback control state (seeking/playing/paused) with epoch_id

## Architecture

Producer-consumer: a synchronous pacing thread (`time.sleep` for wall-clock precision)
puts events into a queue. An async NATS publisher thread drains the queue to JetStream.
The pacing thread never awaits, never yields, never touches NATS.
