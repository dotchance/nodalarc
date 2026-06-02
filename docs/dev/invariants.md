# Architectural Invariants

These are not style preferences.

Each rule here exists because breaking it produced a failure mode that was
expensive to find and easy to reintroduce. If you change code in the Scheduler,
OME, Node Agent, NATS layer, or forwarding plane, read this first. The system
only stays understandable while these boundaries hold.

If a proposed change needs to violate one of these rules, the change is not
ready. Fix the design before touching the code.

## Session Type Boundary

IGP sessions and NodalPath sessions share the OME, Scheduler, and Node Agent wiring. They share nothing in the forwarding plane.

**IGP sessions (IS-IS, OSPF, BGP, any FRR combination):**
- FRR owns forwarding state entirely
- Scheduler dispatches BatchLinkUp/Down to Node Agent via NATS request/reply
- Node Agent manipulates kernel interfaces via pyroute2
- No Node Agent gRPC. No NETCONF. No `GetTopology`.
- `scheduler.agent_pool.AgentPool` is allowed: it is the NATS request/reply
  client pool for Node Agents, not a forwarding-plane agent inventory.
- If you find any of these in an IGP code path, it is wrong. Remove it.

**NodalPath sessions:**
- nodalpath-fwd sidecar owns forwarding state via pyroute2 into policy table 100
- FRR is observability only (zebra + staticd)
- gRPC pushes ForwardingTableUpdates to sidecars
- gRPC exists here and only here

Before writing any Scheduler, Node Agent, or forwarding-plane code: determine which session type it applies to. If the answer is both, the code must not assume either.

## Interface Map is Static

`_interface_map` is built from the constellation definition at session start. It is static for the entire session lifetime. It must never be filtered, modified, or rebuilt from runtime queries.

The OME governs which links are visible. The Scheduler dispatches based on OME events only. The interface map tells you what CAN exist; the OME tells you what DOES exist right now. Read-only kernel verification (`KernelInventory`) may prove whether Scheduler state still matches Node Agent kernel state; it must never rebuild or mutate the interface map.

## Single Dispatch Path

`_reconcile_links(...)` in the Scheduler is the **only automatic** method that dispatches
link state changes to the Node Agent. There is no other automatic mechanism for creating
or destroying links.

In production, only the dispatch worker calls `_reconcile_links`.

Decision callbacks and control-plane callbacks do not talk to Node Agents. They
mutate their owned state, build a `DispatchIntent`, and put it on the dispatch
queue. The worker drains that queue, reconciles latest effective desired state
against actual state, and then calls `_reconcile_links`.

The legacy `_dispatch_batch` helper exists for tests only. Do not add new
behavior there and do not treat it as a production path.

This invariant keeps the actuator honest. `_actual_links`, capacity counters,
Node Agent I/O, and LinkUp/LinkDown publication for normal schedule progression all stay behind one door. The one deliberate exception is explicit operator repair: it is operator-initiated, tagged with an intervention id, serialized by the same actuation lock, and must reconcile the GS to current OME authority rather than retry stale work.

## Node Agent Truth Contract

Node Agent commands are fenced by `session_id` and `wiring_generation`.
Malformed frames, stale generations, missing PIDs, missing `HOST_IP`, missing
peer identity, and protobuf enum zero values fail before mutation.

Node Agent success means the MVP kernel proof passed. Batch replies name every
requested interface, SetLatency replies name every requested entry, and
successful entries have `verified=true`. `KernelInventory` is a read-only proof command used only for ground actuation verification and recovery; it may prove state clean or dirty, but it must never mutate kernel state and must never create `_actual_links` entries. A cleanup/proof/rollback failure sets `dirty_kernel=true`; the Scheduler must stop dispatch for that generation instead of treating aggregate counts as truth.

Wiring readiness is typed. The Scheduler gate checks manifest session,
manifest generation, expected nodes, required phase status, and dirty-kernel
state. Node-set equality alone is never readiness.

## NATS Subject Centralization

All NATS subjects are defined in `lib/nodalarc/nats_channels.py`. No literal subject strings anywhere else. This ensures:
- You can grep for any subject and find every publisher/subscriber
- Renaming a subject is one edit, not a hunt through 6 services
- Typos in subject strings are caught at import time, not at runtime

## OME Threading Model

The OME uses a producer-consumer threading model:

- **Pacing thread** - synchronous `time.sleep()`, produces events to `queue.Queue`
- **Publisher thread** - asyncio in a separate thread, consumes queue, publishes to NATS

The pacing thread must never be converted to async. `asyncio.sleep()` does not provide wall-clock precision - it yields to the event loop and resumes when "convenient." For orbital mechanics, timing jitter causes visible satellite motion artifacts.

`time.sleep()` is precise to 1ms on Linux. This is why the pacing thread is synchronous.

## No Fork in Namespace Operations

Never use `pyroute2.NetNS()`. It forks a child process that:
- Inherits signal handlers (SIGTERM handler runs in the child)
- Inherits socket file descriptors and NATS connections
- Creates orphaned processes that prevent clean restart

Use `_in_namespace(pid, fn)` from `namespace_ops.py` - a single `setns()` syscall. Enter the namespace, perform the operation, return. No fork, no child process, no fd leakage.

## Node Agent Startup Gate

The Node Agent's NATS server must not subscribe to requests until the wiring thread has fully populated `pid_map`. If it subscribes early, the Scheduler dispatches link operations to a Node Agent that doesn't know where pods are.

Share the pid_map from wiring - never rediscover PIDs during request handling. Missing node_id = return error immediately.

## Stream Creation Ownership

The OME init container creates all NATS JetStream streams before any other pod starts. Application code never creates streams at runtime. This ensures:
- Stream configuration is consistent (retention, limits, subjects)
- No race between stream creation and first publish
- Downstream consumers can assume streams exist

## LinkStateSnapshot Replace-Not-Merge

`LinkStateSnapshot` on `NODALARC_LINKS` uses `MaxMsgsPerSubject=1`. Only the latest snapshot is retained. The Scheduler reconciles against this complete snapshot - if the OME says a link exists, the Scheduler activates it. If the OME doesn't mention a link, the Scheduler deactivates it.

There is no delta/merge logic. The snapshot IS the desired state. This eliminates accumulation bugs at orbital window boundaries.

## Snapshot Sequence Monotonicity

`snapshot_seq` is monotonically increasing and never resets. The Scheduler uses it to detect stale/out-of-order snapshots. If `seq <= current`, discard. This holds across seeks, epoch transitions, and OME restarts.

## Master Sim Time on the Wire

All wire-format `sim_time` fields carry **master sim_time** — the OME's pacing clock — never entity-local time. This holds for `VisibilityEvent`, `GroundLinkDecisionSnapshot`, `LinkStateSnapshot`, `ClockTick`, every NATS payload, every SQLite persistence record.

Per-entity local time is a **computed view**. A future `entity_local_time(master_time, clock_config)` helper will derive a node's proper time from master sim_time plus its `NodeClockConfig` (Phase 7 — per-individual-entity clock skew from oscillator drift, relativistic proper-time delta across bodies, ground-ranging sync events). Each entity carries its own clock characteristics; the delta is a function of master_time + per-entity config, never a stored accumulator.

Consumers that need entity-local time call the helper. The wire never carries it as the primary timestamp.

Today master sim_time equals entity-local for every node. That equivalence is a current implementation accident, not a property to lean on:

- Where new code touches time, name the parameter `master_sim_time` (or `master_sim_time_unix`), not bare `sim_time`. A future rename is then mechanical.
- The propagator's time input is "the time this entity's physics integrates against," not "the global sim time."
- Seek operates on master clock only. Per-entity time is computed, not stored — no per-entity state to reset on seek, in either direction.
- Per-entity drift must be deterministic from `(master_time, entity_clock_config)`. No tick-accumulator state on entity records. Arbitrary forward/reverse seek depends on this.
- No per-entity `epoch_id`. Master epoch is the only epoch.

This invariant exists so a future per-entity clock model is a localized addition — a new helper, a new model — not a wire-format archaeology project across every consumer.

## Ground Link Carrier Model

Ground station `gnd0` carrier is driven by host-side veth state - not by explicit admin manipulation inside the pod:

- LinkUp: bring host-side veths UP → carrier arrives on pod gnd0 → FRR forms adjacency
- LinkDown: bring host-side veths DOWN → carrier drops on pod gnd0 → FRR tears adjacency immediately

FRR detects carrier loss without waiting for hold timers. This is the fastest convergence path.

## rp_filter at Pod Creation

`rp_filter=0` is set by the Operator as pod-level sysctls at creation time. The Node Agent does not set it. Without it, IS-IS/OSPF multicast hellos fail reverse-path filtering silently - routing appears broken with no errors in any log.

## No Eliminated Patterns

The following were removed after causing bugs. Do not reintroduce:

- `FullStateSnapshot` - replaced by `LinkStateSnapshot`
- `_pending_vis` - visibility event buffering caused ordering bugs
- `_ome_catchup()` - catch-up logic was unreliable across epoch boundaries
- `_dedup_threshold` - replaced by `snapshot_seq` monotonic ordering
- 15-second watchdog - replaced by queue timeout + SystemExit
- ZMQ (anything) - fully removed, NATS-only
- `_dispatch_ups` / `_dispatch_downs` - replaced by `_reconcile_links`
