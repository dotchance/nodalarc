# Sessions

A session is a running network experiment. It defines which nodes exist, where
they are placed, which links are allowed to become candidates, and what routing
stack runs inside the nodes.

The simplest session is still a single LEO constellation with Earth ground
stations. That is the right place to start. The same session grammar also lets
you assemble multiple orbital regimes, lunar segments, relay nodes, and
body-specific ground sites when the experiment needs them.

## The Session Model

NodalArc sessions are assembled from reusable building blocks:

- **segments** - groups of nodes in a placement frame, such as an Earth LEO
  constellation, an Earth ground-site set, a GEO relay segment, a lunar polar
  relay segment, or a single cislunar relay node
- **link rules** - declarations of which segment endpoints are allowed to form
  candidate links
- **routing** - the protocol and extensions rendered into each router
- **time** - simulation start time, step size, and compression
- **ephemeris** - optional body-position data for multi-body sessions

The important distinction is that a link rule says a link is allowed to be
considered. It does not force the link to exist. Geometry, terminal capability,
policy, capacity, and actuation proof still decide whether the link is actually
active.

## Curated Demo Sessions

These are the sessions intended for normal use and demos:

| Session | What It Shows |
|---------|---------------|
| `earth-leo-simple.yaml` | Default Earth LEO starter. 36 satellites, Earth ground nodes, OSPF, MBB-capable access. |
| `earth-leo-walker.yaml` | Walker-delta LEO shell with IS-IS and traffic engineering. |
| `earth-leo-polar.yaml` | Polar LEO shell with high-latitude ground coverage. |
| `earth-meo-gps.yaml` | GPS-like MEO geometry with long-range RF gateway stations. |
| `earth-geo-inmarsat.yaml` | Representative GEO commercial-relay-style session. |
| `earth-geo-tdrs.yaml` | Representative GEO relay/TDRS-style session. |
| `earth-leo-meo-geo.yaml` | Earth multi-regime session: LEO, MEO, GEO, and ground access in one experiment. |
| `earth-luna-relay.yaml` | Lunar relay demonstrator with Earth relay, lunar polar relay, and lunar ground access. |
| `earth-luna-gateway-site.yaml` | Cislunar gateway-site demonstrator with Earth access nodes, Earth relay, lunar relay, and lunar surface router. |

The catalog files under `configs/constellations/`, `configs/satellite-types/`,
and `configs/ground-stations/` are the reusable parts. The session files under
`configs/sessions/` are the assembled examples.

## Example Session YAML

```yaml
session:
  name: earth-leo-simple
identity:
  mode: segment_namespaced

segments:
  - id: space
    kind: constellation
    source: configs/constellations/demo-36.yaml
    namespace: space
    central_body: earth
    tags: [earth, leo, simple]

  - id: ground
    kind: ground_set
    source: configs/ground-stations/sets/demo-mbb.yaml
    namespace: ground
    reference_body: earth
    tags: [earth, ground, simple]
    scheduling:
      selection_policy:
        name: highest-elevation
      handover_policy:
        name: hysteresis
        params:
          discount_factor: 1.15
          mask_fade_range_deg: 5.0

link_rules:
  - id: ground-access
    kind: access
    endpoints:
      - selector: {segment: ground}
        terminal_role: ground
      - selector: {segment: space}
        terminal_role: ground
    topology:
      mode: visible_candidates

routing:
  protocol: ospf
  area_assignment:
    strategy: flat

time:
  step_seconds: 1
```

Every node-producing segment has a `namespace`. Runtime node IDs are allocated
from that namespace and the node's local ID. For example, a satellite with local
ID `sat-P00S00` in namespace `space` becomes `space-sat-p00s00`. The local ID
and human display name remain separate from the runtime node ID so future
renaming does not break routing identity.

## Segments

### Constellation Segments

A constellation segment references an orbital catalog file:

```yaml
- id: leo
  kind: constellation
  source: configs/constellations/demo-36.yaml
  namespace: leo
  central_body: earth
  tags: [earth, leo]
```

`central_body` tells the OME which body the segment orbits. Current shipped
sessions use Earth and Luna. The grammar is built to grow toward Mars,
Lagrange-point relays, and deeper-space scenarios, but unsupported runtime
features fail validation instead of being silently approximated.

### Ground Segments

A ground segment references a set file or defines sites inline. A ground site is
the physical place. A ground node is a router or terminal system at that place.
A ground node can have one or more terminals, and a site can contain multiple
nodes.

That matters because a real gateway site often has different terminals for
different missions. A Santiago site can have one LEO Ka-band router, one GEO
C-band router, and one cislunar gateway router. They are at the same physical
site, but they are different network nodes with different terminals and
different policies.

### Space Node Segments

A `space_node` segment creates one explicit relay node. The cislunar demo uses
this for an Earth-side relay node in GEO-like placement.

## Link Rules

Link rules define the candidate graph the OME is allowed to evaluate.

| Rule Kind | Meaning |
|-----------|---------|
| `access` | Body-local ground-to-space access. Earth ground to Earth orbit, or lunar ground to lunar orbit. |
| `inter_constellation` | Space-to-space links between constellation segments in the same body frame. |
| `inter_body_relay` | Space-to-space relay across body frames, such as Earth relay to lunar relay. |

Current topology modes:

| Mode | Meaning |
|------|---------|
| `visible_candidates` | Evaluate all visible candidates under the rule, bounded by candidate limits. |
| `nearest_n` | Build a static candidate set from the nearest `n` endpoint pairs. |
| `explicit_pairs` | Use the exact declared candidate pairs. |

Dynamic `nearest_visible` selection is intentionally not accepted until the OME
owns it as a per-tick truth source.

## Ground Handoff Policy

Ground handoff is a property of the ground node, not the whole session.

A ground node with one usable terminal must use break-before-make behavior. A
ground node with multiple compatible terminals can use make-before-break
behavior by reserving enough terminal capacity for overlap.

```yaml
nodes:
  - id: leo-router
    handover_mode: mbb
    mbb_overlap_ticks: 3
    mbb_reserve: 1
    terminals:
      - id: leo-ka
        type: rf
        band: Ka
        count: 2
        tracking_capacity: 1
```

The allocator does not invent overlap where terminal capacity does not exist.
If a configuration asks for make-before-break without enough terminals, the
session is rejected or reduced to the explicitly configured, truthful behavior.

## Switching Sessions

You can switch sessions without restarting the platform:

```bash
make session DEFAULT_SESSION=configs/sessions/earth-leo-meo-geo.yaml
```

The browser session wizard and YAML upload path use the same resolver as the
command-line deploy path. A session that the browser accepts is the same shape
that the runtime accepts.

## Current Limits

NodalArc is intentionally strict. It is better to reject unsupported grammar
than to run an approximation that looks correct.

Current MVP limits include:

- Product session YAML uses the segment grammar. Old top-level
  `constellation`/`ground_stations` session files are not a supported product
  path.
- `access` links are body-local. Cross-body links use `inter_body_relay`.
- The cislunar demos include realistic Earth-Luna range and latency, but DSN
  protocol conversion and full deep-space protocol behavior are future work.
- Router configuration capture/replay and per-class template overlays are future
  work. Today FRR configs are generated from session YAML and templates.

Those limits are part of the truth contract. If the model cannot represent a
behavior correctly yet, it should say so.
