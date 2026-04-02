# Scheduler — Topology Dispatcher

Subscribes to NATS JetStream for OME events, builds desired link state,
and dispatches kernel operations to the Node Agent via `_reconcile_links`.

## Key Design

- **`_reconcile_links(desired, nc, sim_time)`** is the single path to the Node Agent.
  Both live VisibilityEvents and LinkStateSnapshot build a desired state dict and call it.
- **LinkStateSnapshot** is applied as replace-not-merge — eliminates window boundary drift.
- **Latency** comes from the snapshot (OME-authoritative). Position table provides
  inter-snapshot refinement via `_update_latencies`.

## Published Events

- **LinkUp / LinkDown** — dispatched after Node Agent confirms
- **LatencyUpdate** — periodic tc netem updates
