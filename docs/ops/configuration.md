# Configuration Reference

NodalArc sessions are configured through YAML files that assemble reusable
building blocks into a deployable emulation. Users can create sessions through
the browser wizard, upload YAML, or deploy a file with `make session`.

The product session grammar is the segment grammar: top-level `segments` and
`link_rules`. Old top-level `constellation` plus `ground_stations` session YAML
is not a supported product path.

## Session Configuration

A session joins segment definitions, declared connectivity, routing, placement,
time, and optional ephemeris:

```yaml
session:
  name: earth-leo-walker
identity:
  mode: segment_namespaced

segments:
  - id: space
    kind: constellation
    source: configs/constellations/starlink-176.yaml
    namespace: space
    central_body: earth
    tags: [earth, leo]

  - id: ground
    kind: ground_set
    source: configs/ground-stations/sets/starlink-176.yaml
    namespace: ground
    reference_body: earth
    tags: [earth, ground]

link_rules:
  - id: ground-access
    kind: access
    endpoints:
      - selector: {segment: ground}
        terminal_role: ground
      - selector: {segment: space}
        terminal_role: ground
    topology: {mode: visible_candidates}

routing:
  protocol: isis
  extensions: [traffic-engineering]
  area_assignment:
    strategy: per-plane

time:
  step_seconds: 1
```

### Session Fields

| Field | Required | Description |
|-------|:---:|-------------|
| `session.name` | yes | Session identifier |
| `identity.mode` | yes | Must be `segment_namespaced` |
| `segments` | yes | Node-producing and relay building blocks |
| `segments[].namespace` | yes for node-producing segments | Runtime node ID namespace |
| `segments[].kind` | yes | `constellation`, `ground_set`, or `space_node` in current shipped sessions |
| `link_rules` | yes | Declared candidate connectivity between segments |
| `routing.protocol` | yes | `isis` or `ospf` |
| `routing.extensions` | no | Supported protocol extensions |
| `routing.area_assignment` | no | Area assignment strategy |
| `placement.policy` | no | Pod placement policy |
| `time.step_seconds` | no | Simulation time step |
| `ephemeris` | multi-body only | Body ephemeris provider and kernel references |

Runtime node IDs are allocated as `{namespace}-{local_node_id}` and normalized
for NATS, Kubernetes, and interface safety. The local node ID and display name
remain separate from the runtime ID.

## Curated Sessions

These are the shipped demo sessions:

| Session | Routing | Description |
|---------|---------|-------------|
| `earth-leo-simple.yaml` | OSPF | Default Earth LEO starter with MBB-capable ground nodes |
| `earth-leo-walker.yaml` | IS-IS + TE | Walker-delta LEO starter |
| `earth-leo-polar.yaml` | IS-IS + TE | Polar LEO starter with high-latitude ground stations |
| `earth-meo-gps.yaml` | IS-IS | GPS-like MEO starter |
| `earth-geo-inmarsat.yaml` | IS-IS | Representative GEO commercial-relay-style starter |
| `earth-geo-tdrs.yaml` | IS-IS | Representative GEO relay/TDRS-style starter |
| `earth-leo-meo-geo.yaml` | IS-IS + TE | LEO, MEO, GEO, and ground access in one Earth session |
| `earth-luna-relay.yaml` | IS-IS + TE | Earth relay, lunar relay, and lunar ground access |
| `earth-luna-gateway-site.yaml` | IS-IS + TE | Earth gateway site, cislunar relay, lunar relay, and lunar surface router |

The catalog contains more reusable primitives than these sessions. That is
intentional. The sessions are the examples; the catalog parts are the building
blocks.

## Link Rules

| Kind | Current Meaning |
|------|-----------------|
| `access` | Body-local ground-to-space access. Earth ground to Earth orbit, or lunar ground to lunar orbit. |
| `inter_constellation` | Space-to-space links between segments in the same body frame. |
| `inter_body_relay` | Space-to-space relay across body frames, with an explicit protocol boundary. |

Supported topology modes:

| Mode | Meaning |
|------|---------|
| `visible_candidates` | OME evaluates visible candidates under the rule. |
| `nearest_n` | Resolver builds a bounded static candidate set from nearest endpoint pairs. |
| `explicit_pairs` | Resolver uses the exact declared pairs. |

Unsupported future grammar is rejected by the resolver with typed errors. It is
not interpreted as a fallback.

## Routing Protocols

| Protocol | Extensions Available | Description |
|----------|----------------------|-------------|
| `isis` | `traffic-engineering`, `sr`, `mpls` | IS-IS link-state IGP |
| `ospf` | `traffic-engineering`, `sr`, `mpls` | OSPF link-state IGP |

Extension dependencies:

- `mpls` requires `traffic-engineering`
- `sr` requires `isis` or `ospf`

BGP and DSN/DTN-style protocol adapters are roadmap items. Cislunar demo
sessions currently use static protocol-boundary behavior where configured.

## Ground Sites, Nodes, and Terminals

A ground site is the physical location. A ground node is a router or terminal
system at that site. A ground node can carry multiple terminals, and a site can
contain multiple nodes.

```yaml
ground_sites:
  - id: santiago
    display_name: Santiago Gateway Site
    reference_body: earth
    lat_deg: -33.45
    lon_deg: -70.66
    nodes:
      - id: leo-router
        handover_mode: mbb
        mbb_overlap_ticks: 3
        mbb_reserve: 1
        terrestrial_prefixes:
          - prefix: 172.61.10.0/24
            metric: 10
        terminals:
          - id: leo-ka
            type: rf
            band: Ka
            count: 2
            bandwidth_mbps: 1200
            tracking_capacity: 1
```

Handoff policy belongs to the ground node. A node with one compatible terminal
cannot truthfully make before break. A node with multiple compatible terminals
can reserve capacity for overlap.

## Constellation Configuration

Constellation files live in `configs/constellations/` and define orbital
geometry. They are referenced by session segments.

```yaml
mode: parametric
name: starlink-176
satellite_type: starlink-v2
orbit:
  altitude_km: 550
  inclination_deg: 53
  pattern: walker-delta
planes:
  count: 16
  raan_spacing_deg: 22.5
  sats_per_plane: 11
  phase_offset_deg: 2.045
```

Representative catalog examples include:

| Config | Description |
|--------|-------------|
| `demo-36.yaml` | Small LEO starter ring |
| `starlink-176.yaml` | Walker-delta LEO shell |
| `iridium-66.yaml` | Polar Walker-star-style shell |
| `meo-gps-24.yaml` | GPS-like MEO shell |
| `geo-inmarsat-representative.yaml` | GEO commercial-relay-style shell |
| `geo-tdrs-representative.yaml` | GEO relay/TDRS-style shell |
| `luna-polar-8.yaml` | Lunar polar relay shell |

## Satellite Type Configuration

Satellite type files live in `configs/satellite-types/` and describe terminal
inventory:

```yaml
satellite_type:
  name: starlink-v2
  isl_terminals:
    - type: optical
      count: 4
      max_range_km: 5000
      bandwidth_mbps: 100
      max_tracking_rate_deg_s: 3.0
      field_of_regard_deg: 140
  ground_terminals:
    - type: rf
      count: 1
      bandwidth_mbps: 1000
      band: Ku
```

Terminal fields drive candidate eligibility, bandwidth shaping, and
explainability. If a link rule narrows terminal compatibility in a way the
runtime cannot honor, the resolver rejects it rather than silently widening it.

## Ground Station Sets

Ground station set files live in `configs/ground-stations/sets/`. Some sets are
simple station lists; newer sets can define sites, nodes, terminals, and
station-scoped scheduling.

| Config | Purpose |
|--------|---------|
| `demo-mbb.yaml` | Default MBB-capable Earth LEO ground set |
| `meo-gps.yaml` | Long-range RF gateways for MEO/GEO demos |
| `geo-inmarsat.yaml` | GEO commercial-relay-style ground set |
| `geo-tdrs.yaml` | GEO relay/TDRS-style ground set |
| `luna-demo.yaml` | Lunar surface demo sites |

## Resolver Boundary

All product deploy paths use the same session resolver. The browser wizard,
YAML upload, command-line deploy, Operator, OME, Scheduler, VS-API, and
Measurement Interface consume the same resolved runtime view. If a session
cannot be resolved, deployment fails before pods are treated as valid runtime
state.
