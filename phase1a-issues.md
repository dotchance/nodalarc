# Phase 1A Plan: Critical Evaluation — 10 Issues

## Issue 1 (CRITICAL): Shared Library Has Illegal PyYAML Dependency

**Problem:** Step 1 lists `lib/pyproject.toml` with deps `pydantic>=2.0` and `pyyaml`. PRD Section 13.13 explicitly states: "It has no dependencies beyond Pydantic and the Python standard library." PyYAML is not part of the standard library.

YAML loading is a component responsibility, not a shared library responsibility. The shared lib defines Pydantic models that accept Python dicts (via `model_validate()`). Components call `yaml.safe_load()` themselves and pass the resulting dict to the model. If the shared lib depends on pyyaml, it violates the dependency constraint and couples the data modeling layer to the serialization format.

**Proposed Fix:** Remove pyyaml from `lib/pyproject.toml`. The only lib dependency is `pydantic>=2.0`. Add pyyaml to each component's dependencies (or the root `pyproject.toml` dev deps). YAML loading happens in `ome/constellation_loader.py`, `na-deploy`, etc. — not in model constructors.

---

## Issue 2 (CRITICAL): ISL Neighbor Assignment Has No Clear Home

**Problem:** Step 8 puts `assign_isl_neighbors()` in `ome/constellation_loader.py`. Step 5 says `build_template_vars()` in the shared lib "computes neighbors (Section 13.4 priority: intra-fwd, intra-aft, cross-right, cross-left)." This creates a contradiction: either the neighbor computation is in the OME (and the shared lib can't compute `interface_info` with `peer_node_id` without importing OME code), or the neighbor computation is duplicated in both places.

**What the PRD says:**
- Section 13.27: `constellation_loader.py` — "Load YAML, validate via Pydantic, return ConstellationConfig. Expand parametric mode into satellite list. Parse TLE files. Does NOT: Compute anything orbital."
- Section 13.25: `build_template_vars()` returns `interface_info` containing `peer_node_id` for each interface.
- Section 13.4: ISL neighbor assignment is "deterministic based on the satellite's terminal configuration and its position in the constellation grid" — purely structural, not orbital.

The neighbor assignment algorithm is plane/slot modular arithmetic. It has nothing to do with orbital mechanics. It's the same kind of identity derivation that `AddressingScheme` does.

**Proposed Fix:** Move ISL neighbor assignment to the shared library, specifically `lib/nodalarc/models/addressing.py` alongside identity derivation. Add `assign_isl_neighbors(constellation, addressing) -> dict[str, list[NeighborAssignment]]` that uses the expanded satellite list + terminal config + Section 13.4 priority rules to produce a mapping of node_id → ordered list of ISL neighbors. `build_template_vars()` then calls this function (or receives its output) to populate `interface_info`. `constellation_loader.py` stays focused on YAML → Pydantic → expanded satellite list — no neighbor logic in the OME.

---

## Issue 3 (CRITICAL): `build_template_vars()` Does Too Much

**Problem:** Step 5 says this function "computes: node identity, loopbacks, interfaces, neighbors (Section 13.4 priority), area_id, interface_info with link_type/cross_area, terrestrial_prefixes, merged config_overrides." That's 8 distinct computations in one function. The 500-line module limit (Section 13.21 constraint #4) will be extremely tight for `template_vars.py` if all this logic lives there.

**What the PRD says:** Section 13.25 gives the function signature with 10 parameters and a complex return dict. It states "Both na-deploy and na-reconfig call this function. No other code constructs template variable namespaces." This is the single public API, but it doesn't have to be a single monolithic implementation.

**Proposed Fix:** Keep `build_template_vars()` as the public API in `template_vars.py`, but have it delegate to well-named helper functions that already exist or belong elsewhere:
- Identity/IP derivation → already in `AddressingScheme` methods (addressing.py)
- Area assignment → already in `compute_area_assignments()` (addressing.py)
- Neighbor assignment → the new `assign_isl_neighbors()` in addressing.py (per Issue 2 fix)
- Terrestrial prefix resolution → small helper function in `template_vars.py`
- Config override merging → trivial dict merge in `template_vars.py`

With these delegations, `build_template_vars()` becomes an ~80-line orchestrator that calls existing functions. `template_vars.py` stays well under 500 lines. The heavy logic lives in `addressing.py` which is already its natural home.

---

## Issue 4 (CRITICAL): No OME Entry Point

**Problem:** The plan lists `ome/propagator.py`, `ome/visibility.py`, `ome/constellation_loader.py`, and `ome/event_stream.py` but there is no `ome/main.py` or CLI entry point. There's no way to actually run the OME. There's also no way to verify the Phase 1A exit criterion "OME produces correct visibility timelines" end-to-end without manually wiring the modules together.

**What the PRD says:** The repo structure (Section 9) shows `ome/` with a Dockerfile + 4 Python files. The OME is a standalone service. Section 13.27 says `event_stream.py` "Drive the time loop." But something has to call event_stream with the right config.

**Proposed Fix:** Add `ome/main.py` to Step 8. This module:
- Parses args via argparse (session config path, output dir)
- Calls `yaml.safe_load()` + Pydantic `model_validate()` to load configs
- Calls `constellation_loader.load_constellation()` and `load_ground_stations()`
- Creates AddressingScheme from session config
- Calls `event_stream.precompute_timeline()` for the first orbital period
- Writes JSON Lines output file
- Optionally starts ZeroMQ PUB for real-time mode
- Under 100 lines (orchestration only, no logic)

Also requires `ome/__init__.py` — see Issue 7.

---

## Issue 5 (MODERATE): TimelinePositionSnapshot and ClockTick Frequency Is Ambiguous

**Problem:** The plan says event_stream.py emits "VisibilityEvents + TimelinePositionSnapshots" but doesn't clarify WHEN snapshots are emitted. The PRD says "Embed full-constellation position snapshots at each unique event timestamp." If events only occur when visibility CHANGES, the Discrete-Event mode TO may lack position data between changes for its `latency_update_interval_seconds` (default: 10s) updates.

Consider: if visibility changes happen at t=0s and t=47s, and the TO needs to recompute latency every 10s, it has no position data at t=10s, t=20s, t=30s, or t=40s.

**What the PRD says:**
- Section 13.27 on event_stream.py: "Drive the time loop. Call propagator and visibility at each step."
- `ClockTick` is listed as an event type (Section 7)
- DE dispatcher: "extract embedded position snapshots for latency_model"
- Session config: `step_seconds: 1` (default), `latency_update_interval_seconds: 10`

**Proposed Fix:** Clarify in the plan that the OME emits a `ClockTick` event at every `step_seconds` interval (default: 1s). Each ClockTick carries a `TimelinePositionSnapshot` with positions for ALL nodes. VisibilityEvents are emitted only on state changes but are interleaved with the regular ClockTick stream. In the JSON Lines file, this means ~one record per second, giving the DE mode TO enough position data for latency computation at any interval. This matches the PRD's "Drive the time loop. Call propagator and visibility at each step" — every step produces output, not just steps where visibility changes.

---

## Issue 6 (MODERATE): ISL Terminal Scheduling Not Explicitly Planned

**Problem:** The Phase 1A exit criterion says: "4-node-test produces `visible=True, scheduled=False` events for terminal exhaustion." This requires ISL terminal scheduling logic: when a satellite has N feasible ISLs but only M terminals (M < N), the OME must choose the best M and mark the rest `scheduled=False`. The plan mentions this in exit criteria and test descriptions but doesn't specify which module or function implements it, or what the scheduling/ranking algorithm is.

**What the PRD says:** Section 10 Phase 1A: "The OME must schedule the best 2 links per satellite and emit the remaining feasible-but-unallocated links as `visible=True, scheduled=False`." The ISL neighbor assignment (Section 13.4) defines the priority order: intra-fwd > intra-aft > cross-right > cross-left.

**Proposed Fix:** Add ISL terminal scheduling explicitly to `ome/visibility.py`'s `compute_all_visibility()` function. After computing all pairwise visibility, the function runs a terminal allocation pass:
1. For each satellite, collect all feasible ISLs (LOS + range + tracking rate OK)
2. If feasible count exceeds terminal count, rank by the Section 13.4 priority order (intra-fwd > intra-aft > cross-right > cross-left)
3. Top M get `scheduled=True`, remaining get `visible=True, scheduled=False`
4. Ground link scheduling is separate (handled by `schedule_ground_links()` with highest-elevation or longest-pass policy)

Note that terminal allocation must be symmetric: if sat-A schedules a link to sat-B, sat-B must also schedule the reciprocal link to sat-A (consuming a terminal on each side).

---

## Issue 7 (MODERATE): OME Is Not a Python Package

**Problem:** The plan lists files like `ome/propagator.py` and `ome/event_stream.py` but the PRD repo structure doesn't show `ome/__init__.py`. Without it, `ome/` is just a directory, not a Python package. Internal imports like `from ome.propagator import propagate_keplerian` in `ome/event_stream.py` won't resolve.

This also affects how tests import OME code. `tests/unit/test_propagator.py` needs `from ome.propagator import propagate_keplerian` which requires `ome/` to be a package or on sys.path.

**Proposed Fix:** Add `ome/__init__.py` (empty) in Step 1 scaffolding alongside the other `__init__.py` files. The same pattern will apply to `orchestrator/`, `measurement/`, and `vs-api/` in later phases. For Phase 1A development, the OME runs via `python -m ome.main` from the repo root. The Dockerfile will set PYTHONPATH appropriately.

---

## Issue 8 (MODERATE): Walker-Star vs Walker-Delta Expansion Under-Specified

**Problem:** Step 8 says `expand_parametric()` handles "Walker-delta/star expansion" but doesn't specify how the two patterns actually differ in the expansion code. The polar-seam-demo constellation depends on Walker-star producing counter-rotating adjacent planes at 97.4° inclination, so getting this wrong means the polar seam demo doesn't work.

**What the PRD says:** The constellation config provides `raan_spacing_deg`, `phase_offset_deg`, and `pattern` ("walker-delta" or "walker-star") explicitly. For starlink-mini: 6 planes × 30° = 180° total RAAN spread. For polar-seam-demo: 4 planes with RAAN spacing that produces counter-rotating adjacent planes. The PRD says: "Walker-star: ascending node RAAN spacing produces counter-rotating adjacent planes."

**Proposed Fix:** Clarify in the plan: The RAAN and phase offset for each satellite are computed directly from the config values:
- `raan = plane_index * raan_spacing_deg`
- `true_anomaly = slot_index * (360 / sats_per_plane) + plane_index * phase_offset_deg`

The `pattern` field does NOT change the expansion formula — the counter-rotating geometry emerges naturally from the RAAN spacing + inclination (for polar-seam-demo: 4 planes × 90° = 360° RAAN spread at 97.4° inclination). The `pattern` field is metadata that:
1. Signals to `visibility.py` whether to evaluate polar seam tracking dynamics
2. May affect whether cross-plane neighbor assignment wraps at the plane boundary (Walker-star wraps; Walker-delta may not if RAAN spread < 360°)

The plan should add a test case verifying that polar-seam-demo's expansion produces planes with RAAN values that, combined with inclination, create counter-rotating geometry at polar latitudes.

---

## Issue 9 (MINOR): Test conftest.py Creation Timing

**Problem:** The plan mentions shared fixtures in `tests/conftest.py` at the end of Step 8 but doesn't specify when to create it. Fixtures like `zmq_context` are needed starting in Step 2 (test_event_serialization.py needs a ZeroMQ context), and config fixtures like `four_node_config` are needed in Step 3.

**Proposed Fix:** Create `tests/conftest.py` in Step 1 scaffolding with basic fixtures (path helpers, zmq_context). Expand it incrementally:
- Step 2: add zmq_context fixture
- Step 3: add config-loading fixtures (four_node_config, starlink_config, polar_seam_config, gs_config)
- Step 4: add addressing fixture
- Step 5: add tmp_db fixture
- Step 8: add sample_session fixture

---

## Issue 10 (MINOR): Duplicate Config/Fixture Files

**Problem:** The plan creates both `configs/constellations/4-node-test.yaml` (Step 8) and `tests/fixtures/4-node-test.yaml` (Step 3). Having two copies of the same data invites drift — if the config format changes, you have to update both files.

**Proposed Fix:** The `configs/` files are the single authoritative source for valid configurations. `tests/fixtures/` should contain ONLY test-specific fixture data that doesn't belong in configs/:
- Deliberately malformed configs for rejection/validation tests
- Small hand-crafted snapshots or event sequences
- Minimal configs that test edge cases

For tests that need valid configs, the conftest.py should define a `CONFIGS_DIR` path constant pointing to `configs/` and load from there directly. Remove the duplicate valid YAML files from `tests/fixtures/`.

---

## Summary Table

| # | Severity | Issue | Key Decision |
|---|----------|-------|-------------|
| 1 | CRITICAL | pyyaml in shared lib | Remove from lib deps; YAML loading is component code |
| 2 | CRITICAL | ISL neighbor assignment location | Move to addressing.py in shared lib |
| 3 | CRITICAL | build_template_vars too large | Delegate to AddressingScheme + addressing helpers |
| 4 | CRITICAL | No OME entry point | Add ome/main.py + ome/__init__.py |
| 5 | MODERATE | Snapshot/ClockTick frequency | Emit ClockTick with positions every step_seconds |
| 6 | MODERATE | ISL terminal scheduling unplanned | Add scheduling pass to compute_all_visibility() |
| 7 | MODERATE | OME not a Python package | Add ome/__init__.py in scaffolding |
| 8 | MODERATE | Walker-star expansion vague | Same formula; pattern controls visibility behavior |
| 9 | MINOR | conftest.py timing | Create in Step 1, expand incrementally |
| 10 | MINOR | Duplicate fixture files | Load test configs from configs/ directly |
