# Configuration Grammar

This is the grammar for the files you author: the catalog primitives and the
session that assembles them. It is a reference — for a guided walkthrough and
worked examples, start with the [Configuration Reference](configuration.md).

It covers the common authoring surface. A few advanced and forward-looking
constructs are omitted for clarity — payload mounts, Lagrange-anchored sites and
segments, raw state-vector placement, per-segment clocks, and ephemeris/dispatch
tuning — none of which the shipped sessions use.

## Notation

```
::=          is defined as
A | B        A or B (choose one)
[ A ]        A is optional
A*           zero or more A
A+           one or more A
"text"       a literal key or value
Ref(kind)    a nodalarc: token pointing at a <kind> primitive,
             e.g. nodalarc:bodies/earth.yaml — or an inline <kind> object
Integer / Number / String / Bool / URL / CIDR / Identifier
             scalar types (Identifier is lower-case, digits, '-' and '_')
```

Every primitive file contains exactly one top-level object, keyed by its type
(`body:`, `terminal:`, `orbit:`, `node:`, `site:`, `site_set:`,
`constellation:`, `space_node_set:`). The object's `id` must equal the file name
(without `.yaml`).

## References

Any field typed `Ref(kind)` accepts either a catalog token or an inline object of
that kind. A token resolves a primitive file:

```
Reference ::= "nodalarc:" Path | InlineObject
```

```yaml
orbit: nodalarc:orbits/earth/leo/earth-leo-starlink.yaml   # token
# or the same orbit written inline in place of the token
```

---

## Body

```
Body ::= {
  "id": Identifier,
  "display_name": String,
  "gravitational_parameter_km3_s2": Number,
  "mean_radius_km": Number,
  "equatorial_radius_km": Number,
  "polar_radius_km": Number,
  "reference": URL,
  "notes": String
}
```

| Field | Meaning |
|-------|---------|
| `gravitational_parameter_km3_s2` | Standard gravitational parameter (GM). |
| `mean_radius_km` / `equatorial_radius_km` / `polar_radius_km` | Body radii. |

---

## Terminal

A physical link capability that becomes an interface when installed on a node.

```
Terminal ::= {
  "id": Identifier,
  "display_name": String,
  "medium": Medium,
  "signal": RfSignal | OpticalSignal,
  "bandwidth_mbps": { "transmit": Number, "receive": Number },
  "tracking_capacity": Integer,
  "max_range_km": Number,
  "limits": Limits,
  "reference": URL,
  [ "notes": String ]
}

RfSignal      ::= { "band": Identifier, "frequency_hz": Number }   # band is a free label (e.g. ka, ku, s), not a closed set
OpticalSignal ::= { "wavelength_nm": Number }
Limits        ::= {
  "azimuth_deg":   { "min": Number, "max": Number },
  "elevation_deg": { "min": Number, "max": Number },
  "max_tracking_rate_deg_s": Number
}
```

`signal` is `RfSignal` when `medium` is `rf`, `OpticalSignal` when `optical`.
`tracking_capacity` is how many peers the terminal tracks at once.

---

## Orbit

```
Orbit ::= {
  "id": Identifier,
  "central_body": Ref(body),
  "epoch": String,
  ( "elements": Elements | "shape": Shape ),     # exactly one
  "orientation": Orientation,
  "phase": { "mean_anomaly_deg": Number },
  "propagator": Propagator,
  "reference": URL,
  [ "notes": String ]
}

Elements    ::= { "semi_major_axis_km": Number, "eccentricity": Number }
Shape       ::= { "altitude_km": Number }
              | { "perigee_altitude_km": Number, "apogee_altitude_km": Number }
Orientation ::= {
  "inclination_deg": Number,
  "raan_deg": Number,
  "argument_of_perigee_deg": Number
}
```

Use `elements` for an explicit Keplerian orbit, or `shape` for a circular
(`altitude_km`) or eccentric (`perigee` + `apogee`) orbit. A non-circular orbit
needs a propagator that models it (see Allowed Values).

---

## Node (router model)

A reusable template. It declares ports and terminal mounts and carries **no**
addresses and **no** location.

```
Node ::= {
  "id": Identifier,
  "display_name": String,
  "forwarding": Forwarding,
  "ethernet": EthernetPort+,
  "terminals": TerminalMount*,
  "payloads": [ ],
  [ "tags": Identifier* ],
  [ "reference": URL ],
  [ "notes": String ]
}

EthernetPort  ::= { "id": Identifier }
TerminalMount ::= {
  "id": Identifier,
  "role": Role,
  "terminal": Ref(terminal),
  "count": Integer
}
```

`count` is how many of that terminal the model *can* carry; a site chooses how
many to install. `role` is authored here and is closed; the class of any link is
derived from the roles at its endpoints.

---

## Site

A physical facility with a LAN and one or more placed nodes. This is where
addresses live.

```
Site ::= {
  "id": Identifier,
  [ "display_name": String ],
  [ "verified": Verified ],
  "lan": Lan,
  "nodes": SiteNode+,
  "frame": { "body_fixed": { "body": Ref(body) } },
  "location": { "lat_deg": Number, "lon_deg": Number, "alt_m": Number },
  [ "tags": Identifier* ]
}

Lan ::= { [ "ipv4": CIDR ], [ "ipv6": CIDR ] }     # at least one family

SiteNode ::= {
  "id": Identifier,
  [ "display_name": String ],
  "model": Ref(node),
  "terminals": { Identifier: TerminalInstall }*,   # keyed by mount id
  "payloads": { },
  "interfaces": { "lo0": InterfaceAddress, "terr0": InterfaceAddress },
  [ "originated_prefixes": OriginatedPrefixes ],
  [ "scheduling": Scheduling ],          # same shape as a ground segment's apply.scheduling
  [ "tenant_id": Identifier ],
  [ "service_priority": Integer ],
  [ "tags": Identifier* ]
}

TerminalInstall  ::= { "installed_count": Integer, [ "capabilities": Capabilities ], [ "tags": Identifier* ] }
InterfaceAddress ::= { [ "ipv4": CIDR ], [ "ipv6": CIDR ] }
OriginatedPrefixes ::= { [ "ipv4": CIDR* ], [ "ipv6": CIDR* ] }
```

- `interfaces` holds only the two **numbered** placement interfaces: `lo0` (the
  node loopback) and `terr0` (the site-LAN interface, whose address must sit
  inside `lan` for each family). Terminal-facing `termN` interfaces are derived
  from installed terminals at runtime, are point-to-point and unnumbered, and are
  not authored here.
- `terminals` keys are mount ids from the node model; `installed_count` must not
  exceed the model mount's `count`. `capabilities` may narrow the model terminal
  (lower range, tighter mask) but never widen it.
- `originated_prefixes` lists the prefixes this node injects into routing — the
  LAN, a default (`0.0.0.0/0` / `::/0`), or any other network. Anything not
  listed is not advertised. Each family is independent.

---

## Site set

```
SiteSet ::= {
  "id": Identifier,
  [ "display_name": String ],
  "sites": Ref(site)+,
  [ "tags": Identifier* ],
  [ "reference": URL ],
  [ "notes": String ]
}
```

---

## Constellation

Generates many satellites from one node model, one orbit, and a plane/slot
pattern.

```
Constellation ::= {
  "id": Identifier,
  [ "display_name": String ],
  "node": Ref(node),
  "orbit": Ref(orbit),
  "planes": { "count": Integer, "raan_spacing_deg": Number },
  "slots_per_plane": Integer,
  "phasing": { "mode": PhasingMode, [ "phase_offset_deg": Number ] },
  "node_tags": NodeTagRule*,
  [ "tags": Identifier* ],
  [ "reference": URL ],
  [ "notes": String ]
}

NodeTagRule ::= {
  "tag": Identifier,
  [ "planes": Integer* ], [ "slots": Integer* ], [ "node_ids": Identifier* ]
}
```

Total satellites = `planes.count` × `slots_per_plane`. A `NodeTagRule` with no
filter (`{ tag: all }`) tags every generated satellite; filters restrict the tag
to specific planes, slots, or ids.

---

## Space node set

A fixed list of individually placed space nodes.

```
SpaceNodeSet ::= { "id": Identifier, "nodes": SpaceNode+, [ "tags": Identifier* ] }
SpaceNode    ::= { "id": Identifier, "node": Ref(node), "orbit": Orbit, [ "tags": Identifier* ] }
```

---

## Session

The deployable file. It assembles segments and declares connectivity, routing,
and time.

```
Session ::= {
  "session": { "name": Identifier, [ "display_name": String ], [ "description": String ] },
  "segments": Segment+,
  [ "link_rules": LinkRule* ],
  [ "addressing": Addressing ],
  [ "routing": Routing ],
  [ "simulation": Simulation ],
  [ "time": Time ]
}
```

### Segments

```
Segment       ::= SpaceSegment | GroundSegment

SpaceSegment  ::= {
  "id": Identifier,
  "source": Ref(constellation | space_node_set),
  [ "tags": Identifier* ]
}

GroundSegment ::= {
  "id": Identifier,
  "placement": { "from_site_set": Ref(site_set) },
  [ "apply": GroundApply ],
  [ "overrides": GroundOverride* ]
}

GroundApply   ::= {
  [ "scheduling": Scheduling ],
  [ "originated_prefixes": OriginatedPrefixes ],
  [ "tags": Identifier* ]
}
```

`apply` overlays policy onto every node placed by the segment; `overrides` target
specific sites. Scheduling fields (selection policy, handover mode, and the
allocator-wide ordering fields) are detailed in the Configuration Reference.

### Link rules

```
LinkRule ::= {
  "id": Identifier,
  "topology": Topology,
  "endpoints": [ Endpoint, Endpoint ]      # exactly two
}

Endpoint ::= {
  "select": NodeSelector,
  "terminal": TerminalSelector,
  [ "min_elevation_deg": Number ]
}

Topology ::= { "mode": "visible_candidates" }
           | { "mode": "nearest_n", "n": Integer }
           | { "mode": "explicit_pairs", "pairs": Pair+ }
           | { "mode": "nearest_visible" }    # grammar-defined, but rejected at runtime today
```

The link's class is derived from the endpoint roles — there is no `kind` to
author.

### Selectors

Selectors are set expressions. A bare leaf is a complete selector; operators
compose leaves. Maps and lists never imply an implicit AND/OR — only `all`,
`any`, and `not` combine.

```
NodeSelector ::= NodeLeaf
               | { "all": NodeSelector+ }     # intersection
               | { "any": NodeSelector+ }     # union
               | { "not": NodeSelector }      # complement
NodeLeaf     ::= { "segment": Identifier } | { "tag": Identifier } | { "node": Identifier }
               | { "plane": Integer } | { "slot": Integer }     # plane/slot apply to constellation nodes

TerminalSelector ::= TerminalLeaf
               | { "all": TerminalSelector+ } | { "any": TerminalSelector+ } | { "not": TerminalSelector }
TerminalLeaf     ::= { "role": Role } | { "medium": Medium } | { "mount": Identifier }
```

Tags participate only as set membership. A tag's text never carries behavior —
it does not set class, physics, routing, or scheduling. It only names a subset to
select.

### Addressing

Assigns loopback and point-to-point addresses from pools, by an allocation order,
to the nodes a selector covers. Used mainly for generated satellites; ground
nodes carry their own addresses on their site.

```
Addressing ::= {
  [ "loopbacks":      AddressPool* ],
  [ "point_to_point": AddressPool* ],
  [ "terrestrial_prefixes": AddressPool* ]
}

AddressPool ::= {
  "id": Identifier,
  "applies_to": NodeSelector,
  [ "ipv4_pool": CIDR ], [ "ipv6_pool": CIDR ],
  [ "prefix_length": Integer ],
  [ "allocation": Allocation ]
}
```

### Routing

```
Routing ::= {
  "domains": RoutingDomain+,
  [ "boundaries": RoutingBoundary* ]
}

RoutingDomain ::= {
  "id": Identifier,
  "protocol": Protocol,
  [ "capabilities": Capabilities ],
  "selectors": NodeSelector+,
  [ "area_assignment": { "strategy": AreaStrategy } ],
  [ "timers": RoutingTimers ]          # isis/ospf domains only
}

RoutingTimers ::= {                    # engine defaults apply when omitted
  [ "hello_interval_s": Integer ],     # default 1
  [ "hold_interval_s": Integer ],      # default 3; must exceed hello
  [ "spf": SpfThrottle ],
  [ "bfd": BfdConfig ]
}
SpfThrottle ::= {                      # IETF SPF backoff, milliseconds
  [ "init_delay_ms": Integer ], [ "short_delay_ms": Integer ],
  [ "long_delay_ms": Integer ], [ "holddown_ms": Integer ],
  [ "time_to_learn_ms": Integer ]
}
BfdConfig ::= {
  "enabled": Bool,                     # default false
  [ "detect_multiplier": Integer ],
  [ "rx_interval_ms": Integer ], [ "tx_interval_ms": Integer ]
}

RoutingBoundary ::= {
  "over": Identifier,            # the link rule this boundary rides
  "adapter": Adapter,
  "export": ExportRule+
}
ExportRule ::= {
  "from": Identifier, "to": Identifier,
  "prefixes": ( CIDR* | { "aggregate_of": "originated" } ),
  [ "export_node_loopbacks": Bool ],
  [ "install_via": "peer_loopback" | String ]
}
```

A session is multi-protocol by construction: each domain runs its own protocol
over the nodes its `selectors` cover, so one session can carry IS-IS, OSPF, BGP,
and static domains at once. Boundaries join two domains and redistribute prefixes
between them.

### Simulation and time

```
Simulation ::= {
  "candidate_limits": { "max_pairs_per_rule": Integer, "max_pairs_per_tick": Integer }
}

Time ::= {
  "start_time": String,        # UTC simulation epoch
  "step_seconds": Number,      # sim seconds per tick (> 0)
  "compression": Number        # sim seconds per wall second (> 0; 1 = real time)
}
```

---

## Allowed values

Fields below are closed: only the listed values are valid.

| Field | Values |
|-------|--------|
| `medium` (terminal, selector) | `rf`, `optical` |
| `role` (mount, selector) | `access`, `isl`, `crosslink`, `backbone` |
| `forwarding` (node) | `routed`, `host`, `bridge`, `control_only` |
| `propagator` (orbit) | `two_body`, `j2_mean_elements`, `sgp4_tle` |
| `mode` (phasing) | `walker_delta`, `walker_star`, `evenly_spaced_mean_anomaly` |
| `mode` (topology) | `visible_candidates`, `nearest_n`, `explicit_pairs` (`nearest_visible` is grammar-defined but rejected at runtime today) |
| `protocol` (routing domain) | `isis`, `ospf`, `bgp`, `static` (a session with no routing defaults to one `isis` domain) |
| `adapter` (routing boundary) | `static_ip`, `bgp`, `dtn_bundle` |
| `strategy` (area assignment) | `flat`, `per_plane`, `stripe`, `explicit` |
| `allocation` (address pool) | `by_node_order`, `by_attach_index`, `by_plane_slot`, `by_ground_index` |
| selection policy (scheduling) | `highest_elevation`, `lowest_elevation`, `longest_remaining_pass` |
| handover mode (scheduling) | `mbb`, `bbm` |

All values are lower-case `snake_case`. A value outside its list is an error, not
a silent fallback.
