# Configuration Reference

NodalArc emulations are built from a **catalog of reusable primitives** that you
assemble into a **session**. The session is the only file you deploy; everything
else is a building block it references.

This page explains the model and walks through writing a session. For the formal
grammar of every object — exact fields, allowed values, and EBNF — see the
[Configuration Grammar](configuration-grammar.md).

## The catalog model

Two trees hold all configuration:

- `catalog/nodalarc/` — reusable primitives: bodies, terminals, orbits, nodes,
  sites, site sets, constellations, and space node sets.
- `catalog/nodalarc/sessions/` — assembled, deployable sessions that reference catalog
  primitives.

A primitive is referenced by a token of the form `nodalarc:<path-under-catalog>`.
For example, a session segment points at a constellation with:

```yaml
source: nodalarc:constellations/earth/leo/earth-leo-ring-36.yaml
```

Each primitive file wraps one object whose `id` matches the file name. The same
primitive can be reused by many sessions — a gateway router model placed at
twenty sites, one orbit shared by several constellations. The catalog ships more
primitives than the example sessions use; the extra parts are building blocks,
not dead files.

### The primitives

| Primitive | Folder | What it is |
|-----------|--------|------------|
| Body | `bodies/` | A planet or moon: gravity and radii. Earth and Luna ship today. |
| Terminal | `terminals/` | A physical link capability (an antenna/modem): medium, band or wavelength, range, bandwidth, pointing limits. |
| Orbit | `orbits/` | An orbit around a body: Keplerian elements or a shape, orientation, and propagator. |
| Node | `nodes/` | A router *model* — forwarding behavior, ethernet ports, terminal mounts. Carries **no** addresses and **no** location. |
| Site | `sites/` | A physical facility: a location, a LAN, and one or more nodes placed there with concrete addresses. |
| Site set | `site-sets/` | A named list of sites a session can place as a ground segment. |
| Constellation | `constellations/` | A node model + orbit + plane/slot pattern that generates many satellites. |
| Space node set | `space-node-sets/` | A fixed list of individually placed space nodes (for example, specific GEO slots). |

## Writing a session

A session names itself, declares **segments** (the groups of nodes in play),
declares **link rules** (which segments may connect and how), and sets
addressing, routing, and time. Here is a complete shipped session,
`earth-leo-simple` (scheduling fields trimmed for brevity):

```yaml
session:
  name: earth-leo-simple
  display_name: Earth LEO simple
  description: Single 36-satellite LEO ring with MBB-capable gateway sites.

segments:
- id: leo
  source: nodalarc:constellations/earth/leo/earth-leo-ring-36.yaml
- id: ground
  placement:
    from_site_set: nodalarc:site-sets/earth/leo/earth-leo-starlink-pop-sites.yaml
  apply:
    scheduling:
      selection_policy:
        highest_elevation: {}
      handover_mode: mbb

link_rules:
- id: leo_access
  topology:
    mode: visible_candidates
  endpoints:
  - select:
      all:
      - segment: ground
      - tag: leo
    terminal:
      all:
      - role: access
      - medium: rf
    min_elevation_deg: 25
  - select:
      segment: leo
    terminal:
      all:
      - role: access
      - medium: rf
- id: leo_isl
  topology:
    mode: nearest_n
    n: 2
  endpoints:
  - select: { segment: leo }
    terminal: { all: [ { role: isl }, { medium: optical } ] }
  - select: { segment: leo }
    terminal: { all: [ { role: isl }, { medium: optical } ] }

time:
  start_time: '2026-06-08T00:00:00Z'
  step_seconds: 10
  compression: 1
```

### Segments

A segment is a named group of runtime nodes. There are two kinds:

- **Space segment** — references a constellation or space node set as its
  `source`:

  ```yaml
  - id: leo
    source: nodalarc:constellations/earth/leo/earth-leo-ring-36.yaml
  ```

- **Ground segment** — places a site set, with an optional `apply` overlay for
  scheduling and other ground policy:

  ```yaml
  - id: ground
    placement:
      from_site_set: nodalarc:site-sets/earth/leo/earth-leo-starlink-pop-sites.yaml
    apply:
      scheduling: { ... }
  ```

Every node receives a runtime id derived from its segment, kept short and safe
for the routing fabric. You do not author runtime ids or namespaces.

### Link rules

A link rule declares that nodes selected from two endpoints **may** form links,
and how candidates are chosen. It does not force links — visibility and the
topology mode decide the actual connectivity each tick.

```yaml
link_rules:
- id: leo_access
  topology:
    mode: visible_candidates
  endpoints:
  - select:    { all: [ { segment: ground }, { tag: leo } ] }
    terminal:  { all: [ { role: access }, { medium: rf } ] }
    min_elevation_deg: 25
  - select:    { segment: leo }
    terminal:  { all: [ { role: access }, { medium: rf } ] }
```

Each endpoint has a **node selector** (`select:`) choosing which nodes
participate and a **terminal selector** (`terminal:`) choosing which of their
installed terminals. The link's *class* (access, ISL, and so on) is **derived**
from the endpoints — you never write a `kind`.

#### Selectors

Selectors are set expressions, not loose maps. You compose leaf predicates with
`all` (intersection), `any` (union), and `not` (complement):

- Node leaves: `segment`, `tag`, `node`, `plane`, `slot` (`plane`/`slot` match constellation satellites by their orbital position).
- Terminal leaves: `role`, `medium`, `mount`.

A bare leaf is the whole selector; add an operator when you need more than one:

```yaml
select: { segment: leo }                            # all nodes in segment leo
select: { all: [ { segment: ground }, { tag: leo } ] }   # ground nodes tagged leo
select: { any: [ { segment: leo_a }, { segment: leo_b } ] } # union of two segments
terminal: { all: [ { role: access }, { medium: rf } ] }
```

Tags are author labels used only for selection. They never change physics,
routing, topology, or behavior — they are just a way to name and pick a subset.

#### Topology modes

| Mode | Meaning |
|------|---------|
| `visible_candidates` | Every currently visible pair under the rule is a candidate. |
| `nearest_n` | The `n` nearest endpoint pairs (set `n:`). |
| `explicit_pairs` | Exactly the pairs you list. |

(`nearest_visible` is reserved in the grammar but not accepted at runtime yet.)

### Addressing

Satellites are generated by a constellation, so they are not individually
authored — their loopback and point-to-point addresses are handed out from
session-level pools in the `addressing` block:

```yaml
addressing:
  loopbacks:
  - id: node_loopbacks
    applies_to: { any: [ { segment: leo_a }, { segment: leo_b } ] }
    ipv4_pool: 10.255.0.0/16
    prefix_length: 32
    allocation: by_node_order
  point_to_point:
  - id: p2p_links
    applies_to: { segment: leo_a }
    ipv4_pool: 10.128.0.0/12
    prefix_length: 31
    allocation: by_attach_index
```

Ground nodes carry their own addresses on their site (see below), so simple
single-shell sessions need no `addressing` block at all.

### Routing

A session is multi-protocol by construction. Routing is a set of **domains**, and
each domain runs its own protocol over the nodes it selects — so one session can
carry an IS-IS backbone, an OSPF region, BGP at an edge, and static stubs at the
same time. A domain's protocol is one of `isis`, `ospf`, `bgp`, or `static`, and
it may enable capabilities such as MPLS, segment routing, or traffic engineering.
**Boundaries** join domains and redistribute prefixes between them. A session with
no `routing` block defaults to a single IS-IS domain over all of its nodes.

```yaml
routing:
  domains:
  - id: earth_domain
    protocol: isis
    selectors:
    - any: [ { segment: leo }, { segment: ground } ]
    area_assignment:
      strategy: flat
  - id: edge_domain
    protocol: ospf
    selectors:
    - { segment: edge }
  boundaries:
  - over: earth_to_edge          # the link rule this boundary rides
    adapter: static_ip
    export:
    - from: earth_domain
      to: edge_domain
      prefixes: { aggregate_of: originated }
```

### Time

```yaml
time:
  start_time: '2026-06-08T00:00:00Z'   # simulation epoch (UTC)
  step_seconds: 10                     # seconds of sim time per tick
  compression: 1                       # sim seconds per wall second (1 = real time)
```

## Sites, nodes, terminals, and addressing

This is the part that is easy to get wrong, so it is worth stating plainly.

A **node model** is a template — a router on a shelf. It declares forwarding
behavior, ethernet ports, and terminal mounts. It has **no IP addresses and no
location**:

```yaml
node:
  id: leo-gateway
  display_name: Earth LEO gateway router
  forwarding: routed
  ethernet:
  - id: terr0
  terminals:
  - id: access_ka
    role: access
    terminal: nodalarc:terminals/rf/rf-ka-leo-access.yaml
    count: 8
  payloads: []
```

A **site** is a facility with a LAN. Placing a node model into a site creates a
SiteNode — the node *as installed here* — and that is where addresses live:

```yaml
site:
  id: earth-au-perth
  lan:
    ipv4: 172.16.6.0/24
    ipv6: fd00:da7a:6::/64
  nodes:
  - id: leo-gateway
    model: nodalarc:nodes/ground/leo-gateway.yaml
    terminals:
      access_ka:
        installed_count: 3        # how many of the model's 8 mounts are installed here
    interfaces:
      lo0:   { ipv4: 10.255.0.7/32,  ipv6: 'fd00:da7a:ffff::7/128' }
      terr0: { ipv4: 172.16.6.1/24,  ipv6: 'fd00:da7a:6::1/64' }
    originated_prefixes:
      ipv4: [ 172.16.6.0/24, 0.0.0.0/0 ]
      ipv6: [ 'fd00:da7a:6::/64' ]
    tags: [ leo ]
  location:
    lat_deg: -31.9523
    lon_deg: 115.8613
    alt_m: 32
  frame:
    body_fixed:
      body: nodalarc:bodies/earth.yaml
```

The placement, not the model, owns the addresses. Move the same model to another
site and it takes that site's scheme — exactly like re-addressing a real router
when you relocate it.

A SiteNode has exactly two **numbered** interfaces:

- `lo0` — the node's loopback.
- `terr0` — the site-LAN interface; its address sits inside the site's `lan`.

Installed terminals become additional `termN` interfaces at runtime — one per
installed terminal. These are WAN and point-to-point, and in the current routing
model they are **unnumbered**: they borrow `lo0` instead of carrying their own
address, because a ground terminal's peer satellite changes on every handover and
a numbered interface would have to be re-addressed each time. You do not author `termN` interfaces; they come from
the installed terminal mounts. A terminal is an interface, never a router — it
has no loopback of its own.

### Advertising prefixes

A network existing does not advertise it. To inject something into the routing
protocol, list it in a node's `originated_prefixes`:

```yaml
originated_prefixes:
  ipv4:
  - 172.16.6.0/24      # advertise this node's LAN
  - 0.0.0.0/0          # advertise a default route
  ipv6:
  - fd00:da7a:6::/64
```

`originated_prefixes` is routing-injection intent and nothing else: list the LAN
to advertise the LAN, list a default to advertise a default. Anything you do not
list is not advertised. Each IP family is independent.

## Deploying a session

- **Browser wizard** — build and launch from the UI.
- **Upload** — paste or upload session YAML.
- **Command line** — `make session DEFAULT_SESSION=catalog/nodalarc/sessions/earth-leo-walker.yaml`.

Every path resolves the same session through the same resolver, so a session that
deploys from the command line behaves identically in the wizard. If a session
cannot be resolved, deployment fails before any pods are treated as valid runtime
state.

## Shipped sessions

| Session | Description |
|---------|-------------|
| `earth-leo-simple` | Single 36-satellite LEO ring with MBB-capable gateway sites |
| `earth-leo-walker` | Walker-delta LEO shell |
| `earth-leo-polar` | Polar LEO shell with high-latitude gateway sites |
| `earth-meo-gps` | GPS-altitude MEO shell |
| `earth-geo-inmarsat` | Representative GEO commercial-relay-style shell |
| `earth-geo-tdrs` | Representative GEO relay/TDRS-style shell |
| `earth-leo-heo-geo-luna-reachability` | Multi-regime LEO, HEO, GEO, lunar relay, and lunar ground reachability |

The example sessions are routing-light: six declare no `routing` and fall back to
the single-IS-IS default, while `reachability` shows the multi-domain shape —
separate Earth and Luna IS-IS domains joined by a redistribution boundary. The
routing model mixes protocols per domain (see Routing above); the examples simply
do not exercise every combination.

The catalog contains more reusable primitives than these sessions use. That is
intentional: the sessions are the examples; the catalog parts are the building
blocks.
