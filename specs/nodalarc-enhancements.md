# NodalArc Enhancement and Technical Debt Register

**Last updated:** 2026-04-25

Items are tracked by category and priority. Each item has a trigger condition
that defines when it must be addressed — not an arbitrary deferral.

---

## Security Enhancements

### SEC-001: Management VRF for cni0 isolation
**Priority:** P2 — address before multi-tenant hosting
**Current state:** cni0 (K8s CNI interface) is in the default VRF. Visible in
`show interface brief` with kernel routes (10.42.0.0/16, 10.42.0.0/24). Egress
blocked by iptables OUTPUT DROP but routes are visible to users.
**Proper fix:** Move cni0 into a management VRF at the kernel level. FRR in the
default VRF would not see cni0 at all. OpenSSH `sshd` must start in the mgmt VRF
context (`ip vrf exec mgmt /usr/sbin/sshd -D ...`, or equivalent systemd/entrypoint
wiring). Industry standard: Cisco mgmt VRF, Juniper mgmt_junos.
**Blocker:** OpenSSH needs VRF-aware binding (either a second sshd instance bound
to a mgmt-VRF listener, or `BindAddress` + `ip vrf exec` wrapping at launch).
SSH through ISL/gnd (in-band) must stay in default VRF — cannot move sshd entirely
to mgmt VRF without breaking the in-band SSH use case. Needs architectural design
for dual-VRF SSH listening (likely two sshd instances with distinct ListenAddress
directives, one per VRF).
**Trigger:** Multi-tenant hosting or when users report confusion about cni0 routes.

### SEC-002: cni0 kernel routes visible in routing table
**Priority:** P3 — cosmetic, blocked by iptables
**Current state:** `K>* 10.42.0.0/16 via cni0` and `C>* 10.42.0.0/24 connected cni0`
visible in `show ip route`. Non-functional (iptables blocks egress) but confusing.
**Fix:** Investigate removing kernel/connected routes for cni0 subnet without
breaking SSH inbound. The pod IP address must remain on the interface for SSH
to work, but the subnet routes may be removable.
**Trigger:** User feedback or SEC-001 (management VRF eliminates this entirely).

### SEC-003: FRR requires SYS_ADMIN capability
**Priority:** P3 — monitor FRR releases
**Current state:** FRR's ospfd and mgmtd call `privs_init()` which explicitly
requests `cap_sys_admin`. Verified empirically: removing SYS_ADMIN causes
`privs_init: initial cap_set_proc failed: Operation not permitted`.
**Mitigation:** vtysh login shell (no shell escape), read-only root, iptables
egress block on cni0.
**Fix:** Revisit when FRR provides compile-time or runtime capability flags, or
when we build FRR from source with modified privs_init.
**Trigger:** FRR 11.x release or when building custom FRR image.

### SEC-004: bash exists in FRR container
**Priority:** P3 — mitigated by vtysh login shell
**Current state:** FRR's `docker-start` and `frrcommon.sh` are bash scripts.
Removing `/bin/bash` breaks FRR startup (`docker-start: not found`). Verified
empirically.
**Mitigation:** SSH login shell is `/usr/bin/vtysh`. vtysh `terminal shell`
disabled. No path from SSH session to bash.
**Fix:** Rewrite entrypoint to not use FRR's docker-start (call watchfrr directly),
or wait for FRR to provide a non-bash startup path.
**Trigger:** When building custom FRR image or when vendor NOS images (cRPD, cEOS)
are integrated (they don't use bash docker-start).

---

## Scaling Enhancements

### SCALE-001: VF rendering architecture + visualization feature parity — PARTIALLY COMPLETE
**Priority:** P1 — rendering breaks at 220+ satellites, blocks demo capability
**Status:** Milestones 1-6 implemented in nodalarc-0.2.2 (42 commits).

**Completed (2026-04-25):**
- InstancedMesh for satellites (440 draw calls → 2)
- Web Worker SGP4 with double-buffered SharedArrayBuffer
- Position lookup API (zero-allocation, all consumers migrated)
- Per-link Line2 with shared materials (fail-flash preserved)
- GPU picker with InstancedMesh intersection + touch support
- DOM-based satellite labels with ray-sphere earth occlusion
- Design token system (120+ tokens, CSS + Three.js unified)
- Layout shell with responsive tablet breakpoints
- Filter panel (per-orbital-plane toggles, link/overlay controls)
- Political boundaries (Natural Earth 110m, 3 globe modes)
- Toast notifications (auto-dismissing, categorized)
- Dashboard metrics panel
- GS labels with occlusion and distance fade
- Label toggles: semicolon=sat, apostrophe=GS
- Build hash in bottom bar for deployment verification
- 149 automated tests

**Not completed / deferred:**
- Custom RawShaderMaterial for links (A6) — deferred, batched
  Line2 interleaving cost is 84µs at 440 links, not a bottleneck.
  Revisit at >2000 links.
- troika-three-text SDF labels — didn't render with Three.js 0.172,
  replaced with DOM labels. Revisit at >1000 satellites where DOM
  label count becomes a performance concern.
- Split terminal (2-pane side-by-side vtysh)
- Topology mini-graph in detail panel (UX-010)
- Day/night terminator already existed, not changed
- Production theme palette replacement (tokens are placeholder)

**Original problem description:**
The VF creates one `THREE.Mesh` + one `THREE.Sprite`
per satellite and one `Line2` per link. At 220 sats + 448 links =
~1,100 draw calls per frame. The JavaScript main thread updates every
object's position every frame. Performance degrades visibly above
200 satellites. Reference: satellitemap.space renders 30,000+
satellites at interactive framerates using GPU instancing.
**Root cause:** Per-object rendering trades scalability for dev speed.
Selection, hover, color changes are trivial with individual meshes but
the approach doesn't scale.

**Fix — Rendering Architecture (Phase 1):**
1. `THREE.InstancedMesh` for satellites. One geometry, one material,
   one draw call. Update positions via `instanceMatrix` typed array.
   `instanceColor` buffer for per-satellite coloring. Built-in
   `InstancedMesh.raycast()` for selection.
2. `THREE.LineSegments` with shared `BufferGeometry` for all links.
   One draw call for all ISL + ground links. Position buffer updated
   per frame. Color attribute for link type differentiation.
3. Web Worker for SGP4 propagation. Positions computed off main
   thread, transferred via `SharedArrayBuffer` or `postMessage`.
4. Satellite labels via SDF text or `CSS2DRenderer` with distance-
   based scaling. Labels shrink/hide at global zoom, grow at regional.
5. LOD: point sprites at global view, instanced spheres at regional.

**Fix — Globe and Earth Rendering (Phase 2):**
1. Political boundaries — country borders, state/province lines from
   Natural Earth vector data rendered as line geometry on the globe.
   Country/region labels at appropriate zoom levels.
2. Day/night terminator with lit/unlit earth textures.
3. Multiple earth styles: blue marble (current), political map, or
   OpenStreetMap tiles at ground-level zoom.

**Fix — Augment Existing Visualization (Phase 3):**

We already have: satellite selection + raycaster, orbital trails,
coverage footprints, ground tracks, satellite/GS/link detail panels,
event log with filtering, network summary, time controls with
play/pause/seek/speed, interactive SSH terminal, topology view, and
trace dialog. None of these need to be rebuilt.

Augmentations to existing features:
1. Multi-select — extend existing `selection.ts` with a stacking
   widget (click multiple sats, each with zoom/info/deselect).
   Current: single-select. Needed: multi-select with side panel.
2. Legend/filter panel — colorize by orbital plane, link type, or
   scheduling state. Click colors to show/hide. Augments existing
   `EventFilter.tsx` pattern.
3. POV camera mode — ride along with a selected satellite (forward
   or nadir view). New camera mode, hooks into existing selection.
4. Ground-level camera — zoom into a GS location with terrain
   detail. Augments existing zoom controls.
5. Search — fuzzy search across all satellites and ground stations
   with quick-select. New toolbar feature.
6. Deep-linking — URL encodes current view/selection for sharing.
7. Configurable toolbar — user picks which bottom bar controls
   to display. Augments existing `Toolbar.tsx`.

Features we have that they do NOT:
- Interactive SSH terminal into any satellite/GS node
- Live routing protocol state (show isis neighbor, show ip route)
- Topology view with link-level detail
- Event log with protocol convergence events
- Trace dialog for path analysis
- Session wizard for constellation design
- Full network emulation (IS-IS, OSPF, BFD, MBB handovers)

**Fix — Scale Beyond LEO (Phase 4, cislunar):**
1. Zoom range from ground-level through LEO, MEO, GEO, to cislunar.
   Clarke belt ring visualization at GEO altitude.
2. Moon rendering with correct orbital position and bump-mapped
   surface.
3. Lagrange point markers (L1-L5).
4. Support for cislunar vehicle ephemeris (Artemis, JWST-class
   trajectories).
5. Multi-body reference frame support in the OME (already in the
   GroundSegment schema via `reference_body` field).

**What satellitemap.space does NOT have (our advantages):**
- No routing/forwarding plane — pure visualization, zero emulation
- No interactive terminal access (SSH to satellite nodes)
- No protocol simulation (IS-IS, OSPF, BGP, SR-MPLS)
- No link-level emulation (latency, bandwidth, packet loss, tc netem)
- No constellation design tools — only displays real TLE data
- No what-if scenarios — cannot modify orbital parameters
- No ground segment networking emulation
- No MBB handover emulation
- No NodalPath centralized PCE

**Trigger:** Immediate — rendering performance blocks demo of the
220-satellite realistic constellation.
**Competitive reference:** satellitemap.space (WebGL via TWGL.js,
30K+ objects, SGP4 via satellite.js, GPU-accelerated).

### SCALE-002: Operator template generation is single-threaded
**Priority:** P1 — blocks scale testing above ~500 satellites
**Current state:** `build_template_vars()` in the Operator generates
FRR configs (zebra.conf, isisd.conf, etc.) for every node sequentially.
At 220 nodes this is sub-second. At 1,648 nodes (Starlink Shell 1 full
geometry) it takes 5+ minutes of single-core CPU. The operator is stuck
at "Building template variables for 1648 nodes" while consuming 250m CPU.
**Root cause:** Jinja2 template rendering is called once per node in a
sequential loop. ISL neighbor computation may also be O(n²) in some
paths (checking all nodes for adjacency).
**Fix options:**
1. Parallelize with `concurrent.futures.ProcessPoolExecutor` — each node's
   template vars are independent of other nodes
2. Pre-compute the ISL neighbor map once (O(n)) then look up per node (O(1))
3. Cache compiled Jinja2 templates (they're re-parsed from file on each call)
4. Generate ConfigMaps in bulk via K8s batch API instead of per-pod
**Impact:** Session startup time at 1,000+ nodes. Does not affect
runtime performance (template generation is one-time at session start).
**Trigger:** Immediate — discovered during starlink-gen2-scale test
(1,584 sats + 64 GS = 1,648 nodes) on 2026-04-25.

### SCALE-003: VF render loop thrashes CPU when no session data
**Priority:** P1 — makes the user's system unusable during deploy
**Current state:** When the VF is open but no session is active (or the
session is still deploying), the render loop + Web Worker + simClock
continue running at 60fps processing zero satellites. The combination
of an empty InstancedMesh render, troika label updates on empty maps,
and the Worker trying to propagate zero nodes consumes significant
CPU and makes the user's system slow.
**Root cause:** The animation loop doesn't short-circuit when there's
no session data. `animateSatellites`, `animateLinks`, `animateLabels`
all iterate empty maps every frame — individually cheap but combined
with the Three.js render call and Worker message overhead, it adds up.
**Fix:** Add a session-active guard at the top of the animation loop.
If `snapshot === null || snapshot.nodes.length === 0`, skip all animate
calls and render a static globe at reduced framerate (e.g., 5fps for
camera orbit only). Resume 60fps when data arrives.
**Trigger:** Discovered during starlink-gen2-scale deploy — user
reported system-wide slowdown with VF open and no session data.

### SCALE-004: Sub-second step granularity
**Priority:** P3 — required for microsecond-precision measurements
**Current state:** `step_seconds: 1` (configurable). Link events quantized to
1-second boundaries — up to 1s timing error per event. At BFD intervals of
100ms, this is 10x the protocol timer.
**Fix:** Analytical event-time computation — solve for exact moment a visibility
constraint is crossed rather than detecting by sampling. Eliminates step
quantization entirely.
**Trigger:** BFD integration, sub-second convergence measurement requirements.

### SCALE-005: Remove vestigial `ground_bridges` from wiring manifest
**Priority:** P2 — dead code cleanup (blocking issue resolved by gzip)
**Current state:** `ground_bridges` satellite lists removed (empty dicts now).
The ConfigMap size issue is resolved by gzip compression (SCALE-006). However,
`ground_bridges` itself is still vestigial — the Node Agent iterates it only
for GS node IDs, which it could derive from `nodes` where
`node_type == "ground_station"`.
**History:** `ground_bridges` was introduced in `6e78c55` (2026-03-24) as a
forward-looking structure for ground-link wiring. The satellite lists were never
consumed — the Scheduler drives ground links dynamically via LinkUp/LinkDown.
Commit `a3f9d3d` (2026-04-23) moved the last actual data read (GS interface
list) from `ground_bridges` to `nodes`, leaving `ground_bridges` fully vestigial.
**Fix:** Remove `ground_bridges` from `session_deployer.py` (don't build it,
don't serialize it). In `wiring.py`, derive the GS set from
`nodes` where `node_type == "ground_station"`. Two files, ~10 lines changed.
**Trigger:** Next code cleanup pass.

### SCALE-006: Gzip-compressed wiring manifest — RESOLVED
**Priority:** P1 — blocks scale testing above ~1,000 satellites
**Resolved:** 2026-04-25. Wiring manifest is now gzip + base64 encoded in the
ConfigMap (`manifest.json.gz.b64` key). Node Agent detects the compressed key
and decompresses transparently, with fallback to uncompressed `manifest.json`
for backward compatibility.
**Root cause:** Kubernetes ConfigMaps have a hard 1MB limit. At 1,626 nodes
the per-node specs alone are ~1.07MB (675 bytes × 1,584 satellites). Removing
the vestigial satellite lists (SCALE-005) saved ~800KB but wasn't enough —
the node specs themselves exceeded the limit.
**Fix:** gzip compression on the JSON manifest. Typical 10-15x compression on
repetitive JSON. A 1.07MB manifest compresses to ~70KB. Scales to 5,000+
nodes without hitting the ConfigMap limit.
**Files changed:** `session_deployer.py` (writer), `node_agent/__main__.py`
(reader).

---

### SCALE-007: OME compute parallelization for large constellations
**Priority:** P1 — blocks realtime simulation above ~1,500 satellites
**Current state:** The OME pacing thread is single-threaded by design (async
sleep causes satellite motion jitter, per development contract §3.3). At 1,584 satellites
+ 42 ground stations, compute time is p50=600ms, p95=1,100ms against a
1,000ms budget (1-second step at 1x realtime). The OME exceeds its tick budget
at p95, meaning ~5% of ticks run slower than realtime. At 3,000+ satellites
the OME cannot maintain realtime at any percentile.
**Measured data (2026-04-25 scale test):**
- 1,626 nodes, 3,372 active links (3,168 ISL + ~204 GS)
- OME process: 82-89% of one CPU core, 143 MB RSS, 8 threads
- Compute p50: 537-624ms, p95: 887-1,119ms
- Headroom: -12% to +11% (frequently negative at p95)
- Full data: `specs/scaling-performance-2026-04-25.md`
**Root cause:** Per-tick computation is O(n) for satellite propagation +
O(n x m) for GS visibility (n=satellites, m=ground stations). The O(n x m)
GS visibility term dominates: 1,584 x 42 = 66,528 elevation/azimuth checks
per tick. All computed sequentially in one thread.
**Fix options (complementary, not exclusive):**
1. **ProcessPoolExecutor within each tick** — satellite propagation is
   embarrassingly parallel (each sat independent). GS visibility can be
   partitioned by GS. ISL visibility can be partitioned by plane. Must use
   processes (not threads) because the math is CPU-bound and GIL-limited.
   The pacing thread stays synchronous — it dispatches work to the pool and
   collects results within the same tick.
2. **Spatial indexing for GS visibility** — replace brute-force O(n x m)
   with spatial partitioning. At any given time, only ~5-15 satellites are
   visible from any one GS. A sub-satellite-point grid or kd-tree would
   reduce visibility checks from 66,528 to ~2,000-5,000 per tick.
3. **Vectorized numpy propagation** — replace per-satellite Python loop
   with batched numpy operations for orbital mechanics. SGP4 has numpy
   bindings (`sgp4.api` batch mode).
**Scaling projections:**
- 576 sats: ~200-300ms/tick (comfortable)
- 1,584 sats: ~600-1,100ms/tick (marginal)
- 3,168 sats: ~2,000-4,000ms/tick (cannot maintain realtime)
- 5,000 sats: ~5,000-8,000ms/tick (far from realtime)
**Trigger:** Immediate — discovered during starlink-gen2-scale test on
2026-04-25. Any constellation above ~1,500 satellites cannot maintain
1-second realtime pacing.

### SCALE-008: Ground station pod placement imbalance
**Priority:** P2 — contributes to node01 overload at scale
**Current state:** `planePerNode` placement policy puts all ground station
pods on `available_nodes[0]` (node01). At 42 GS pods, this adds ~4.2 cores
of FRR load plus 42 x 50MB = ~2.1GB memory to node01, which already carries
all platform services (OME, Scheduler, VS-API, Operator, NATS, VF, NodalPath).
**Measured (2026-04-25):** node01 hit 100% CPU, 90% memory (28.6/32 GB),
load average 1,727. k3s API server became unresponsive, containerd socket
refused connections, node went NotReady. Required manual k3s restart.
**Fix:** Distribute GS pods across nodes. Options:
1. Round-robin GS pods across available nodes (same as satellites)
2. Dedicated GS node assignment (configurable in PlacementConfig)
3. Weight-aware placement — account for platform service overhead on node01
**Files:** `session_deployer.py` `compute_pod_placement()` lines 77-81.
**Trigger:** Any scale test above ~30 ground stations on a 4-node cluster.

---

## Architecture Enhancements

### ARCH-000: Operator on_create handler must be idempotent (reconciler pattern)
**Priority:** P1 — causes data loss on operator restart during session deploy
**Current state:** The `on_create` handler in `handlers.py` is a linear
sequential function: build templates → create ConfigMaps → create pods →
signal FRR → write wiring manifest → wait for wiring. If the operator
restarts at any point, kopf fires `on_resume` which only handles "Wiring"
and "Ready" phases. For "Creating" or "Pending" phases, it falls through
to `on_create` which detects existing session pods and deletes them all
before starting over — destroying a potentially complete deployment.

**Partial fix in place (2026-04-25):** `on_resume` now handles "Creating"
phase when all pods are already running — continues from FRR signaling
through wiring. This covers the common case (operator restart after pod
creation is complete).

**Root cause:** `on_create` is not idempotent. It assumes a blank slate
and fails destructively when session pods already exist. The Kubernetes
reconciler pattern requires: compare desired state (from the CRD spec)
against actual state (existing pods, ConfigMaps), then converge the
difference. The handler should create only MISSING pods, skip already-
signaled FRR configs, and only write the wiring manifest if it doesn't
exist.

**What this would fix:**
- Operator restart during template building → resume template building
- Operator restart during pod creation → create only missing pods
- Operator restart during FRR signaling → re-signal (idempotent)
- Operator restart during wiring → already handled by wiring_check timer
- `make deploy-operator` during an active session → no disruption

**Impact:** `services/nodalarc_operator/handlers.py` and
`services/nodalarc_operator/session_deployer.py`. Major refactor of
the deploy flow from linear to reconciliation-based.
**Trigger:** Any operator restart during session deployment at scale.
Discovered during starlink-gen2-scale test (1,648 nodes) on 2026-04-25.

### ARCH-001: Scenario handler should be async callback, not daemon thread
**Priority:** P2 — prevents future concurrency bugs
**Current state:** The scenario handler runs as a daemon thread sharing
`_active_links` with the Dispatcher's asyncio event loop. `asyncio.Lock`
doesn't protect against thread access. CPython GIL makes individual dict ops
atomic but compound operations could theoretically race.
**Fix:** Move scenario handler into the Dispatcher's event loop as an async NATS
callback — same pattern as `_on_visibility`, `_on_snapshot`, `_on_clock_tick`.
Eliminates all cross-thread state sharing.
**Impact:** Contained to `services/scheduler/scenario_handler.py` and
`services/scheduler/__main__.py`.
**Trigger:** Next scenario handler feature addition or any concurrency bug report.

### ARCH-002: Node Agent partial failure recovery
**Priority:** P2 — operational reliability
**Current state:** Multi-step kernel operations (create veth + move to namespace +
rename + tc mirred) have no rollback on partial failure. If step 3 of 5 fails,
steps 1-2 are orphaned. The Scheduler thinks the link is down, Node Agent has
partial kernel state.
**Fix:** Either transactional wrappers with rollback, or a periodic reconciliation
loop that verifies kernel state matches desired state and corrects divergences.
**Trigger:** Any report of phantom interfaces or stuck link state after failures.

### ARCH-003: Scheduler crash recovery (checkpoint not read)
**Priority:** P2 — operational reliability
**Current state:** Scheduler writes sim_time checkpoint to ConfigMap after every
event batch. `read_checkpoint()` is defined but never called. After crash,
`_active_links` starts empty. Node Agent kernel state may have links up that
the Scheduler doesn't know about.
**Mitigation:** LinkStateSnapshot (MaxMsgsPerSubject=1 in JetStream) provides
authoritative state on reconnection. But there's a window between Scheduler
restart and first snapshot where state diverges.
**Fix:** Read checkpoint on startup, apply retained LinkStateSnapshot from
JetStream, reconcile with Node Agent.
**Trigger:** Any Scheduler crash in production, or before multi-tenant hosting.

### ARCH-004: Real-time stepped emission look-ahead integration with NodalPath
**Priority:** P1 — required for NodalPath live mode
**Current state:** The look-ahead thread runs `precompute_timeline_window()` in
background and stores results. NodalPath's LiveOrchestrator subscribes to
real-time VisibilityEvents but has no access to look-ahead data for proactive
scheduling.
**Fix:** Publish look-ahead VisibilityEvents on a separate NATS subject
(`nodalarc.ome.lookahead` or similar) that NodalPath can consume for advance
notice of link transitions.
**Trigger:** NodalPath live mode activation.

### ARCH-010: R-OME-004b GroundSegment unified abstraction + UT support
**Priority:** P2 — architectural foundation for UT-scale emulation
**Current state:** `GroundSegment` base class exists with `tenant_id`,
`reference_body`, `mobility`, `service_priority`, `hysteresis`.
`GroundStationConfig` inherits from it. The schema direction is set.
**What's not done:**
1. No `UserTerminal` specialization class. UT implementation model is
   OPEN per PRD (pod-per-UT / netns-in-aggregator / logical-only).
2. The OME's bipartite allocation (`compute_step`) only processes
   ground stations. UTs are not in the scheduling loop. When UTs land,
   the fold must process both GSes and UTs in a single walk against
   the unified `GroundSegment` collection.
3. No bulk UT import mechanism (GSes are individually curated; UTs
   should be generated or imported in bulk).
4. No UT-specific scheduling policy (beam_count=1 for typical
   consumer terminal, vs multi-beam gateway).
**Depends on:** UT implementation model decision (PRD R-OME-004b).
NMTS alignment (ARCH-005) should happen in the same pass.
**Trigger:** First scenario requiring UT emulation, or when the
R-OME-004 family amendment lands in v0.75+.
**PRD reference:** `nodalarc-prd-v74.md` R-OME-004b,
`nodalarc-gs-scheduling-hysteresis.md` §7.4.

### ARCH-005: NMTS data model alignment
**Priority:** P2 — architectural direction; binds the R-OME-004b
GroundSegment refactor
**Current state:** NodalArc's data model (`lib/nodalarc/models/`) was
built independently of industry schemas. `GroundStationConfig`,
`SatelliteNode`, `TerminalConfig`, `TerrestrialPrefix`, etc. are
bespoke Pydantic models without cross-references to external
standards. The model works; it also does not interoperate with other
non-terrestrial networking systems without a translation layer.
**Direction:** The Outernet-Council NMTS (Network Model for
Temporospatial Systems) — `github.com/outernetcouncil/nmts` — is the
design target for the GroundSegment refactor and eventually the full
NodalArc data model. NMTS provides protobuf schemas for
`PLATFORM_DEFINITION`, `NETWORK_NODE`, `Motion` (including
selenographic coordinates for cislunar), `ANTENNA_PATTERN`,
`BAND_PROFILE`, `INTERFERENCE_CONSTRAINT`, `SURFACE_REGION`,
`STATION_SET`, `DEVICES_IN_REGION`, and `COMPUTED_MOTION`. It is
Apache-2.0 licensed, consumed natively by Aalyria Spacetime, and
positioned by the Outernet Council as an industry standard.
**Commitment level:** Soft — "lean that direction barring major
revelation" (user guidance, 2026-04-21). The GroundSegment refactor's
schema choices default to NMTS vocabulary. Divergences from NMTS
require documented justification.
**Gaps to verify:** Our parametric Walker-constellation helpers have
no NMTS equivalent (NMTS expects per-platform orbital elements);
NMTS does not yet model `tenant_id` scoping (ARCH-007 will add this
as an extension); NMTS has not been proven as a real-time
query-substrate at constellation scale — that is an open research
question Aalyria has not published results on.
**Impact:** Covers lib/nodalarc/models/* rewrites, VS-API schemas,
configs/*, session YAML shape. Large surface; incremental migration
possible (start with GroundSegment, expand outward).
**Trigger:** Start of the R-OME-004b GroundSegment implementation
(scheduled for v0.75 PRD amendment or later).

### ARCH-006: Capacity-aware link modeling (BandProfile / AdaptiveRateTable)
**Priority:** P3 — post-MVP; enables predicted-throughput-based
scheduling and measurement
**Current state:** Link capacity is a fixed attribute
(`bandwidth_mbps`) on each terminal definition. Actual capacity does
not vary with distance, elevation angle, antenna gain, atmospheric
loss, or spectral conditions. In reality, a satellite link at the
edge of a beam footprint has dramatically lower throughput than one
at beam center, and rain attenuation on Ku-band ground links at low
elevation is significant — but these effects are not modeled.
**Direction:** Adopt the Spacetime / NMTS BandProfile +
AdaptiveRateTable model. Each BandProfile groups channels with
similar propagation characteristics; each AdaptiveRateTable maps
received signal power (C/(N+I) in dB) to achievable data rate. This
requires:
1. Antenna pattern model on each terminal (3D radiation pattern,
   aligned with NMTS `ANTENNA_PATTERN` — Gaussian, Parabolic,
   Helical, Isotropic, or custom phi/theta/gain tables)
2. Path loss model (free-space Friis, atmospheric attenuation per
   ITU-R P.676, rain fade for Ku/Ka bands per ITU-R P.618)
3. Band profile per link type with adaptive rate tables
4. Scheduling score extension to accept capacity-aware policies
   (see `specs/nodalarc-gs-scheduling-hysteresis.md` §5.2 and Q13)
**What this enables:**
- Realistic throughput measurements under partial-beam conditions
- Hysteresis scoring by predicted throughput, not just geometry
  (meaningful for UT handoff decisions where link quality matters
  more than elevation per se)
- Weather integration — rain fade predictions drive routing
  decisions (Spacetime demonstrated this with Telesat using Canadian
  Space Agency data)
**Trigger:** Research or demo scenarios that require realistic link
quality modeling; customer request for weather-aware routing
experiments; UT-scale tests where per-UT throughput dispersion
matters.
**Does not block:** MVP scheduling works with fixed capacity. The
design-time cost is keeping the `SchedulingPolicy` interface abstract
enough that a capacity-weighted policy can be plugged in later
without refactor.

### ARCH-007: Multi-tenant SaaS architecture
**Priority:** P2 — architectural direction; affects every
tenant-scopeable entity from day one
**Current state:** Platform assumes single-operator deployment. Every
entity (GroundSegment, Satellite, Constellation, Session, LinkState,
SchedulingPolicy) implicitly belongs to one operator. No tenant
scoping attribute on any entity. No RBAC partitioning. No quota /
resource-isolation model.
**Direction:** Evolve toward multi-tenant SaaS (inspired by
Aalyria's Spacetime as a SaaS model). The hard rule landing in v0.74
(R-OME-004c, R-NET family) is: every tenant-scopeable entity carries
a `tenant_id` / `operator_id` attribute **from day one**, defaulted
to a well-known single-tenant constant in MVP. This avoids
retrofitting tenant scope into a data model later — retrofitting is
a full-stack migration (schema, queries, RBAC, UIs, every stored
artifact) that is expensive after single-tenant assumptions harden.
**What multi-tenant needs beyond the attribute:**
- Kubernetes namespace-per-tenant OR label-based partitioning of a
  shared namespace (decision pending — see
  `specs/nodalarc-gs-scheduling-hysteresis.md` Q14)
- NATS JetStream subject / consumer partitioning per tenant
- VS-API authentication/authorization with tenant scope
- Session YAML / ConstellationSpec CRD accepts multiple concurrent
  sessions (today it's a singleton `current-session`)
- Federation API between tenants (see below)
- Quota and resource-isolation enforcement
- Tenant lifecycle (create, suspend, delete, migrate)
**Federation dimension:** inter-tenant resource brokering — a
customer requesting capacity across another operator's constellation
during a weather outage, a cislunar scenario where Earth-operator and
Luna-operator are different organizations. Aalyria's Federation API
(RFC 9834 Attachment Circuit as a Service) defines the industry
pattern: providers advertise time-dynamic reachability, latency
/throughput SLA attributes, and cost metrics; requesters invoke
`InterconnectService` to obtain cross-tenant paths. Our federation
implementation can adopt this shape directly or design our own.
**Trigger:** Second external operator deployment; deliberate
SaaS-product pivot; cislunar real-world test (which is almost
certainly multi-operator in reality). The architectural-hygiene
piece — `tenant_id` attributes on every entity from day one — does
not wait for a trigger; it lands in the R-OME-004b GroundSegment
implementation unconditionally.

### ARCH-008: DTN / Bundle Protocol integration
**Priority:** P2 — planned forwarding paradigm alongside SR-MPLS
and IP
**Current state:** NodalArc and NodalPath assume continuous IP
connectivity. SR-MPLS paths are installed proactively via NodalPath;
IP paths come from FRR's IGP. DTN (Delay-Tolerant Networking) is
not modeled. There is no Bundle Protocol (BPv7) agent, no
Contact-Graph-Routing engine, no store-and-forward buffering on
pods.
**Direction:** Add DTN as a second forwarding paradigm. Relevant
scenarios:
- **Cislunar intermittent links** — deep-space probes with
  contact-window-driven connectivity; lunar surface-to-Gateway
  links blocked by lunar rotation; cislunar relay satellites with
  scheduled contact windows rather than continuous coverage
- **Contested environments** — military / defense scenarios where
  continuous IP is denied or degraded; store-and-forward is the
  only viable delivery model
- **Deep-space science missions** — Mars probes, outer-solar-system
  missions; RTTs measured in minutes to hours; BPv7 is the standard
**Relevant standards:**
- RFC 9171 (Bundle Protocol v7)
- `draft-taylor-dtn-btpu` (Bundle Transfer Protocol - Unidirectional,
  for hardware-speed implementation over unreliable link-layer
  protocols without IP services; authored by Rick Taylor at
  Aalyria)
- `draft-taylor-dtn-dpp` (DTN Peering Protocol, inter-domain routing
  with scheduled contact windows for deep-space networks)
- `draft-ek-dtn-ipn-arpa` (DNS-based discovery for DTN, authored by
  Erik Kline at Aalyria)
- `draft-ek-dtn-ethernet` (BP datagrams over Ethernet)
**Architectural positioning:** DTN and SR-MPLS are **complementary,
not alternatives**. A constellation deployment can run SR-MPLS in
the well-connected cislunar core (e.g., L1-halo-relay backbone) AND
DTN at intermittent edges (science-probe-to-relay links, denied
environments). NodalPath's proactive-install core is the right
foundation for both: SR-MPLS SIDs and label stacks for the
well-connected case; Bundle Protocol agents and scheduled contact
windows for the intermittent case. See
`specs/nodalarc-gs-scheduling-hysteresis.md` §9.4 for the cislunar
positioning and `nodalpath-prd-v07.md` §3.9 for the architectural
sketch.
**Trigger:** First cislunar real-world test (scheduled for after
NodalPath MVP stabilizes); defense-customer requirement for
contested-environment modeling; any experiment involving
intermittent-connectivity topology.

### ARCH-009: TVR WG standards alignment evaluation
**Priority:** P3 — evaluation-pending; decision deferred
**Current state:** NodalPath's AlmanacEvent, VisibilityEvent, and
LinkStateSnapshot schemas are independently designed. They work and
are in production on the current cluster. The IETF TVR (Time-Variant
Routing) working group is producing standards-track data models for
time-variable routing properties, with Aalyria actively
participating (R. Taylor co-authored RFC 9657 "Time-Variant Routing
Use Cases"; the WG charter explicitly targets non-terrestrial
networks). Relevant drafts:
- `draft-ietf-tvr-requirements` — TVR information model requirements
- `draft-li-arch-sat` — "A Routing Architecture for Satellite
  Networks" by Tony Li; proposes IS-IS with scheduled link
  connectivity changes
- `draft-hou-tvr-satellite-network-usecases`
- `draft-li-istn-addressing-requirement`
- `draft-king-tvr-ntn-challanges`
- `draft-zw-tvr-igp-extensions` — IS-IS/OSPF extensions for TVR
**Question:** should NodalPath's event schemas align with TVR WG
data models?
**Arguments for alignment:** standards-track interoperability;
TVR builds on NMTS foundations (ARCH-005), so NMTS adoption
naturally positions us for TVR; emerging industry vocabulary for
exactly the problem we are solving.
**Arguments against:** drafts are still in flight (not RFC yet);
alignment with a moving target costs migration effort; existing
event schemas work and are deployed; downstream consumers
(Scheduler, VS-API, NodalPath) would need updates to match any
schema migration.
**Action required:** read the current TVR drafts end-to-end;
compare to our existing event schemas at field level; identify
concrete divergences; assess migration cost vs alignment value.
This is a pure evaluation pass; no implementation commitment until
the evaluation is in.
**Trigger:** Before any PRD-level commitment to AlmanacEvent schema
(i.e., before the R-OME-004 family amendment that lands the full
scheduling/hysteresis design). If the alignment evaluation shows
strong value and low migration cost, land it alongside the
amendment. If value is low or cost is high, document the divergence
reason and move on.

---

## Diagnostic Tool Enhancements

### DIAG-001: traceroute needs CAP_NET_RAW for operator user — RESOLVED
**Priority:** P1 — affects core network engineering workflow
**Current state:** FRR's vtysh `traceroute` command execs busybox traceroute as
the `operator` user (uid 1000). The operator user has zero effective capabilities
(CapEff: 0x0). Busybox traceroute requires CAP_NET_RAW to create raw ICMP sockets.
Result: `traceroute: socket(AF_INET,3,1): Operation not permitted`.
**Root cause:** Linux capabilities on the container (NET_RAW in pod security
context) don't transfer to non-root users unless set on the binary via setcap
or via ambient capabilities. The FRR daemons handle this via `privs_init()` but
vtysh-exec'd system tools don't benefit from FRR's privilege mechanism.
**Rejected approach:** Installing standalone traceroute with setcap broke vtysh's
command parser — vtysh's `traceroute WORD` definition only passes the destination
to `execute_command()`, and the standalone binary has different argument handling
than busybox. A busybox copy with setcap is viable (busybox determines applet from
argv[0]) but adds complexity.
**Viable fix:** Copy `/bin/busybox` to `/usr/local/bin/busybox-netraw`, setcap
`cap_net_raw+ep` on the copy, symlink `/usr/bin/traceroute` → the copy. Busybox
runs the traceroute applet from argv[0]. Original `/bin/busybox` untouched.
ping does not need this fix (busybox ping uses UDP DGRAM sockets).
**Trigger:** Before any demo or user testing involving traceroute.

### DIAG-002: vtysh traceroute/ping don't pass flags to system binary
**Priority:** P2 — limits diagnostic capability
**Current state:** FRR vtysh's `traceroute` command definition (FRR source:
`vtysh.c`) is `"traceroute WORD"` which passes exactly one argument (the
destination) to `execute_command("traceroute", 1, argv[idx]->arg, NULL)`. No
flags are accepted. `traceroute -n 10.0.1.1` returns "Unknown command."
Same limitation applies to `ping` — vtysh wraps it with limited syntax.
**Impact:** Users cannot use `-n` (no DNS), `-m` (max TTL), `-w` (timeout),
`-s` (source IP), or any other standard traceroute/ping flags. The `--help`
output is misleading because it shows busybox's help (which lists all flags)
but vtysh rejects them.
**Workaround:** Pod spec `dnsConfig timeout:1 attempts:1` makes failed DNS
lookups resolve in 1 second instead of 10, partially mitigating the no-DNS
issue for traceroute. Not a fix — just makes the timeout fast.
**Structural fix options:**
1. Custom login shell (`nodalarc-shell`) that tries vtysh first, falls back to
   whitelisted system binary execution with full argument passthrough. Preserves
   security (whitelist only) while enabling full flag support.
2. FRR upstream contribution to extend the `traceroute` and `ping` command
   definitions to accept optional flags.
3. Accept the FRR limitation — this is how FRR works, same as how Cisco's
   traceroute has its own syntax that differs from Linux traceroute.
**Trigger:** User feedback on diagnostic limitations, or NodalPath debugging
requiring specific traceroute options.

## User Experience Enhancements

### UX-001: Terminal connection time (4-8 seconds)
**Priority:** P3 — acceptable for now
**Current state:** SSH key exchange + vtysh startup takes 4-8 seconds. Terminal
shows "Connecting to {node}, please wait..." during this time.
**Fix options:** Pre-establish SSH connections on session start (warmup pool),
or use faster key exchange (already ED25519, which is fast). The bottleneck
may be vtysh startup time rather than SSH negotiation.
**Trigger:** User feedback, or when session pod count makes warmup impractical.

### UX-002: Persistent terminal sessions across browser refresh
**Priority:** P3 — nice to have
**Current state:** Browser refresh kills all terminal sessions (WebSocket
disconnects). User must reopen each tab.
**Fix:** VS-API could maintain SSH sessions server-side with session IDs.
Browser reconnects to existing sessions by ID. Requires server-side session
management.
**Trigger:** User feedback on workflow disruption from browser refreshes.

---

## Vendor Integration

### VENDOR-001: Juniper cRPD / Arista cEOS routing stack support
**Priority:** P2 — key product differentiator
**Current state:** FRR is the only routing stack. The architecture supports
pluggable routing stacks (stack.yaml, Jinja2 templates, container image swap).
**Impact:** Terminal access already works via SSH — vendor NOS images ship
with SSH and their own CLI. The VS-API SSH proxy connects to the vendor CLI
identically. The FRR-specific hardening (OpenSSH `sshd` with vtysh login shell)
is replaced by the vendor's own security model.
**Trigger:** Customer/partner request for specific vendor NOS.

### VENDOR-002: Physical node integration (in-band SSH)
**Priority:** P2 — strategic differentiator
**Current state:** All nodes are emulated (K8s pods). Terminal access uses
pod IP on the K8s management network.
**Fix:** Node registry maps node_id to IP (pod IP for emulated, management IP
for physical). VS-API SSH proxy connects to physical router's management IP.
Same WebSocket endpoint, same xterm.js UI.
**Trigger:** First physical node integration (BGP neighbor at ground station,
user terminal connected to satellite).

---

## MBB / Handover Enhancements

### MBB-001: Overlap preemption for superior satellites
**Priority:** P2 — performance optimization
**Current state:** When both GS terminals are physically occupied (1 steady +
1 teardown overlap), a new satellite with a much better score must wait up to
`mbb_overlap_ticks` for the teardown to expire before it can be allocated. The
GS stays on a suboptimal link during the wait. No packet loss — the steady link
carries traffic.
**Fix:** If a candidate's score exceeds the steady link's score by a configurable
threshold (e.g., `mbb_preemption_threshold`), abort the active overlap immediately
to free the terminal for the superior link. The evicted overlap link drops (BBM
for that terminal), but the overall handover quality improves because the GS
switches to the best available satellite faster.
**Design consideration:** The fold checks: `new_score > steady_score +
threshold` AND `physical_room == False` AND `pending_teardowns` non-empty for
this GS → abort the lowest-remaining-time teardown → free terminal → allocate.
**Trigger:** Empirical evidence of suboptimal link selection during overlap
windows at scale (576+ sats).
**Plan reference:** `specs/plans/mbb-reserve-terminal-overlap.md` Known Limitations.

### MBB-002: Configurable mbb_reserve per ground station
**Priority:** P2 — needed for high-terminal-count gateways
**Current state:** `mbb_reserve` is hardcoded to 1 for all GSes with
`tracking_capacity > 1`. A GS with `tracking_capacity=8` gets 7 steady links
and 1 spare for MBB. Only 1 concurrent overlap at a time.
**Fix:** Make `mbb_reserve` configurable per GS in the ground station config.
A gateway with 8 terminals might set `mbb_reserve=2` to allow 2 concurrent
overlaps (6 steady + 2 spare). The invariant `steady + overlap <=
tracking_capacity` is already in the algorithm — just needs the per-GS
parameter plumbing.
**Trigger:** Gateways with `tracking_capacity >= 4` experiencing serialized
handover delays.
**Plan reference:** `specs/plans/mbb-reserve-terminal-overlap.md` §What This
Plan Does NOT Do.

### MBB-003: Closed-loop MBB verification (IS-IS adjacency feedback)
**Priority:** P3 — correctness refinement, not required for v1
**Current state:** MBB uses an open-loop timer (`mbb_overlap_ticks`) to hold
the old link. The operator sets the timer based on expected routing convergence
time. This is the standard telecom approach (BFD, OSPF dead interval, etc.).
**Fix:** Add optional closed-loop verification: the MI or Node Agent reports
IS-IS adjacency state on the new terminal. The OME waits for "adjacency UP"
before releasing the old link, with the open-loop timer as a fallback ceiling.
**Design consideration:** Adds a feedback loop from routing layer to OME,
partially violating the Physicist role separation. The open-loop timer remains
the primary mechanism; closed-loop is an optimization that tightens the overlap
window. May not be worth the complexity unless empirical testing shows the
open-loop timer is too conservative (holding links longer than needed).
**Trigger:** Empirical evidence that open-loop timer is significantly over-
conservative, or NodalPath requiring tighter handover timing.
**Plan reference:** `specs/plans/mbb-reserve-terminal-overlap.md` §What This
Plan Does NOT Do.

### MBB-004: OME crash recovery of fold state from LinkStateSnapshot
**Priority:** P2 — operational resilience
**Current state:** OME starts fresh on restart. `current_associations = {}`,
`mbb_pending_teardowns = {}`. The fold rebuilds within a few ticks from
visibility geometry. No hysteresis continuity across restarts — active links
lose their hysteresis discount and may experience unnecessary handovers.
**Fix:** On OME restart, read the latest LinkStateSnapshot and reconstruct
`current_associations` from active links and `mbb_pending_teardowns` from
links with `scheduling_state="teardown"` and `teardown_remaining_ticks > 0`.
Age correction: `effective_remaining = remaining - int((current_sim_time -
snapshot.sim_time).total_seconds() / step_seconds)`. If ≤ 0, drop immediately.
**Design consideration:** `sim_time` is absolute (survives seek). Do NOT use
`step_number` (resets on seek, produces negative age across epochs).
**Trigger:** OME crash in production causing visible handover disruption.
**Plan reference:** `specs/plans/mbb-reserve-terminal-overlap.md` §Recovery.

### MBB-005: scheduling_state consumption by NodalPath PCE
**Priority:** P2 — required for NodalPath MBB correctness
**Current state:** `scheduling_state: "active" | "teardown"` is added to
VisibilityEvent and LinkState by the MBB plan. IS-IS mode ignores it. NodalPath
PCE does not yet filter on it.
**Fix:** NodalPath's PCE must exclude `scheduling_state="teardown"` links from
path computation. These links are physically UP (carrier, IS-IS adjacency) but
logically draining. Routing new traffic over them defeats the MBB invariant.
**Trigger:** First NodalPath session with `mbb_dispatch=true` and multi-terminal
ground stations.
**Plan reference:** `specs/plans/mbb-reserve-terminal-overlap.md` §Event Model.

### MBB-006: Routing-layer MBB (§6.2) for single-terminal segments
**Priority:** P2 — required for UT-scale handovers
**Current state:** Single-terminal GSes and UTs cannot do physical-layer MBB
(no spare terminal). They fall back to cold handover (BBM) with IS-IS.
`specs/nodalarc-gs-scheduling-hysteresis.md` §6.2 describes routing-layer MBB
where NodalPath pre-installs forwarding state on the new satellite BEFORE the
physical handoff.
**Fix:** Implement the §6.2 mechanism: NodalPath proactively advertises the
segment's prefix on the new satellite. The physical handoff is hard BBM, but
the routing fabric sees MBB (both satellites advertising the prefix during the
transition). Forwarding gap bounded by physical handoff time (~ms), not IGP
reconvergence (~seconds).
**Trigger:** UT implementation (R-OME-004b) or single-terminal GS scenarios
requiring zero-loss handovers.
**Plan reference:** `specs/plans/mbb-reserve-terminal-overlap.md` §What This
Plan Does NOT Do.

## Node Agent Hardening

### NA-001: tc mirred atomic replace
**Priority:** P2 — defensive correctness
**Current state:** `_tc_mirred_redirect` in `ground_bridge.py` uses a
delete-then-add sequence for the ingress qdisc. Under the current architecture,
phases execute sequentially (Phase 1 completes before Phase 2), so there is no
kernel race. However, if the delete fails silently (partial cleanup), the
subsequent add fails with EEXIST.
**Fix:** Use `NLM_F_REPLACE | NLM_F_CREATE` flags in the pyroute2 tc call
for the ingress qdisc. Single atomic syscall replaces any existing qdisc or
creates a new one. Eliminates the delete/add dance and the partial-failure
edge case.
**Trigger:** Any EEXIST failure observed in Node Agent logs during handovers,
or as a prerequisite before implementing same-tick terminal reuse.
**Plan reference:** `specs/plans/mbb-reserve-terminal-overlap.md` §Prerequisites.

### NA-003: IS-IS timers and BFD as user-configurable in session wizard — RESOLVED
**Priority:** P1 — needed for realistic emulation
**Current state:** The IS-IS template (`isisd.conf.j2`) is now fully
parameterized with defaults: hello interval (1s), hello multiplier (3),
IETF SPF delay (init=50ms, short=200ms, long=5s, holddown=10s,
time-to-learn=500ms), BFD (disabled by default, detect-multiplier=3,
rx/tx=300ms). All parameters flow through `config_overrides` in the
session YAML. `bfdd=yes` in the daemons file (daemon available but
IS-IS only uses BFD when `bfd_enabled=true`).
**What's missing:** The session wizard (VF Extensions & Area Strategy
page) has no UI for these parameters. Currently checkboxes exist for
Traffic Engineering, MPLS/LDP, and Segment Routing only.
**Fix — wizard UI:**
1. IS-IS Timers section: hello interval (dropdown: 1s, 3s, 10s),
   hello multiplier (dropdown: 3, 5, 10), SPF delay preset
   (dropdown: "aggressive" = 50/200/5000, "moderate" = 200/1000/10000,
   "conservative" = 1000/5000/30000, "custom" = text fields).
2. Enable BFD toggle (yes/no). When enabled, show BFD timers:
   detect multiplier (3/5), receive interval (100ms/300ms/1000ms),
   transmit interval (same options).
3. All values flow into `config_overrides` in the generated session
   YAML. The Operator passes them through `build_template_vars` →
   Jinja2 template.
**Files:** `frontend/vf/src/console/` (wizard components),
`services/nodalarc_operator/session_deployer.py` (config_overrides
passthrough), `lib/nodalarc/template_vars.py` (if needed).
**Trigger:** Before next release — operators need to tune IS-IS for
their specific scenario.
**Plan reference:** Identified during MBB live testing 2026-04-23.

### UX-003: Design system — single-source style token library — RESOLVED
**Priority:** P2 — foundational for all UX work
**Resolved:** 2026-04-24. tokens.ts with 120+ tokens, injectCssTokens()
before createRoot(), all 11 CSS files + config.ts migrated. CSS coverage
test scans all var() references. Zero hardcoded hex colors remaining.
**Current state:** `variables.css` has 26 generic tokens (colors,
borders, layout dimensions). No typography, spacing scale, z-index
system, or semantic naming. 10 CSS files with hardcoded values
scattered throughout. Compare to nodalviz (`theme.py`) which has
~120 semantic tokens covering colors, typography, geometry, spacing,
z-index — all in one file, all with domain-specific names like
`COLOR_SAT_LEO`, `COLOR_PROTO_BGP`, `FONT_SIZE_LABEL`.
**Fix:**
1. Expand `variables.css` into a complete design token system:
   - Typography: font family, 6 size tiers (xs through xxl)
   - Spacing scale: 4/8/12/16/24/32/48px increments
   - Z-index layers: bar, panel, overlay, modal, tooltip
   - Semantic colors: `--color-node-satellite`, `--color-node-gs`,
     `--color-link-up`, `--color-link-down`, `--color-proto-isis`,
     `--color-proto-ospf`, `--color-state-active`,
     `--color-state-teardown`
   - Status palette: success, warning, error, info
   - Opacity scale for overlays and disabled states
   - Border radii, shadows, transitions
2. Replace all hardcoded values in the 10 CSS files with token refs.
   Zero magic numbers in component CSS.
3. Document each token's purpose with inline comments.
4. Establish rule: no hardcoded colors, font sizes, or spacing in
   component CSS — everything through tokens.
**Architecture goal:** Change the theme in one file, every component
updates. Same pattern as nodalviz/theme.py but in CSS custom
properties for browser runtime resolution.
**Trigger:** Before any major UX feature work (SCALE-001 Phase 2/3).
All new rendering code should use the token system from day one.

### UX-004: Toast/notification system for async events — RESOLVED
**Priority:** P2 — needed for operator awareness
**Resolved:** 2026-04-24. Toasts.tsx — auto-dismissing (5s), categorized
(info/warning/error), stackable, slide-in animation. Fed from
snapshot.recent_events.
**Current state:** Async events (handover, link failure, IS-IS
reconvergence) are visible only in the event log. No proactive
notification to the operator when something important happens.
**Fix:** Toast notification component — non-modal, auto-dismissing,
stackable. Categories: info (handover completed), warning (MBB
fallback to BBM), error (link failure, Node Agent timeout). Feeds
from the same NATS events the EventLog consumes.
**Trigger:** When operators miss important events because they're
focused on the globe view.

### UX-005: Dashboard/metrics view — RESOLVED
**Priority:** P2 — real-time network health at a glance
**Resolved:** 2026-04-24. Dashboard.tsx — satellite/GS/link counts,
constellation name, sim time. Accessible as a view mode.
**Current state:** Network state is spread across NetworkSummary
panel, EventLog, and individual satellite/GS detail panels. No
single view shows overall health.
**Fix:** Dashboard panel with real-time metrics: total links
(ISL/ground), handover rate, packet loss (if probes active),
IS-IS adjacency count, BFD session count, SPF run rate. Feeds from
NATS LinkStateSnapshot + OME ClockTick. Sparkline charts for trends.
**Trigger:** When operating constellations >100 satellites where
individual satellite monitoring is impractical.

### UX-006: Keyboard shortcuts for power users
**Priority:** P3 — ergonomics
**Current state:** `useKeyboard.ts` exists but capabilities are
limited. No documented shortcut map.
**Fix:** Full keyboard shortcut system: Space=play/pause,
+/-=speed, Esc=deselect, /=search, T=toggle topology view,
number keys=select orbital plane. Help overlay (?) showing
all shortcuts.
**Trigger:** Power user feedback.

### UX-007: Wizard timer UI — compact layout with hover tooltips
**Priority:** P4 — UX polish
**Current state:** Timer fields use full-width cards with description
and range text displayed inline below each input. The page is too
long. The descriptions are useful but should be hover tooltips, not
always-visible text.
**Fix:** Replace inline desc/range text with hover tooltips (title
attribute or custom tooltip component). Each timer becomes a single
compact row: label on left, input+unit on right, tooltip on hover.
Cross-field validation errors stay inline (they're actionable, not
informational). Full UX design pass for the wizard needed — this is
one item in a broader UX workstream.
**Trigger:** UX design pass for the session wizard.

### UX-008: Cross-product layout architecture (NodalArc + NodalPath)
**Priority:** P2 — foundational for visual consistency across products
**Current state:** NodalArc's VF has an organic layout that evolved
feature-by-feature: TopBar, BottomBar (time controls), left-side
InfoPanel, CliDrawer at the bottom, various detail panels. There is
no formal zone architecture, and NodalPath has no shared visual
shell with NodalArc.
**Reference:** Aalyria Spacetime uses a consistent four-zone layout:
- Top bar: hamburger nav, search, Map/Graph toggle, user account
- Left panel: collapsible overlay/filter controls (categorized)
- Right panel: context-sensitive detail (appears on selection)
- Bottom bar: timeline scrubber with playback controls
Both Spacetime's "Map" (3D globe) and "Graph" (topology diagram)
views share the same shell. The content changes; the container
stays constant. This is the pattern we should follow.
**Fix — shared layout shell:**
1. Define a formal four-zone layout that both NodalArc and NodalPath
   use. The zones are structural containers — the content inside
   them differs per product and per view.
2. Top bar: product name, search, view toggle (Globe/Topology),
   session info, user controls. Shared between products.
3. Left panel: collapsible sections for filtering and overlay
   controls. NodalArc sections: Platforms (satellite/GS/UT
   visibility toggles with color-coded icons), Overlays (link
   types, coverage footprints, ground tracks), Protocol State
   (IS-IS/OSPF adjacency status overlay). NodalPath sections:
   same Platforms + Forwarding State (MPLS label overlays, SR
   policy paths), Spectrum (frequency/interference visualization).
4. Right panel: context-sensitive detail on selection. NodalArc
   content: satellite detail (orbital params, ISL neighbors, IS-IS
   state, terminal access), GS detail (tracking capacity, active
   links, MBB state, terrestrial prefixes), link detail (latency,
   bandwidth, BFD state). NodalPath content: same node info +
   forwarding table, MPLS label stack, SR policy, computed path.
5. Bottom bar: timeline scrubber — identical between products.
   Time range selector, play/pause/seek, speed control, live
   indicator. Our TimeControls component already does this;
   needs visual polish to match the Spacetime-level timeline.
6. The Globe view and Topology view are tabs/toggles within the
   same layout — not separate pages. We already have both
   (GlobeView.tsx and TopologyView.tsx); they need to be promoted
   to a first-class toggle in the top bar like Spacetime's
   Map/Graph pill.
**Architecture:** The layout shell is a shared React component
library consumed by both NodalArc VF and NodalPath console. Zone
content is injected via slots/children. This requires UX-003
(design token system) to be in place first for visual consistency.
**Trigger:** Before any major UX feature work. The layout shell
defines where features live — building features without the shell
means retrofitting them later.

### UX-009: Platform type filter panel with color-coded icons
**Priority:** P2 — operator orientation
**Current state:** All satellites render the same color (unless
selected). Ground stations have a different color. No visual
distinction between orbital planes, satellite types, node roles,
or link types without clicking individual nodes.
**Reference:** Spacetime's left panel has a PLATFORMS section with
color-coded, toggleable entries: LEO (cyan), MEO (blue),
GEO/Lunar (gray), Aircraft (pink), Ship (teal), Gateway (yellow),
User Terminal (orange). Clicking a type toggles visibility. Each
node on the globe matches its type color.
**Fix for NodalArc:**
1. Left panel "Platforms" section with toggleable entries:
   - Satellites by orbital plane (Plane 0, Plane 1, ...) with
     per-plane colors matching the globe rendering
   - Ground Stations (gateway icon, distinct color)
   - User Terminals (when UT support lands, ARCH-010)
2. Each platform type uses a consistent icon + color from the
   design token system (UX-003).
3. Toggle visibility: unchecking "Plane 3" hides all Plane 3
   satellites from the globe. Useful for isolating specific
   orbital planes during debugging.
4. "Trail Path" toggle: show/hide orbital trails (already exists
   as a feature, needs UI control in the filter panel).
5. Link type filtering: show/hide ISL links, ground links, or
   VXLAN cross-node links independently.
**For NodalPath:** Same platform filter panel + additional entries
for SR policy paths, MPLS label visibility.
**Trigger:** When satellite count exceeds 100 and visual clutter
makes the globe hard to read without filtering.

### UX-010: Context detail panel with topology mini-graph
**Priority:** P2 — network context without view switching
**Current state:** Selecting a satellite shows SatelliteDetail panel
with orbital parameters, IS-IS neighbors, and a terminal launcher.
Selecting a GS shows GroundStationDetail. Selecting a link shows
LinkDetail. These panels show data tables but no visual network
context.
**Reference:** Spacetime's right detail panel includes a "TOPOLOGY"
section with a small force-directed graph showing the selected
node's immediate neighbors and link types. For links, it shows
"CONNECTED NODES" with typed edges and a "ROUTES" section showing
the end-to-end forwarding path. This gives the operator network
context without leaving the globe view.
**Fix for NodalArc:**
1. Add a "Topology" collapsible section to SatelliteDetail and
   GroundStationDetail panels. Renders a small force-directed
   graph of the selected node + its IS-IS neighbors (1-hop).
   Edges colored by link type (ISL=gray, ground=green).
   Clicking a neighbor in the mini-graph selects it.
2. Add a "Routes" section to GroundStationDetail showing the
   IS-IS computed path to other ground stations. Each hop shows
   node type icon + interface name. Clickable hops for selection.
3. For LinkDetail: show both endpoints with their interface names,
   link type, latency, bandwidth, BFD state, and
   scheduling_state (active vs teardown for MBB).
**For NodalPath:** Same topology mini-graph + MPLS label stack
visualization at each hop, SR policy SID list, computed path
overlay on the globe.
**Trigger:** When operators need to understand network paths without
switching between Globe and Topology views.

### UX-011: Weather/environmental overlay system
**Priority:** P3 — link budget modeling context
**Current state:** No environmental data on the globe. The earth is
a static blue marble texture.
**Reference:** Spacetime has a WEATHER section with radio-selectable
overlays: Surface Temperature, Cloud Moisture, Wind Speed,
Precipitation. These visualize environmental conditions that affect
RF link budgets (rain fade, atmospheric attenuation). The overlays
render as colored layers on the earth surface.
**Fix:** Weather overlay system as a left-panel control section.
Phase 1: static overlays from public datasets (NASA EOSDIS, NOAA
GFS). Phase 2: real-time data feeds. Phase 3: integration with
link budget models (RF ground links derate under rain fade).
**Applicability:** Both NodalArc (affects ground link availability)
and NodalPath (affects link budget and path selection).
**Trigger:** When modeling RF link degradation under weather
conditions, or when demonstrating the platform to customers who
care about environmental impacts on constellation performance.

### UX-012: Map/Graph view toggle as first-class control
**Priority:** P2 — navigation clarity
**Current state:** Globe view and Topology view exist but switching
between them is not prominent. Users may not discover the topology
view.
**Reference:** Spacetime has a prominent pill toggle ("Map" / "Graph")
centered in the top bar. Both views share the same layout shell —
the left panel filters, right panel detail, and bottom timeline
all stay constant. Only the center content switches.
**Fix:** Promote the Globe/Topology toggle to a pill button in the
top bar center. Both views share the same layout shell (UX-008).
Selection state persists across view switches — selecting a
satellite in Globe and switching to Topology shows that satellite
highlighted in the topology graph, and vice versa. The left panel
adapts its filter options to the active view (Globe: platform
visibility, overlays. Topology: layout algorithm, node grouping).
**Trigger:** UX-008 (layout shell) must be in place first.

### UX-013: Cislunar-scale globe rendering
**Priority:** P3 — required for cislunar scenarios
**Current state:** Globe zoom is limited to LEO altitude range. No
Moon rendering. No Lagrange point visualization. The
`reference_body` field on GroundSegment supports "earth" and
"luna" but the VF has no visual for anything beyond LEO.
**Reference:** Spacetime renders Earth, Moon (bump-mapped), and
cislunar vehicles. The "GEO / Lunar" platform type extends to
cislunar distances. satellitemap.space shows JWST and Artemis
trajectories.
**Fix:**
1. Extend camera zoom range to cislunar distances (~400,000 km).
2. Render Moon at correct orbital position with surface texture.
3. Lagrange point markers (L1-L5) as labeled waypoints.
4. Clarke belt ring at GEO altitude (35,786 km).
5. Support for cislunar vehicle ephemeris in the OME (TLE or
   custom propagator for high-eccentricity/multi-body orbits).
**Applicability:** Both products — NodalArc for cislunar network
emulation, NodalPath for cislunar path computation (relay
scheduling through Lagrange point infrastructure).
**Trigger:** First cislunar scenario or when demonstrating
multi-body support to potential customers.

### NA-004: Terrain-aware horizon profiles for ground stations
**Priority:** P2 — needed for realistic link availability modeling
**Current state:** `min_elevation_deg` is a scalar per station — one
value for all azimuths. The visibility check in `ome/visibility.py`
computes elevation angle and compares against this flat threshold.
Real ground stations have terrain obstructions (mountains, buildings,
vegetation) that block specific azimuth/elevation sectors.
**Fix — phased:**
1. **Add `azimuth_deg` to `GroundVisibility` output.** The GS→satellite
   vector is already computed; azimuth is `atan2(east, north)`.
   Trivial change, no behavioral impact.
2. **`horizon_profile` field on `GroundStationConfig`.** References an
   external file: `horizon_profile: configs/ground-stations/profiles/
   hawthorne.csv`. Format: CSV with `(azimuth_deg, min_elevation_deg)`
   waypoints at 1° or 5° resolution. The visibility check interpolates
   between waypoints: `elevation >= interpolated_mask(azimuth)`.
   When absent, falls back to the scalar `min_elevation_deg` (backward
   compatible).
3. **Profile sources:** Site survey data (CSV from antenna vendor),
   or auto-generated from SRTM/DTED digital elevation models given
   the station's coordinates. Auto-generation is a separate tool,
   not in the OME hot path.
**Model change:** `min_elevation_deg: float | None` stays as the
uniform fallback. New field `horizon_profile: str | None` (path to
CSV). The `check_ground_visibility` function accepts either and
dispatches accordingly.
**Trigger:** When modeling specific real-world sites where terrain
masking materially affects link availability (mountain valleys,
urban canyons, polar stations near ridgelines).
**Plan reference:** Identified during MBB testing 2026-04-23.

### NA-002: Ground station elevation mask realism — RESOLVED
**Priority:** P1 — affects emulation accuracy
**Current state:** Test sessions use `min_elevation_deg: 10` which produces
unrealistically large GS footprints. Real Starlink gateways operate at ~25°
minimum, consumer terminals at ~25-40°. The large footprint means too many
satellites are visible simultaneously, creating unrealistic scheduling behavior
and masking handover problems that would appear with realistic geometry.
**Fix:** Align default elevation masks with real hardware specifications.
Gateway stations: 15-25° (depending on antenna characteristics). Consumer
terminals: 25-40°. Per-station override already supported via
`min_elevation_deg` in station config.
**Trigger:** Any scenario intended to produce realistic handover behavior
(which is all of them — the platform exists to emulate reality).
**Plan reference:** Identified during MBB live testing 2026-04-23.

### NA-005: Ground station model library incomplete — terminals not accurate
**Priority:** P1 — affects emulation fidelity for every session
**Current state:** The model library architecture is in place (2026-04-27):
station models in `configs/ground-stations/stations/`, sets reference by
name, sessions reference sets. But the station data is not yet accurate:
- 66 US stations have `terminals:` derived from FCC `antennas:` count,
  but bandwidth and tracking_capacity are bulk-assigned (10 Gbps, N
  tracking per N antennas). Real gateway terminals have heterogeneous
  configurations — some sites have Ka-only, some Ka+E-band, some have
  mixed terminal types with different tracking capacities.
- 59 international stations have placeholder `terminals:` (single
  terminal, 1 Gbps, tracking_capacity=1) because we don't have national
  regulator filing data yet. Many of these are real Starlink gateways
  with 8+ antennas — the models are undersized.
- No station has terrain-aware horizon profiles (see NA-004).
- `band:` field on stations is informational only — not connected to
  terminal type or link budget model.
**What "done" looks like:** Every station model reflects its actual
hardware: correct terminal count and type per national regulator filings
(FCC for US, Ofcom for UK, BNetzA for Germany, etc.), correct bandwidth
per terminal type, correct tracking_capacity. International stations
need the same filing research that was done for the 66 US stations.
The `antennas:` field should be derivable from `terminals:` (sum of
counts), not the other way around.
**Trigger:** Before any customer demo or accuracy-sensitive scenario.
Any session using international stations is running on placeholder data.
