# Sessions

A session is a running network experiment. It defines which nodes exist, where
they are placed, which links are allowed to become candidates, and what routing
runs inside the nodes.

The simplest session is a single LEO constellation with Earth gateway sites —
the right place to start. The same grammar also assembles multiple orbital
regimes, lunar segments, relay nodes, and body-specific ground sites when an
experiment needs them.

This page is the conceptual tour. See the
[Configuration Guide](../ops/configuration.md) for the authoring workflow and
the [Configuration Grammar](../ops/configuration-grammar.md) for every field,
type, allowed value, and constraint.

## The session model

A session is assembled from reusable catalog building blocks:

- **segments** — groups of nodes: a *space* segment (a constellation or space
  node set) or a *ground* segment (a set of sites).
- **link rules** — which segment endpoints may form candidate links, and how
  candidates are chosen.
- **addressing** — address pools for generated nodes (satellites).
- **routing** — one or more routing domains over selected nodes, with boundaries
  redistributing between them. The grammar defines `isis`, `ospf`, `bgp`, and
  `static`; the current runtime executes `isis`, `ospf`, and `static` and rejects
  `bgp` before deployment. A supported session can mix protocols.
- **workload profiles** — what each node runs: the catalog profile carrying its
  complete container composition, taken from the node model, its segment, or
  the node's own entry.
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
| `earth-luna-quic.yaml` | Earth-to-Luna application path: a QUIC client host on an Earth site reaches a QUIC server host on the lunar surface through LEO, GEO, and cislunar relay hops. |

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
      handover_policy:
        hysteresis:
          discount_factor: 1.1
          mask_fade_range_deg: 3
      handover_mode: mbb
      mbb_overlap_ticks: 30
      mbb_reserve: 1
      handover_concurrency: one_at_a_time
      ranking_order:
      - service_priority
      - selection_score
      - satellite_ground_terminal_capacity
      - lex_pair
      mbb_preemption: 'off'
      successor_abort_policy: hard_release
      cross_tenant_displacement: 'off'
      bbm_acquire_timeout_ticks: 1

link_rules:
- id: leo_access
  topology: { mode: visible_candidates }
  endpoints:
  - select:   { all: [ { segment: ground }, { tag: leo } ] }
    terminal: { all: [ { role: access }, { medium: rf } ] }
    min_elevation_deg: 25
  - select:   { segment: leo }
    terminal: { all: [ { role: access }, { medium: rf } ] }

simulation:
  candidate_limits:
    max_pairs_per_rule: 500
    max_pairs_per_tick: 2000

time:
  start_time: '2026-06-08T00:00:00Z'
  step_seconds: 10
  compression: 1
```

You do not author runtime node ids directly. Space-node ids are derived from
the normalized space-segment id and local space-node id. Ground-node ids are
derived from the normalized catalog site id and its site-owned node id, so a
ground node keeps the same runtime identity regardless of which ground segment
places its site. All runtime ids must be globally unique, lower-case DNS-label
safe, and no longer than 63 characters.

## Segments

A **space segment** references a constellation (or space node set):

```yaml
- id: leo
  source: nodalarc:constellations/earth/leo/earth-leo-ring-36.yaml
```

A **ground segment** places a site set. It may overlay tags, originated-prefix
intent, and scheduling onto the nodes it places:

```yaml
- id: ground
  placement:
    from_site_set: nodalarc:site-sets/earth/leo/earth-leo-starlink-pop-sites.yaml
```

When placed ground nodes participate in enabled access-link candidates, their
effective scheduling must supply every field shown in the complete example.

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
| `nearest_n` | Rank pairs by physical distance and greedily cap each selected node at `n` candidates; a node may receive fewer when a peer's cap is already full. |
| `explicit_pairs` | Use the exact declared candidate pairs. |

## Ground handoff policy

Ground handoff is a property of a ground node. Segment `apply.scheduling`
supplies the base policy, a ground override may replace it for one site, and
site-node scheduling may override individual fields.

A node with only one unit of usable access-terminal capacity must use
break-before-make. Make-before-break requires enough installed access-terminal
tracking capacity for the active link plus the declared `mbb_reserve`; the
current runtime supports one reserved overlap. An explicit MBB configuration
with insufficient capacity is rejected before deployment. NodalArc neither
invents the overlap nor silently reduces the requested policy to BBM.

How many terminals a node has comes from its catalog node model and how many the
site installs — not from inline session fields.

## Workload profiles

Every node runs a declared workload. A profile is the complete composition for
one node and may hold several cooperating containers: an FRR router with an
observer beside it is one profile, and a QUIC client host is another. The node
model names a default profile, a segment can override that default for its
nodes, and a single node's own entry can override both. A session in which any
node ends up with no profile at any level is rejected at load. NodalArc never
assumes a workload.

Profiles keep implementation detail out of the session. The session says which
profile a node runs; the profile and its adapter own the native configuration.
Session YAML never contains vendor configuration syntax.

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
