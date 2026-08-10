# Configuration Guide

NodalArc environments are authored as ordinary YAML catalog objects and a root
session. The session composes reusable objects by reference; the session and
the complete transitive set of referenced YAML files are the configuration
truth deployed to NodalArc.

This page explains how to use that model. For the complete language contract,
including every field, type, allowed value, and constraint, see the
[Configuration Grammar](configuration-grammar.md). The tables, fragments, and
operational notes below are usage guidance, not an independent field list or a
second grammar.

## Catalogs and ownership

NodalArc has two catalog namespaces:

- `nodalarc:` contains shipped, read-only product objects and example sessions.
- `user:` contains user-owned objects and sessions.

Both namespaces have the same families and use the same grammar:

| Object | Family path | Purpose |
|---|---|---|
| Body | `bodies/` | Gravity, radii, and identity of a physical body. |
| Terminal | `terminals/` | RF or optical communication capability and limits. |
| Payload | `payloads/` | Reusable terminal slots and shared resource groups. |
| Profile | `profiles/` | Complete node workload composition: images, containers, adapter, terminal access. |
| Orbit | `orbits/` | Body reference, epoch, geometry, orientation, and propagator. |
| Node | `nodes/` | Reusable forwarding model, ports, and terminal or payload mounts. |
| Site | `sites/` | Facility frame, location, LAN, installed nodes, and concrete addresses. |
| Site set | `site-sets/` | Reusable collection of site references. |
| Constellation | `constellations/` | Generated population from a node, orbit, planes, slots, and phasing. |
| Space node set | `space-node-sets/` | Fixed list of individually identified space nodes. |
| Session | `sessions/` | Deployable composition of segments, links, routing, time, and policy. |

A reference contains its namespace, family, relative path, and lower-case YAML
suffix. For example, the shipped LEO ring is
`nodalarc:constellations/earth/leo/earth-leo-ring-36.yaml`. A user-owned
terminal follows the same shape, such as `user:terminals/rf/my-ka-terminal.yaml`
after that object has been saved in the user catalog.

Each reusable object file has one top-level wrapper such as `terminal:`,
`orbit:`, or `site_set:`. Its `id` matches its file stem. A session is not given
an extra wrapper; its top-level mapping contains `session:` and `segments:`.
YAML mapping order is not significant.

### Use, customize, and save

- **Use** an object by placing its reference in the containing object or
  session.
- **Customize** a shipped object by copying it to a new `user:` path, changing
  the new object's id and content, and updating the containing reference.
- **Save** a session with its references intact. Do not inline the referenced
  object bodies into the session.

Editing a `nodalarc:` object in place is not supported. A session may freely
mix `nodalarc:` and `user:` references.

## A complete session

This is a complete, resolver-valid Earth LEO session composed entirely from
shipped objects. It declares ground scheduling and candidate limits explicitly
because the active ground links and multi-segment candidate graph require them.

```yaml
session:
  name: earth-leo-simple
  display_name: Earth LEO simple
  description: Single 36-satellite LEO ring with MBB-capable Starlink-style gateway sites.

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
      handover_policy:
        hysteresis:
          discount_factor: 1.1
          mask_fade_range_deg: 3.0
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
  - select: {segment: leo}
    terminal: {all: [{role: isl}, {medium: optical}]}
  - select: {segment: leo}
    terminal: {all: [{role: isl}, {medium: optical}]}

simulation:
  candidate_limits:
    max_pairs_per_rule: 500
    max_pairs_per_tick: 2000

time:
  start_time: '2026-06-08T00:00:00Z'
  step_seconds: 1
  compression: 1
```

The same session may instead set its space-segment `source` to a saved
user-owned component such as
`user:constellations/earth/leo/my-leo-shell.yaml`.

The `user:` constellation can in turn reference any valid mix of shipped and
user-owned node, orbit, terminal, payload, and body objects.

## Segments

A segment names a group of runtime nodes. The grammar has three segment forms:

- A **space segment** references a constellation or space node set.
- A **ground segment** places a site set and may apply group or per-site policy.
- A **Lagrange segment** declares one node in a typed Lagrange frame.

```yaml
segments:
- id: leo
  source: nodalarc:constellations/earth/leo/earth-leo-ring-36.yaml
- id: ground
  placement:
    from_site_set: nodalarc:site-sets/earth/leo/earth-leo-starlink-pop-sites.yaml
```

Lagrange segments are structurally defined but not executable by the current
runtime. They fail the runtime-support gate instead of being approximated.

A fixed space node set gives every node exactly one placement: an orbit
reference, an Earth `sgp4_tle` record, or a structurally defined state vector.
The current runtime supports orbit and Earth-TLE placement; raw state-vector
placement remains support-gated.

Runtime node ids are derived from the segment or site placement and the local
node id. Space ids use the segment id; ground ids use the site id and therefore
do not change when the same physical site is placed by another ground segment.
Underscores normalize to hyphens, and the final id must be a unique lower-case
DNS label no longer than 63 characters. Authors do not assign Kubernetes names,
runtime namespaces, or derived interface ids in session YAML.

## Link rules and selectors

A link rule declares which selected endpoint pairs may become physical links.
It does not force connectivity. OME geometry, terminal capability, visibility,
and current time still determine what is possible.

```yaml
link_rules:
- id: leo_access
  topology:
    mode: visible_candidates
  endpoints:
  - select: {all: [{segment: ground}, {tag: leo}]}
    terminal: {all: [{role: access}, {medium: rf}]}
    min_elevation_deg: 25
  - select: {segment: leo}
    terminal: {all: [{role: access}, {medium: rf}]}
```

Node-selector leaves are `segment`, `tag`, `node`, `plane`, and `slot`.
Terminal-selector leaves are `role`, `medium`, and `mount`. Compose them only
with:

- `all` for intersection;
- `any` for union;
- `not` for complement.

Tags that reach resolved nodes are labels for selection only. They never create
links or change physics, routing, addressing, or scheduling. The formal grammar
states which catalog and placement tag fields enter that runtime selector set.

The current runtime executes these topology modes:

| Mode | Meaning |
|---|---|
| `visible_candidates` | All endpoint pairs declared as geometry-tested candidates. |
| `nearest_n` | Greedy physical-distance ranking with degree capped at `n` per node; some nodes may receive fewer links. |
| `explicit_pairs` | The exact undirected node pairs listed in the rule. |

`nearest_visible` is part of the structural language but is not currently
supported. Of the optional link constraints, the current runtime executes
`max_links_per_node`; `max_range_km` and `require_mutual_visibility` are
currently support-gated.

The resolver derives a rule's link class from its endpoints. Do not author a
`class`, `kind`, or link-label field.

For `explicit_pairs`, `a` and `b` are the local node ids inside the rule's two
endpoint segments, not the resolver-derived runtime ids. Ground local ids are
site-qualified, and every pair must remain inside both endpoint selector sets.
For an access rule, the effective ground elevation mask is the stricter of the
selected terminal limit and the authored ground-endpoint `min_elevation_deg`.

## Sites, nodes, terminals, and addresses

A node is a reusable model: a router on a shelf. It has forwarding behavior,
ports, and mounts, but no address or location.

```yaml
node:
  id: starlink-gateway
  display_name: Starlink-style gateway router
  forwarding: routed
  ethernet:
  - id: terr0
  terminals:
  - id: access_ka
    role: access
    terminal: nodalarc:terminals/rf/rf-ka-starlink-ground-gateway.yaml
    count: 64
  payloads: []
```

A site installs that model at a physical facility. The placement owns the LAN,
concrete interface addresses, installed terminal count, and optional
capability narrowing:

```yaml
site:
  id: earth-us-hawthorne
  display_name: Hawthorne Gateway Site
  lan:
    ipv4: 172.16.113.0/24
    ipv6: fd00:da7a:71::/64
  nodes:
  - id: gw1
    model: nodalarc:nodes/ground/starlink-gateway.yaml
    terminals:
      access_ka:
        installed_count: 8
        capabilities:
          boresight: {mode: local_vertical}
    payloads: {}
    interfaces:
      lo0:
        ipv4: 10.255.0.119/32
        ipv6: fd00:da7a:ffff::77/128
      terr0:
        ipv4: 172.16.113.1/24
        ipv6: fd00:da7a:71::1/64
    originated_prefixes:
      ipv4: [172.16.113.0/24]
      ipv6: [fd00:da7a:71::/64]
    tags: [leo]
  frame:
    body_fixed:
      body: nodalarc:bodies/earth.yaml
  location:
    lat_deg: 33.9175
    lon_deg: -118.328111
    alt_m: 20
```

Each placed node authors exactly two numbered interfaces:

- `lo0` is the node loopback.
- `terr0` is the site-LAN interface and must be inside the site's LAN for that
  address family.

Each interface declares IPv4, IPv6, or both. The site's `terminals` mapping is
the exhaustive installation inventory: a model mount omitted from that mapping
has zero installed instances at the site, and `installed_count` cannot exceed
the model count. Capability overrides may narrow the selected terminal but may
not widen it. The required `payloads` mapping follows the same mount-inventory
shape; payload execution is structurally defined but support-gated today.

Installed terminal mounts produce runtime WAN interfaces. Those interfaces are
derived and currently unnumbered; they borrow the node loopback. Do not author
`termN`, `islN`, or other derived WAN interfaces in a site.

For the current substrate, a ground node model declares exactly the `terr0`
Ethernet port and a space node model declares no Ethernet ports. A satellite
access mount declares `boresight: {mode: nadir}` on the node mount; a ground
access mount declares its boresight on the site installation as shown above.

If a site node omits `tenant_id`, the resolver uses `default`. If it omits
`service_priority`, OME uses priority `10` for ground allocation. These are
allocation facts, not authentication, catalog ownership, or storage-isolation
controls.

`originated_prefixes` is explicit routing-injection intent. A LAN is not
advertised merely because it exists. Listing `0.0.0.0/0` or `::/0` explicitly
originates a default route; omitting a prefix means NodalArc does not inject it.

## Workload profiles

A profile is the complete workload composition for one node: the software the
node runs. It declares the container image by registry and digest, the primary
container's command, capabilities, filesystem posture, volumes, mounts, and
resources, optional sidecar containers, terminal access, readiness behavior,
and the adapter that renders per-node native configuration. A routing node's
profile runs one standalone routing stack. An application node's profile is a
plain container with no routing daemon. The complete field list and every
constraint are in the [Configuration Grammar](configuration-grammar.md).

A node acquires its profile from the most specific of three statements:

- the node model declares the default for every node built from that model;
- a segment `profile` overrides the model default for the nodes it resolves;
- a single space node or site node `profile` overrides both.

A resolved node with no profile statement at any level is rejected before
deployment. NodalArc has no default workload and no built-in preference for
any implementation. Inheriting a node-model default is an authored statement,
because someone wrote it into a reviewable catalog object.

Customize a profile like any other catalog object: copy the shipped object to
a new `user:` path, change the command, image, capabilities, or resources, and
reference the new object at the level where it should apply. Forking a profile
requires no platform code.

The installed models predate this part of the language and still reject
`profile` fields structurally; the matching model and resolver change follows
this definition, and worked profile examples land in this guide with it.

## Address pools

Session-level addressing is primarily for generated space nodes. The current
runtime supports loopback pools with `by_node_order` allocation:

```yaml
addressing:
  loopbacks:
  - id: node_loopbacks_v4
    applies_to: {segment: leo}
    ipv4_pool: 10.240.0.0/16
    prefix_length: 32
    allocation: by_node_order
  - id: node_loopbacks_v6
    applies_to: {segment: leo}
    ipv6_pool: fd00:da7a:240::/64
    prefix_length: 128
    allocation: by_node_order
```

When no loopback assignment covers a generated routed space node, the resolver
provides deterministic resolver-owned IPv4 and IPv6 loopbacks. Site-placed
nodes keep their authored loopbacks.

Point-to-point and terrestrial-prefix pools, and allocation modes other than
`by_node_order`, are structurally defined but not currently executable. WAN
interfaces remain unnumbered in the current routing model.

For a dual-stack pool, one `prefix_length` applies to both families. Use
separate assignments when the desired IPv4 and IPv6 prefix lengths differ.

## Routing

Routing is an optional set of disjoint domains. Each explicit domain selects
its nodes and declares its own protocol. The current runtime supports `isis`,
`ospf`, and `static`. BGP is structurally defined but currently rejected by the
runtime-support gate.

```yaml
routing:
  domains:
  - id: earth_domain
    protocol: isis
    capabilities:
      mpls: {}
      segment_routing:
        data_plane: mpls
    selectors:
    - any:
      - segment: leo_a
      - segment: leo_b
      - segment: meo
      - segment: heo_relay
      - segment: geo_relay
      - segment: leo_a_ground
      - segment: leo_b_ground
      - segment: heo_ground
      - segment: geo_ground
    area_assignment:
      strategy: flat
  - id: luna_domain
    protocol: isis
    selectors:
    - any:
      - segment: luna_relay
      - segment: luna_ground
    area_assignment:
      strategy: flat
  boundaries:
  - over: geo_to_luna
    adapter: static_ip
    export:
    - from: earth_domain
      to: luna_domain
      prefixes:
        aggregate_of: originated
      export_node_loopbacks: true
      install_via: peer_loopback
    - from: luna_domain
      to: earth_domain
      prefixes:
        aggregate_of: originated
      export_node_loopbacks: true
      install_via: peer_loopback
```

When an explicit `routing` block is present, every node whose effective
profile renders routing belongs to exactly one domain, and a domain may
select only nodes whose profile's adapter renders its protocol. Nodes whose
profiles render no routing, such as hosts, are never domain members; a host
is reached through the domain of the router serving its network. A fixed link
crossing domain boundaries must have a declared boundary over that link rule. The current runtime supports the `static_ip`
boundary adapter; `bgp` and `dtn_bundle` adapters are support-gated.

IS-IS and OSPF domains may declare MPLS, segment routing, and traffic
engineering capabilities. Static domains carry no IGP capabilities.

OSPF area ids use canonical dotted IPv4 notation such as `0.0.0.0`. IS-IS area
ids use the lower-case hexadecimal dotted form defined by the formal grammar.
The IS-IS-only SPF `holddown_ms` and `time_to_learn_ms` fields are invalid in
an OSPF domain.

With no area assignment, or with `strategy: flat`, the runtime uses
`49.0001` for IS-IS and `0.0.0.0` for OSPF unless `gs_area_id` is supplied.
`per_plane` and `stripe` derive satellite areas and use `gs_area_id` or that
protocol default for ground nodes. Those two strategies require at least one
satellite. Every satellite in a non-flat strategy requires resolved plane
facts. Derived area indexes are limited to `255` for OSPF and `9999` for the
current IS-IS format. A ground-only `explicit` assignment is valid; otherwise
`explicit` requires every selected satellite plane to be mapped exactly once.
Ground mappings use site-qualified local node ids.

If `routing` is omitted, the resolver creates one `default_domain` running
IS-IS over every node whose effective profile's adapter renders IS-IS. Nodes
whose profiles render no routing are not inserted into that default domain.

## Time and ephemeris

Every session declares explicit simulated time:

```yaml
time:
  start_time: '2026-06-08T00:00:00Z'
  step_seconds: 1
  compression: 1
```

`start_time` includes an explicit UTC offset. `step_seconds` advances simulated
time per tick. `compression` is requested simulated seconds per wall-clock
second.

Earth-only sessions do not require an ephemeris block. Every active body other
than Earth must appear in an ephemeris manifest target; Earth state is implicit
in the current runtime. The current runtime supports `skyfield_bsp`; it requires
exactly one local kernel with the declared SHA-256. Its coverage start and end
must be declared together, and the end must be later than the start. The session
start must lie inside those bounds, and playback, seek, or lookahead outside
them fails rather than extrapolating.

## Deploying configuration

The browser, upload API, Builder, Wizard, and Make-driven session path all use
the same backend grammar and shared resolver. Deployment consists of:

1. the persisted root session YAML selected for deployment;
2. every YAML document in its transitive reference closure;
3. the original `nodalarc:` or `user:` reference and relative path for each
   document.

These are ordinary YAML files. NodalArc does not flatten referenced objects
into the session, rewrite `user:` references, or invent a separate deployment
format. A missing, malformed, family-mismatched, or unsupported dependency
blocks deployment before the session is treated as valid runtime state.

Builder and Wizard saves are authored by the backend through the same strict
models. They preserve catalog references and configuration semantics, but they
may normalize key ordering and YAML formatting. Import, reopen, save, and
export do not promise byte-for-byte layout or comment preservation.

For a shipped session on an installed development cluster, use the repository
Make target:

```text
make session DEFAULT_SESSION=catalog/nodalarc/sessions/earth-leo-walker.yaml
```

## Shipped sessions

| Session | Description |
|---|---|
| `earth-leo-simple` | Single 36-satellite LEO ring with gateway sites. |
| `earth-leo-walker` | Walker-delta LEO shell. |
| `earth-leo-polar` | Polar LEO shell with high-latitude gateway sites. |
| `earth-meo-gps` | GPS-altitude MEO shell. |
| `earth-geo-inmarsat` | Representative fixed GEO commercial relay slots. |
| `earth-geo-tdrs` | Representative fixed GEO relay slots. |
| `earth-leo-heo-geo-luna-reachability` | Multi-regime Earth-Luna reachability experiment. |
| `earth-luna-quic` | Earth-to-Luna QUIC application path with host endpoints at both ends. |

The shipped sessions are examples assembled from a larger reusable catalog.
Six omit explicit routing and therefore use the default IS-IS domain. The
Earth-Luna reachability session demonstrates explicit multiple domains and a
`static_ip` boundary. A deployed session may legitimately converge slowly or
remain unreachable; NodalArc does not repair an experimental routing result.
