# Scheduler - Topology Dispatcher

**Location:** `services/scheduler/`
**Deployment:** Kubernetes Deployment (1 replica)
**Entry point:** `services/scheduler/main.py`

## Responsibility

The Scheduler bridges the OME's orbital model and the kernel's network interfaces. It translates visibility changes into concrete kernel operations that the Node Agent executes.

## Core Data Structure

```python
_active_links: dict[tuple[str, str], ActiveLinkInfo]
```

The single source of truth for what links currently exist. Keyed by (node_a, node_b) pair. Contains latency, bandwidth, interface names, and locality (LOCAL/CROSS_NODE).

## Reconcile Pattern

`_reconcile_links(desired: dict[pair, ActiveLinkInfo], nc)` is the **only** method that dispatches to the Node Agent.

```
VisibilityEvents  ──→  _dispatch_batch()  ──→  build desired  ──→  _reconcile_links()
LinkStateSnapshot ──→  _on_link_state_snapshot()  ──→  build desired  ──→  _reconcile_links()
```

Both paths converge at `_reconcile_links`. This function:
1. Computes links to remove: `current - desired`
2. Computes links to add: `desired - current`
3. Dispatches `BatchLinkDown` for removals
4. Dispatches `BatchLinkUp` for additions
5. Updates `_active_links`

`_dispatch_lock` covers the full sequence to prevent interleaving.

## Locality Determination

For each link pair, the Scheduler determines whether both endpoints are on the same K8s node (LOCAL) or different nodes (CROSS_NODE). This is set per-interface, not per-batch, because a single batch to one Node Agent may contain both types.

- **LOCAL:** host-mediated veth pair with tc mirred redirect
- **CROSS_NODE:** VXLAN tunnel between nodes

## Latency Updates

The Scheduler loads `SessionEphemeris` orbital elements once per epoch and propagates active link endpoints locally via Keplerian propagation on a 10-second interval. For each active link:

```
latency_ms = range_km / 299792.458 * 1000
```

For CROSS_NODE links, substrate compensation applies:
```
netem_ms = max(0, orbital_latency_ms - substrate_latency_ms)
```

Substrate latency is measured by the Node Agent and published to `SUBJECT_SUBSTRATE_LATENCY`.

## Communication with Node Agent

NATS request/reply (not JetStream). Each node has subject `nodalarc.agent.{hostname}`. Messages are protobuf-encoded (`lib/nodalarc/proto/node_agent.proto`).

Request types:
- `BatchLinkUp` - activate a set of links
- `BatchLinkDown` - deactivate a set of links
- `SetLatency` - update tc netem on active links

Timeout: 60 seconds (accommodates cold-start VXLAN batch).

## What It Subscribes To

| Subject | Purpose |
|---------|---------|
| `nodalarc.ome.visibility` | Individual link visibility events |
| `nodalarc.links.state` | Complete link state snapshot (reconcile) |
| `nodalarc.session.ephemeris` | Orbital elements for local propagation |
| `nodalarc.substrate.latency` | Physical inter-node latency measurements |

## What It Publishes

| Subject | Stream | Content |
|---------|--------|---------|
| `nodalarc.links.up` | NODALARC_LINKS | LinkUp confirmation |
| `nodalarc.links.down` | NODALARC_LINKS | LinkDown confirmation |
| `nodalarc.links.latency` | NODALARC_LINKS | LatencyUpdate |

## Key Files

| File | Content |
|------|---------|
| `dispatcher.py` | `_reconcile_links`, `_dispatch_batch`, `_on_link_state_snapshot` |
| `main.py` | Entry point, NATS subscriptions, consumer setup |
| `latency.py` | Keplerian propagation for latency updates |
| `substrate.py` | Substrate latency map management |
