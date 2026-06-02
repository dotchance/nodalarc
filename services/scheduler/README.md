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
   effective desired state. Sole automatic writer of `_actual_links`, sole
   automatic caller of Node Agent I/O, sole automatic publisher of
   LinkUp/LinkDown events. Explicit operator repair is the only other Node
   Agent I/O path and is serialized by the same actuation lock.

Communication: decision engine / control plane → dispatch queue → actuator.

## Key Design

- **`_reconcile_links(desired, nc, sim_time, down_reasons, forced_bbm_pairs)`**
  is the single automatic path to the Node Agent. All schedule-progression
  state changes flow through it. Explicit operator repair is a separate
  intervention path, tagged and serialized by the actuation lock.
- **`DispatchIntent`** is the typed queue payload — carries effective desired
  state, down_reasons, forced_bbm_pairs, sim_time, source, rebaseline_counts.
- **`_build_dispatch_intent()`** composes raw desired + overrides into an
  structurally frozen intent. Override-caused removals get reason attribution and
  forced BBM classification at enqueue time.
- **LinkStateSnapshot** is applied as replace-not-merge — eliminates window
  boundary drift. Clears `_teardown_pairs` before rebuild.
- **Latency** is OME-authoritative in the live dispatch path. VisibilityEvents
  and LinkStateSnapshots carry `range_km` and one-way `latency_ms`; the
  Scheduler preserves those values, applies substrate compensation for
  cross-node links, and uses SetLatency when authoritative desired latency
  changes on an active link. Stale or missing live substrate measurements block
  cross-node dispatch.
- **Node Agent ACKs** are exact and verified. Scheduler requires a fenced
  request envelope, one response entry per requested interface/latency entry,
  `verified=true` for successes, and `dirty_kernel=false`. Read-only
  `KernelInventory` is allowed only for ground actuation proof/audit and never
  writes `_actual_links`.
- **Wiring gate** checks typed session/generation status and all required
  wiring phases. Matching node names alone are not readiness.
- **Scenario overrides** are declarative future suppressions. Node-level
  overrides suppress all pairs involving that node. Forced BBM escalates
  to GS-segment level for ground links.

## Published Events

- **LinkUp / LinkDown** — published after verified Node Agent ACKs
- **LatencyUpdate** — published after per-entry verified tc netem updates
