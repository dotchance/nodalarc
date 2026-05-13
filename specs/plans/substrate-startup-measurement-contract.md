# Plan: Startup Substrate Measurement Contract

## Purpose

Substrate RTT is a physical Kubernetes node-pair fact. The Scheduler needs this
fact before it can compute trustworthy cross-node netem compensation. Therefore
the substrate measurement producer cannot depend on Scheduler dispatch having
already created a VXLAN link.

The goal of this plan is to make substrate RTT a first-class, pre-dispatch,
generation-fenced control-plane fact. Missing, stale, failed, mismatched, or
implied substrate RTT must block cross-node dispatch. The system must never
fall back to zero for cross-node links.

## Ownership

- Operator owns topology expansion, pod placement, and the wiring manifest.
- Node Agent owns host-network substrate measurement from each physical node.
- Scheduler owns substrate evidence gating and netem compensation.
- VS-API and visualization may display status, but they do not invent or infer
  substrate truth.

## Core Contract

Add shared substrate measurement models under `lib/nodalarc/substrate/`.

Required models:

- `RequiredSubstratePair`
  - `source_node`
  - `source_ip`
  - `target_node`
  - `target_ip`
  - `reasons`
  - `pair_key`
  - `directional_key`
- `SubstrateMeasurement`
  - `session_id`
  - `wiring_generation`
  - `source_node`
  - `source_ip`
  - `target_node`
  - `target_ip`
  - `measured_at`
  - `stale_after`
  - `status`
  - `sample_count`
  - `success_count`
  - `median_rtt_ms`
  - `min_rtt_ms`
  - `max_rtt_ms`
  - `error_message`
- `SubstrateStatusDocument`
  - `session_id`
  - `wiring_generation`
  - `source_node`
  - `measurements`

All models must reject extra fields. Runtime contracts must validate session,
generation, node names, host IPs, measurement status, sample counts, and
freshness.

## Phase 1: Operator Manifest Authority

Update `services/nodalarc_operator/session_deployer.py` to compute required
substrate pairs while writing the topology wiring manifest.

Rules:

1. Use Kubernetes node InternalIPs from the same node discovery authority used
   for placement.
2. Build required pairs from actual session pod placement.
3. ISL pairs come from `assign_isl_neighbors()` plus pod placement.
4. Ground pairs come from ground-station to satellite possibilities plus pod
   placement.
5. Emit directional pairs for both node directions when two Kubernetes nodes
   differ.
6. For single-node sessions, emit an empty list.
7. Include `required_substrate_pairs` in the wiring manifest before deriving
   `wiring_generation`.
8. Delete stale substrate status ConfigMaps when writing a new manifest, but do
   not rely on deletion for correctness. Generation validation remains the
   safety mechanism.
9. Deployment RBAC must give the Scheduler service account namespace-scoped
   `list` access to ConfigMaps, because substrate status documents are selected
   by label. A missing permission must fail startup rather than bypass the gate.

Tests:

- Single-node manifest emits no substrate pairs.
- Multi-node ISL manifest emits the expected directional node pairs.
- Multi-node ground manifest emits the expected directional node pairs.
- Placement changes alter the derived wiring generation.

## Phase 2: Node Agent Manifest-Driven Measurement

Rewrite `services/node_agent/substrate_monitor.py` so startup measurement is
manifest-driven, not only VXLAN-peer-driven.

Behavior:

1. After reading the wiring manifest and setting `session_id` /
   `wiring_generation`, each Node Agent filters `required_substrate_pairs`
   where `source_node == hostname`.
2. It verifies `HOST_IP == source_ip`. Mismatch is fatal.
3. It measures each target IP with structured evidence.
4. It writes one authoritative ConfigMap per source node:
   `nodalarc-substrate-status-<source-node>`.
5. It periodically refreshes those measurements before `stale_after`.
6. Failed measurements are written as failed evidence, not hidden.
7. ConfigMap write failure is fatal or blocks command serving. It must not be
   silently ignored for substrate truth.
8. Measurement execution must be injectable for tests.

VXLAN peer references may remain as diagnostics, but they must not be the
bootstrap authority for substrate RTT.

Tests:

- Node Agent writes successful measurement documents.
- Node Agent writes failed measurement evidence.
- Node Agent rejects startup on `HOST_IP` mismatch.
- Node Agent does not serve commands before substrate status exists for its
  required local pairs.

## Phase 3: Scheduler Substrate Gate

Update Scheduler to treat substrate status as a startup and runtime gate.

Files:

- `services/scheduler/__main__.py`
- `services/scheduler/dispatcher.py`
- `services/scheduler/substrate_latency.py`

Behavior:

1. Read required substrate pairs from the wiring manifest.
2. Read all `nodalarc-substrate-status-*` ConfigMaps.
3. Validate every required directional measurement against:
   - `session_id`
   - `wiring_generation`
   - source and target node
   - source and target IP
   - status
   - sample count
   - timestamp
   - freshness
4. Refuse cross-node dispatch until all required evidence is present.
5. Return `0.0` only for same-Kubernetes-node links.
6. Do not log "single-node deployment" unless the manifest has zero required
   substrate pairs.
7. Periodically reload substrate status while running.
8. If evidence becomes stale or failed, block affected cross-node dispatch with
   an explicit reason.

Startup should wait for a bounded timeout and then exit fatal if substrate
evidence is incomplete. Runtime staleness should block dispatch and surface a
critical reason.

Tests:

- Missing evidence is rejected.
- Stale evidence is rejected.
- Failed evidence is rejected.
- Wrong session is rejected.
- Wrong generation is rejected.
- Wrong IP is rejected.
- Complete fresh evidence is accepted.
- Scheduler restart recovers evidence from ConfigMaps.

## Phase 4: Node Agent Mutation Defense

Before any cross-node mutation in `services/node_agent/handlers.py`, Node Agent
must verify fresh local substrate evidence for the remote IP under the current
`session_id` and `wiring_generation`.

Scheduler should prevent missing evidence, but Node Agent is the privileged
substrate actuator and must not trust Scheduler blindly.

Tests:

- Cross-node mutation without local substrate evidence is rejected before
  mutation.
- Cross-node mutation with stale, failed, wrong-session, or wrong-generation
  evidence is rejected before mutation.

## Phase 5: Remove Duplicate And Legacy Paths

After the new path is tested:

1. Remove Scheduler consumption of NATS substrate latency events as dispatch
   authority.
2. Remove `configured_rtt_by_node_pair` fallback from substrate resolution.
3. Remove the legacy `nodalarc-substrate-latency` ConfigMap reader.
4. Remove peer-ref measurement triggers from Node Agent handlers, or keep them
   only as explicit diagnostics that do not feed dispatch authority.
5. Remove legacy `peers` substrate event parsing tests.
6. Update docs to say ConfigMap substrate status is the control-plane source of
   truth. NATS/OpsEvents may be observability, not dispatch authority.

## Phase 6: Verification

Required before live redeploy:

1. `uv run pytest --ignore=tests/integration --tb=short -q`
2. `npm --prefix frontend run test`
3. `make nuke`
4. `make all`
5. Load a 176-node session.
6. Verify all platform images match the commit tag.
7. Verify Node Agents write substrate status for all required node pairs.
8. Verify Scheduler starts only after substrate evidence is present.
9. Verify there is no Scheduler CrashLoopBackOff.
10. Verify no stale UI session is shown.
11. Verify cross-node links render and dispatch.
12. Verify known previously problematic nodes such as `P10S02`, `P15S10`,
    `P09S02`, and `P14S10` have real link state, not visual-only state.

## Stop Conditions

Stop and report instead of coding around the issue if:

1. Required ground substrate pairs cannot be derived cleanly from current config
   models.
2. Measurement volume is too large for ConfigMap size or refresh cadence.
3. Node Agent cannot reliably identify its Kubernetes node and host IP.
4. Scheduler health/status has no clean way to expose "blocked on substrate
   evidence."

## Non-Goals

- Do not restore cross-node zero fallback.
- Do not suppress Scheduler failures.
- Do not make NATS substrate events the only dispatch source of truth.
- Do not rely on VXLAN creation as the first substrate measurement trigger.
- Do not invent topology or substrate locality.
