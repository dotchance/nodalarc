# Scheduler — Topology Dispatcher

Subscribes to NATS JetStream for OME events, builds desired link state,
and dispatches kernel operations to the Node Agent via `_reconcile_links`.

## Architecture

Three roles on a single asyncio event loop:

1. **Decision Engine** — NATS JetStream callbacks maintain `_desired_links`
   (Scheduler's unoverridden desired topology derived from OME state and
   Scheduler safety policy). Enqueues `DispatchIntent` objects.
2. **Control Plane** — scenario command callback (core NATS request/reply)
   maintains override state (`_override_pairs`, `_override_nodes`).
   Enqueues `DispatchIntent` objects.
3. **Actuator** — dispatch worker reconciles `_actual_links` toward queued
   effective desired state. Sole writer of `_actual_links`, sole caller of
   Node Agent I/O, sole publisher of LinkUp/LinkDown events.

Communication: decision engine / control plane → dispatch queue → actuator.

## Key Design

- **`_reconcile_links(desired, nc, sim_time, down_reasons, forced_bbm_pairs)`**
  is the single path to the Node Agent. All state changes flow through it.
- **`DispatchIntent`** is the typed queue payload — carries effective desired
  state, down_reasons, forced_bbm_pairs, sim_time, source, rebaseline_counts.
- **`_build_dispatch_intent()`** composes raw desired + overrides into an
  immutable intent. Override-caused removals get reason attribution and
  forced BBM classification at enqueue time.
- **LinkStateSnapshot** is applied as replace-not-merge — eliminates window
  boundary drift. Clears `_teardown_pairs` before rebuild.
- **Latency** computed via local Keplerian propagation from SessionEphemeris
  orbital elements. The Scheduler loads ephemeris once per epoch, propagates
  active link endpoints on its 10-second update interval, and applies tc
  netem via SetLatency.
- **Scenario overrides** are declarative future suppressions. Node-level
  overrides suppress all pairs involving that node. Forced BBM escalates
  to GS-segment level for ground links.

## Published Events

- **LinkUp / LinkDown** — dispatched after Node Agent confirms
- **LatencyUpdate** — periodic tc netem updates
