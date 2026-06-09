# Sessions

A session is a running network experiment. It defines which nodes exist, where
they are placed, which links are allowed to become candidates, and what routing
runs inside the nodes.

The simplest session is a single LEO constellation with Earth gateway sites —
the right place to start. The same grammar also assembles multiple orbital
regimes, lunar segments, relay nodes, and body-specific ground sites when an
experiment needs them.

This page is the conceptual tour. For the full authoring reference — every field
and allowed value — see the [Configuration Reference](../ops/configuration.md)
and [Configuration Grammar](../ops/configuration-grammar.md).

## The session model

A session is assembled from reusable catalog building blocks:

- **segments** — groups of nodes: a *space* segment (a constellation or space
  node set) or a *ground* segment (a set of sites).
- **link rules** — which segment endpoints may form candidate links, and how
  candidates are chosen.
- **addressing** — address pools for generated nodes (satellites).
- **routing** — one or more routing domains, each running its own protocol
  (`isis`, `ospf`, `bgp`, or `static`) over the nodes it selects, with boundaries
  redistributing between them. A single session can mix protocols.
- **time** — simulation start time, step size, and compression.

A link rule says a link is *allowed to be considered*. It does not force the link
to exist — geometry, terminal capability, policy, capacity, and actuation proof
still decide whether a link is actually active.

## Curated demo sessions

| Session | What it shows |
|---------|---------------|
| `earth-leo-simple.yaml` | Default Earth LEO starter: 36-satellite ring, gateway sites, MBB-capable access. |
| `earth-leo-walker.yaml` | Walker-delta LEO shell. |
| `earth-leo-polar.yaml` | Polar LEO shell with high-latitude gateway sites. |
| `earth-meo-gps.yaml` | GPS-altitude MEO geometry with long-range RF gateways. |
| `earth-geo-inmarsat.yaml` | Representative GEO commercial-relay-style session. |
| `earth-geo-tdrs.yaml` | Representative GEO relay/TDRS-style session. |
| `earth-leo-heo-geo-luna-reachability.yaml` | Multi-regime session: LEO, HEO, GEO, a lunar relay, and lunar ground reachability in one experiment. |

The reusable parts live under `catalog/nodalarc/` (bodies, terminals, orbits,
nodes, sites, site sets, constellations); the assembled examples live under
`catalog/nodalarc/sessions/`.

## A session at a glance

```yaml
session:
  name: earth-leo-simple
  description: Single 36-satellite LEO ring with MBB-capable gateway sites.

segments:
- id: leo
  source: nodalarc:constellations/earth/leo/earth-leo-ring-36.yaml
- id: ground
  placement:
    from_site_set: nodalarc:site-sets/earth/leo/earth-leo-starlink-pop-sites.yaml
  apply:
    scheduling:
      selection_policy: { highest_elevation: {} }
      handover_mode: mbb

link_rules:
- id: leo_access
  topology: { mode: visible_candidates }
  endpoints:
  - select:   { all: [ { segment: ground }, { tag: leo } ] }
    terminal: { all: [ { role: access }, { medium: rf } ] }
    min_elevation_deg: 25
  - select:   { segment: leo }
    terminal: { all: [ { role: access }, { medium: rf } ] }

time:
  start_time: '2026-06-08T00:00:00Z'
  step_seconds: 10
  compression: 1
```

You do not author runtime node ids — each node gets one derived from its
segment, kept short and safe for the routing fabric.

## Segments

A **space segment** references a constellation (or space node set):

```yaml
- id: leo
  source: nodalarc:constellations/earth/leo/earth-leo-ring-36.yaml
```

A **ground segment** places a site set, optionally overlaying scheduling and
other policy onto the nodes it places:

```yaml
- id: ground
  placement:
    from_site_set: nodalarc:site-sets/earth/leo/earth-leo-starlink-pop-sites.yaml
  apply:
    scheduling: { ... }
```

A site is a physical place; the nodes inside it are routers with terminals. One
facility can host several nodes — a Santiago site might carry a LEO Ka gateway
and a MEO gateway as separate nodes with different terminals and policies. The
site, its nodes, and their terminals are catalog primitives the segment
references — you do not write terminals inline in the session.

## Link rules

Link rules define the candidate graph the OME is allowed to evaluate. Each
endpoint selects nodes (`select:`) and terminals (`terminal:`) with set
expressions; a link's class is **derived** from the endpoint roles, not authored.

| Class (derived) | Meaning |
|-----------------|---------|
| access | Body-local ground-to-space access: Earth ground to Earth orbit, or lunar ground to lunar orbit. |
| inter-constellation | Space-to-space links within the same body frame. |
| inter-body relay | Space-to-space relay across body frames, such as Earth relay to lunar relay. |

Topology modes:

| Mode | Meaning |
|------|---------|
| `visible_candidates` | Evaluate every visible candidate under the rule, bounded by candidate limits. |
| `nearest_n` | Build a static candidate set from the nearest `n` endpoint pairs. |
| `explicit_pairs` | Use the exact declared candidate pairs. |

## Ground handoff policy

Ground handoff is a property of a ground node, set in the segment's
`apply.scheduling` (or a per-site override), not the whole session.

A node with one usable terminal must use break-before-make. A node with multiple
compatible terminals can use make-before-break by reserving enough terminal
capacity for overlap. The allocator never invents overlap where terminal capacity
does not exist — a configuration that asks for make-before-break without enough
terminals is reduced to the truthful behavior rather than faked.

How many terminals a node has comes from its catalog node model and how many the
site installs — not from inline session fields.

## Switching sessions

You can switch sessions without restarting the platform:

```bash
make session DEFAULT_SESSION=catalog/nodalarc/sessions/earth-leo-walker.yaml
```

The browser session wizard and YAML upload path use the same resolver as the
command-line deploy. A session the browser accepts is the same shape the runtime
accepts.

## Current limits

NodalArc is intentionally strict. It is better to reject unsupported grammar than
to run an approximation that looks correct. Current limits include:

- The product session format is the segment grammar. Old top-level
  `constellation` / `ground_stations` session files are not a supported product
  path.
- `access` links are body-local. Cross-body connectivity uses an inter-body
  relay.
- The cislunar demos include realistic Earth-Luna range and latency, but
  deep-space protocol conversion (DSN/DTN) is future work.
- Router configuration capture/replay and per-class template overlays are future
  work. Today FRR configs are generated from session YAML and templates.

Those limits are part of the truth contract: if the model cannot represent a
behavior correctly yet, it says so rather than approximating it.
