# Nodal Arc: Implementation Planning Prompt

## What This Project Is

Nodal Arc is an orbital network emulation lab. It deploys real routing stacks (FRR, BIRD, etc.) on container topologies driven by real orbital mechanics, so network engineers can observe how routing protocols behave on satellite constellation topologies. The architecture has five backend components (OME, TO, MI, VS-API, VF) connected by ZeroMQ and a shared Pydantic model library.

## Documents You Must Read Before Writing Any Code

Two specification documents are in this repository:

1. `nodalarc-prd.md` — The complete PRD. Read Sections 1-3 (architecture), Section 8 (tech stack), Section 9 (repo structure), Section 10 (phases), Section 13 (implementation constraints, schemas, module boundaries), Appendix A (implementation notes), and Appendix B (test strategy). Section 13 is prescriptive and normative. Appendix A contains specific warnings about where things break at runtime.

2. `nodalarc-vf-spec.md` — The VF specification. Read this only when beginning Phase 1D. It depends on the VS-API being functional first.

Read the PRD thoroughly before producing a plan or writing code. The PRD is intentionally detailed to prevent you from having to make architectural decisions. If the PRD specifies how something works, follow it. If it does not address something, choose the simplest approach that satisfies the requirements.

## How To Approach This Project

This is a phased build with hard gates between phases. Do not start Phase 1B code until Phase 1A exit criteria are met. Do not start Phase 1C until Phase 1B exit criteria are met. The phases exist because each layer depends on the one below it being correct. A bug in a Pydantic model in Phase 1A becomes a wrong ZeroMQ message in Phase 1B, a corrupt SQLite row in Phase 1C, and a broken visualization in Phase 1D. Fix things where they originate.

### Phase 1A: Shared Library + OME

Build and test in this exact order:

1. **Pydantic models first.** All models in `lib/nodalarc/models/`. Every event type, every config schema, every VS-API snapshot model. Write serialization round-trip tests for every model before moving on. The discriminated union for constellation config (Section 13.26) must dispatch correctly for parametric, explicit, and TLE modes.

2. **AddressingScheme class.** Derives all node identifiers from plane/slot/station indices. One class, one source of truth. Write tests that verify satellite and ground station identity derivation.

3. **Area assignment logic.** All four strategies (stripe, per-plane, flat, explicit). Write tests that verify area_id per node and cross_area flags per interface for the reference constellation.

4. **`build_template_vars()` function (Section 13.25).** This is the single function that produces the complete Jinja2 template variable namespace for any node. Both `na-deploy` and `na-reconfig` call it. Test it for satellite nodes, ground station nodes, nodes with per-station terrestrial prefix overrides, and different area strategies.

5. **SQLite schema and query functions.** `create_tables()`, typed insert/query functions. Test WAL mode and concurrent reads.

6. **ZeroMQ channel constants.** All ports, topic prefixes, socket addresses in `zmq_channels.py`.

7. **Keplerian propagator.** Start with the 4-node-test constellation (4 satellites, 2 planes). Verify a satellite returns to its starting position after one orbital period. Verify ECEF-to-geodetic conversion produces correct lat/lon/alt. Do NOT start with starlink-mini (60 nodes). You cannot debug orbital math at scale.

8. **Visibility computation.** This is the hardest part of Phase 1A. Line-of-sight requires computing whether the straight-line path between two points clears the earth body. Test against known geometries: same-plane satellites always have LOS within range, opposite-side satellites never do. Angular velocity computation for polar seam requires coordinate frame derivatives. Test co-rotating same-plane neighbors (near-zero angular rate) and cross-plane neighbors at increasing latitudes.

9. **Ground station access scheduling.** Highest-elevation and longest-pass policies. Test with the 4-node-test constellation where multiple satellites may be visible simultaneously.

10. **Event stream and timeline writer.** ZeroMQ PUB + JSON Lines file. The timeline must contain VisibilityEvents with full-constellation position snapshots at each unique timestamp (TimelinePositionSnapshot records).

11. **Constellation loader.** Parametric mode (starlink-mini, polar-seam-demo) and explicit mode (4-node-test). TLE mode can be stubbed but is a lower priority for Phase 1.

**Exit gate:** All shared library tests pass. The OME produces correct visibility timelines for all three reference constellations. The 4-node-test constellation produces `visible=True, scheduled=False` events for terminal exhaustion. The polar-seam-demo produces observable cross-plane ISL dropouts at polar latitudes.

### Phase 1B: Container Networking + Routing

Key pain points (from PRD Appendix A):

- **pyroute2 netlink is not the same as `ip link`.** The PID-to-namespace mapping through containerd uses `/proc/{pid}/ns/net` where pid is the container's init process, not the pod sandbox PID. Use the K8s API to get the container ID, then `crictl inspect` to get the PID. Test veth creation on a single pair of pods before attempting the full constellation.

- **tc netem + tbf ordering matters.** Apply tbf as root qdisc, netem as child. Verify round-trip latency between two pods matches 2x configured one-way delay using `ping` with timestamps, not `tc -s qdisc show`.

- **FRR config templates: test in isolation first.** Render a template for one satellite node, load it into a standalone FRR container, run `vtysh -f /etc/frr/frr.conf`. Common bugs: incorrect IS-IS NET format (must be even number of hex digits), missing `no ipv6 nd suppress-ra` on point-to-point interfaces, IS-IS interface type not set to point-to-point.

- **The rolling window OME computation can be deferred.** Pre-compute the entire first orbital period as a single window. Add rolling windows later.

- **Discrete-Event Mode is simpler than Real-Time Mode.** Build it first. It reads a file, pops events, applies link changes, calls convergence gate. No timing, no compression.

### Phase 1C: MI + VS-API

The FRR IS-IS adapter will be the messiest code in the project. FRR's gRPC API does not expose all IS-IS events. You will parse syslog for some events. The adapter's job is to produce clean AdapterEvent records from dirty input. Record which source (gRPC or log) each event came from in event_data.

The VS-API is a straightforward FastAPI + WebSocket server. It subscribes to ZeroMQ channels, maintains in-memory state, and publishes snapshots. Test it with `wscat` before starting the VF.

### Phase 1D: Visualization Frontend

Read `nodalarc-vf-spec.md` now. The VF is a standalone React + Three.js application. Key architectural decisions:

- Three.js integration is imperative (`useRef`/`useEffect`), not through react-three-fiber.
- Single static Blue Marble texture, no tile streaming, no API keys.
- Satellite positions lerp toward targets in the render loop (exponential convergence).
- Use Line2 from `three/addons` for pixel-width links, not standard Line (clamped to 1px).
- Labels are HTML divs positioned via `vector.project(camera)`, not Three.js text geometry.
- The topology view (Section 6A of the VF spec) is HTML5 Canvas, not Three.js.

### Phase 1E: Scenarios + Documentation

Scenario executor, reference scenario YAML files, `na-compare` tool, docs.

## Critical Rules

These are from PRD Section 13 and are non-negotiable:

- **Python 3.14+ for all backend components.** No Go, no Rust, no Java. Shell scripts are thin wrappers only. The VF uses TypeScript.
- **Pydantic v2 for all data modeling.** No raw dicts for structured data. No dataclasses. All cross-component data flows through Pydantic models.
- **No abstraction layers.** No EventBus, MessageRouter, ConfigManager, Settings singleton, or logging framework. Use ZeroMQ directly. Load YAML and validate through Pydantic directly. Use `logging.getLogger(__name__)` directly.
- **No extra dependencies.** Only libraries in the tech stack table (PRD Section 8). No click, no rich, no celery, no ORM. Use argparse, standard logging, sqlite3 directly.
- **pyroute2 for all netlink operations.** No shelling out to `ip link`, `ip netns`, `tc`.
- **f-strings exclusively** for string formatting (except logging lazy formatting).
- **`model_config = ConfigDict(frozen=True)`** for all event models (immutable after creation). Config models may be mutable.
- **All port numbers in `zmq_channels.py`.** No literals anywhere else.
- **Test with real infrastructure.** No mocking of Linux networking, ZeroMQ, or SQLite in unit tests that test those interfaces.

## What NOT To Do

- Do not create plugin systems, registries, factories, or extensibility frameworks. New actions are added by editing a match statement. New adapters are added by writing a module and importing it by name.
- Do not create an event bus or message abstraction over ZeroMQ.
- Do not add dependencies not in the tech stack table.
- Do not write tests for Three.js rendering, Grafana dashboards, or Helm chart syntax.
- Do not write performance benchmarks disguised as tests.
- Do not start with starlink-mini. Start with 4-node-test.
- Do not build the VF until the VS-API WebSocket is functional and verified.
- Do not implement features not specified in the PRD. If the PRD says "deferred to Phase 2," do not build it.

## Planning Deliverable

Before writing any code, produce a phased implementation plan that:

1. Lists every file to be created in Phase 1A, in dependency order.
2. Lists the test files and what each test proves.
3. Identifies the first executable milestone (the smallest subset of code that produces a verifiable output).
4. For each subsequent phase, lists the files, their dependencies on prior phases, and the verification steps.
5. Calls out the known pain points from PRD Appendix A and your plan to address each one.

The plan should be concrete enough that each item can be executed as a single coding task. Do not produce a plan that says "implement the OME." Produce a plan that says "implement `propagator.py` with `propagate_keplerian(elements, epoch, dt) -> Position` and test it against the ISS TLE ground track."
