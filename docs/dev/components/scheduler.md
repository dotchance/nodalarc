# Scheduler - Topology Dispatcher

**Location:** `services/scheduler/`
**Deployment:** Kubernetes Deployment (1 replica)
**Entry point:** `services/scheduler/__main__.py`

## Responsibility

The Scheduler bridges the OME's orbital model and the kernel's network interfaces. It translates visibility changes into concrete kernel operations that the Node Agent executes.

## Architecture

Three roles on a single asyncio event loop:

1. **Decision Engine** - NATS JetStream callbacks maintain `_desired_links` (Scheduler's unoverridden desired topology derived from OME state and Scheduler safety policy). Produces `DispatchIntent` objects onto the queue.
2. **Control Plane** - scenario command callback (core NATS request/reply) maintains override state (`_override_pairs`, `_override_nodes`). Produces `DispatchIntent` objects onto the queue.
3. **Actuator** - dispatch worker reconciles `_actual_links` toward queued effective desired state. Sole automatic writer of `_actual_links`, sole automatic caller of Node Agent I/O, sole automatic publisher of LinkUp/LinkDown. Explicit operator repair is the only other Node Agent I/O path and is serialized by the same actuation lock.

Communication: decision engine / control plane -> dispatch queue -> actuator.

## Core Data Structures

```python
_desired_links: dict[tuple[str, str], ActiveLinkInfo]  # OME-derived, never filtered by overrides
_actual_links: dict[tuple[str, str], ActiveLinkInfo]   # Node Agent confirmed state
_override_pairs: dict[tuple[str, str], str]            # pair -> reason
_override_nodes: dict[str, str]                        # node_id -> reason
```

## DispatchIntent

Typed queue payload carrying effective desired state (raw desired minus overrides):

```python
@dataclass(frozen=True, slots=True)
class DispatchIntent:
    desired: dict[tuple[str, str], ActiveLinkInfo]
    down_reasons: dict[tuple[str, str], str]
    forced_bbm_pairs: frozenset[tuple[str, str]]
    sim_time: datetime
    source: Literal["ome_event", "snapshot", "scenario", "resume"]
    rebaseline_counts: bool = False
```

`_build_dispatch_intent()` composes raw desired + overrides into an intent. Override-caused removals get reason attribution and forced BBM classification at enqueue time. The actuator never reads override state directly.

## Reconcile Pattern

`_reconcile_links(desired, nc, sim_time, down_reasons, forced_bbm_pairs)` is the **only automatic** method that dispatches to the Node Agent.

```
VisibilityEvents  --> _apply_events_to_desired() --> _build_dispatch_intent() --> queue --> worker --> _reconcile_links()
LinkStateSnapshot --> _build_desired_from_snapshot() --> _build_dispatch_intent() --> queue --> worker --> _reconcile_links()
ScenarioCommand   --> _on_scenario_command() mutates overrides --> _build_dispatch_intent() --> queue --> worker --> _reconcile_links()
```

All schedule-progression paths converge at the dispatch worker, which calls `_reconcile_links`. This function:
1. Computes links to remove: `actual - desired`
2. Computes links to add: `desired - actual`
3. Dispatches fenced `BatchLinkDown` for removals (with `down_reasons`)
4. Dispatches fenced `BatchLinkUp` for additions
5. Updates `_actual_links` and capacity counters only after exact verified ACKs

If the Node Agent reports `dirty_kernel`, a stale generation, or an
unverified success, dispatch fails loudly. The worker records the block reason
and stops processing the affected generation instead of manufacturing active
state. Read-only `KernelInventory` may be issued by the Scheduler to prove
ground actuation state during audit/recovery; it never writes `_actual_links`.
Explicit operator repair uses Node Agent I/O only under operator intent and the
actuation lock, and reconciles to current OME authority.

## Queue Drain

The dispatch worker drains to the latest intent before processing. Special handling:
- `rebaseline_counts`: OR'd across drained intents (side effect must not be lost)
- `forced_bbm_pairs`: latest intent's set only (override state is most recent)

## Locality Determination

For each link pair, the Scheduler determines whether both endpoints are on the same K8s node (LOCAL) or different nodes (CROSS_NODE). This is set per-interface, not per-batch, because a single batch to one Node Agent may contain both types.

- **LOCAL:** host-mediated veth pair with tc mirred redirect
- **CROSS_NODE:** VXLAN tunnel between nodes

## Latency Updates

Live dispatch latency is OME-authoritative. The OME includes `range_km` and
one-way `latency_ms` on `VisibilityEvent` and active `LinkStateSnapshot` links.
The Scheduler preserves those values in `ActiveLinkInfo`, refuses to dispatch
when they are missing, and sends `SetLatency` when an authoritative snapshot or
visibility event changes the desired latency for an already-active link.

```
latency_ms = range_km / 299792.458 * 1000
```

For CROSS_NODE links, substrate compensation applies:
```
netem_ms = max(0, orbital_latency_ms - substrate_latency_ms)
```

Substrate latency is measured by Node Agents from manifest-required
Kubernetes-node pairs and written to generation-scoped
`nodalarc-substrate-status-<node>` ConfigMaps. The Scheduler validates every
required directional measurement before dispatch and reloads status while
running. Missing, stale, failed, or generation-mismatched cross-node substrate
RTT is unrepresentable and blocks dispatch.
`SessionEphemeris` is consumed for epoch synchronization and edge propagation
contracts; it is not the live Scheduler's latency authority.

## Scenario Override

Scenario commands are received via core NATS request/reply on a session-scoped subject. Override types:
- **Pair override** (`_override_pairs`): suppresses a specific link
- **Node override** (`_override_nodes`): suppresses all links involving a node

Override-caused removals are forced BBM (escalated to GS-segment level for ground links). Unknown pairs/nodes are accepted as future suppressions.

## Communication with Node Agent

NATS request/reply (not JetStream). Each node has subject `nodalarc.agent.{hostname}`. Messages are protobuf-encoded (`lib/nodalarc/proto/node_agent.proto`).

Every request carries `CommandEnvelope(operation_id, session_id,
wiring_generation, operation_kind)`. Scheduler builds those fields from the
validated wiring manifest identity read at startup. Node Agent responses must
name every requested interface or latency entry, must have `verified=true` for
successes, and must not report `dirty_kernel`.

Request types:
- `BatchLinkUp` - activate a set of links
- `BatchLinkDown` - deactivate a set of links
- `SetLatency` - update tc netem on active links

Timeout: 60 seconds (accommodates cold-start VXLAN batch).

## Wiring Gate

Startup reads the Operator-generated wiring manifest, validates the
`session_id` and `wiring_generation`, then waits for typed Node Agent wiring
status. A matching node set is not enough: every expected node must be `ready`,
must match the same session/generation, must report all required phases ready,
and must not be dirty.

## What It Subscribes To

| Subject | Type | Purpose |
|---------|------|---------|
| `nodalarc.ome.{session_id}.>` | JetStream | OME visibility + clock events |
| `nodalarc.links.{session_id}.state` | JetStream | Complete link state snapshot |
| `nodalarc.session.{session_id}.ephemeris` | JetStream | Orbital elements |
| `nodalarc.scheduler.{session_id}.scenario` | Core NATS | Scenario injection commands |

## What It Publishes

| Subject | Stream | Content |
|---------|--------|---------|
| `nodalarc.links.{session_id}.up` | NODALARC_LINKS | LinkUp confirmation |
| `nodalarc.links.{session_id}.down` | NODALARC_LINKS | LinkDown confirmation |
| `nodalarc.links.{session_id}.latency` | NODALARC_LINKS | LatencyUpdate |

## Key Files

| File | Content |
|------|---------|
| `dispatcher.py` | `Dispatcher`, `DispatchIntent`, `_reconcile_links`, `_build_dispatch_intent`, `_on_scenario_command` |
| `__main__.py` | Entry point, session config loading, wiring gate, K8s setup |
| `scenario_handler.py` | `parse_scenario_command` - pure command parsing |
| `latency_model.py` | `PositionTable` - retained on-demand ephemeris propagation helper for diagnostics/tests, not live dispatch authority |
| `pod_locator.py` | `PodLocationMap` - node ID to K3s node + NATS subject |
| `agent_pool.py` | `AgentPool` - NATS client pool for Node Agents |
| `node_agent_client.py` | `NodeAgentClient` - NATS request/reply to one agent |
