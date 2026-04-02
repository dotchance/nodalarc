# OME — Orbital Mechanics Engine

Computes satellite positions via Keplerian propagation, determines ISL visibility
and ground station access using line-of-sight geometry, schedules terminals, and
publishes events to NATS JetStream.

## Published Events

- **VisibilityEvent** — ISL and ground link state changes (1Hz per active pair)
- **TimelinePositionSnapshot** — all satellite/GS positions (1Hz)
- **LinkStateSnapshot** — complete admin/carrier/latency state, replace-not-merge (every 5 sim-seconds)
- **ClockTick** — sim-time heartbeat (1Hz)

## Architecture

Producer-consumer: a synchronous pacing thread (`time.sleep` for wall-clock precision)
puts events into a queue. An async NATS publisher thread drains the queue to JetStream.
The pacing thread never awaits, never yields, never touches NATS.
