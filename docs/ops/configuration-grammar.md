# Configuration Grammar

This document is the sole public formal definition of the persisted NodalArc
YAML language: the reusable objects in both catalog namespaces and the session
that composes them. The strict backend models implement the structural grammar.
The shared resolver loads references, validates relationships across objects,
applies the current runtime-support gate, and produces the immutable runtime
model. The Configuration Guide explains how to use this language; it does not
define another one.

For a practical introduction and complete examples, see the
[Configuration Guide](configuration.md).

## Validation pipeline

Every deployment follows one interpretation path:

```text
YAML text
  -> strict YAML data model
  -> structural grammar in this document
  -> catalog reference resolution
  -> cross-object semantic validation
  -> current-runtime support validation
  -> resolved session
```

The YAML loader accepts one document and rejects duplicate mapping keys before
structural validation. Unknown mapping keys, wrong scalar kinds, invalid closed
values, malformed references, dangling references, family mismatches, and
invalid relationships are errors. Runtime-future constructs are part of the
structural language only where they are listed here; the runtime must reject
them explicitly rather than ignore or approximate them.

## Formal notation

The productions use the notation defined by ISO/IEC 14977 EBNF: `=` defines a
production, `;` terminates it, `,` concatenates terms, `|` separates
alternatives, `[...]` is optional, `{...}` repeats zero or more times, quoted
text is a terminal, and `?...?` is a special sequence whose recognition is
delegated to the named scalar validator.

YAML mappings are unordered, so applying a character-stream grammar directly
to YAML source would incorrectly make field order significant. The EBNF below
instead operates on a canonical token projection of the parsed YAML data
model:

- a mapping projects to `MappingBegin`, then each present key and value in the
  order stated by its production, then `MappingEnd`;
- a mapping whose production permits dynamic keys projects those entries in
  lexicographic key order;
- a sequence projects to `SequenceBegin`, its values in authored order, then
  `SequenceEnd`;
- mapping keys and closed string values project as their quoted terminal;
- other scalar nodes project as one token recognized by the applicable scalar
  production; and
- projection fails before EBNF recognition if a mapping contains an unlisted
  key. Duplicate keys have already failed YAML loading.

This projection is a definition used to state the language precisely. It is
not a second persisted format, a deployment artifact, or a requirement to
reorder YAML source. These base productions define the projection markers:

```ebnf
MappingBegin  = ? beginning of a YAML mapping in the canonical projection ? ;
MappingEnd    = ? end of a YAML mapping in the canonical projection ? ;
SequenceBegin = ? beginning of a YAML sequence in the canonical projection ? ;
SequenceEnd   = ? end of a YAML sequence in the canonical projection ? ;
```

EBNF is context-free and cannot express numeric comparisons, uniqueness,
reference resolution, graph acyclicity, set membership, or runtime support.
Those requirements are stated as normative semantic constraints after the
relevant productions. The executable models and shared resolver enforce both
the EBNF structure and those semantic constraints.

## Scalar types

```ebnf
Null               = ? YAML null scalar ? ;
Boolean            = ? YAML boolean scalar ? ;
String             = ? YAML string scalar ? ;
Identifier         = ? YAML string matching [a-z0-9][a-z0-9_-]* in full ? ;
RoutingAreaId      = ? nonempty YAML string matching [0-9a-f]+(\.[0-9a-f]+)* in full ? ;
NonEmptyToken      = ? nonempty YAML string containing no whitespace ? ;
Integer            = ? YAML integer scalar, excluding booleans ? ;
PositiveInteger    = ? Integer greater than zero ? ;
NonNegativeInteger = ? Integer greater than or equal to zero ? ;
FiniteNumber       = ? finite YAML integer or floating-point scalar, excluding booleans ? ;
PositiveNumber     = ? FiniteNumber greater than zero ? ;
NonNegativeNumber  = ? FiniteNumber greater than or equal to zero ? ;
Url                = ? YAML string accepted by the backend URL validator ? ;
AwareTimestamp     = ? ISO 8601 YAML string with an explicit UTC offset ? ;
IPv4Network        = ? canonical IPv4 network string in CIDR notation with no host bits ? ;
IPv6Network        = ? canonical IPv6 network string in CIDR notation with no host bits ? ;
IpNetwork          = IPv4Network | IPv6Network ;
IPv4Interface      = ? IPv4 interface string in CIDR notation ? ;
IPv6Interface      = ? IPv6 interface string in CIDR notation ? ;
RelativeAssetPath  = ? contained relative forward-slash path accepted by the backend validator ? ;
Sha256Hex          = ? lower-case YAML string containing exactly 64 hexadecimal digits ? ;
RegistryHost       = ? registry host with optional port accepted by the backend validator ? ;
PinnedImage        = ? image repository path pinned by one @sha256 digest, accepted by the backend validator ? ;
MountPath          = ? normalized absolute forward-slash container path accepted by the backend validator ? ;
EnvName            = ? YAML string matching [A-Za-z_][A-Za-z0-9_]* in full ? ;
Tags               = SequenceBegin, { Identifier }, SequenceEnd ;
```

All identifiers and closed string values are case-sensitive. Tag values are
unique within each `Tags` sequence. `RelativeAssetPath` is nonempty and rejects
roots, backslashes, repeated separators, trailing separators, and empty, dot,
or parent components. `PinnedImage` names an image by repository path and
SHA-256 digest; a mutable tag is not part of the language. `MountPath` is
absolute, is not `/`, rejects trailing or repeated separators and dot or
parent segments, and rejects the reserved `/proc`, `/sys`, `/dev`, and
`/var/run/secrets` trees.

## Catalog references and documents

Persisted catalog-valued fields contain references only. Inline copies of
catalog objects are not part of this language.

```ebnf
BodyRef          = ? catalog reference accepted by BodyRef ? ;
TerminalRef      = ? catalog reference accepted by TerminalRef ? ;
PayloadRef       = ? catalog reference accepted by PayloadRef ? ;
ProfileRef       = ? catalog reference accepted by ProfileRef ? ;
OrbitRef         = ? catalog reference accepted by OrbitRef ? ;
NodeRef          = ? catalog reference accepted by NodeRef ? ;
SiteRef          = ? catalog reference accepted by SiteRef ? ;
SiteSetRef       = ? catalog reference accepted by SiteSetRef ? ;
ConstellationRef = ? catalog reference accepted by ConstellationRef ? ;
SpaceNodeSetRef  = ? catalog reference accepted by SpaceNodeSetRef ? ;
SessionRef       = ? catalog reference accepted by SessionRef ? ;
SpaceSourceRef   = ConstellationRef | SpaceNodeSetRef ;
```

Each reference is one YAML string with this exact shape:

```text
(nodalarc|user):<family>/<component>(/<component>)*.(yaml|yml)
```

`<component>` matches `[a-z0-9][a-z0-9_-]*`. The required family is `bodies`,
`terminals`, `payloads`, `profiles`, `orbits`, `nodes`, `sites`, `site-sets`,
`constellations`, `space-node-sets`, or `sessions`, as selected by the typed
reference production. Absolute paths, backslashes, empty components, dot
components, and parent traversal are invalid.

`nodalarc:` identifies shipped, read-only product content. `user:` identifies
user-owned content. Both namespaces use the same families and grammar. To
customize a shipped object, create a new `user:` object and change the
containing reference; never edit the `nodalarc:` object in place.

Every reusable object document has exactly one wrapper. A session document is
the unwrapped `Session` mapping. `ConfigurationDocument` is the start
production for one persisted YAML document.

```ebnf
ConfigurationDocument = BodyDocument
                      | TerminalDocument
                      | PayloadDocument
                      | ProfileDocument
                      | OrbitDocument
                      | NodeDocument
                      | SiteDocument
                      | SiteSetDocument
                      | ConstellationDocument
                      | SpaceNodeSetDocument
                      | Session ;

BodyDocument          = MappingBegin, "body", Body, MappingEnd ;
TerminalDocument      = MappingBegin, "terminal", Terminal, MappingEnd ;
PayloadDocument       = MappingBegin, "payload", Payload, MappingEnd ;
ProfileDocument       = MappingBegin, "profile", Profile, MappingEnd ;
OrbitDocument         = MappingBegin, "orbit", Orbit, MappingEnd ;
NodeDocument          = MappingBegin, "node", Node, MappingEnd ;
SiteDocument          = MappingBegin, "site", Site, MappingEnd ;
SiteSetDocument       = MappingBegin, "site_set", SiteSet, MappingEnd ;
ConstellationDocument = MappingBegin, "constellation", Constellation, MappingEnd ;
SpaceNodeSetDocument  = MappingBegin, "space_node_set", SpaceNodeSet, MappingEnd ;
```

When a document is loaded through a catalog reference, its object `id` must
equal the referenced file stem. For a referenced session, `session.name` must
equal the file stem. Every reference must resolve in its declared namespace and
family, and the complete transitive reference graph must be acyclic.

## Body

```ebnf
Body = MappingBegin,
       "id", Identifier,
       "display_name", String,
       "gravitational_parameter_km3_s2", PositiveNumber,
       "mean_radius_km", PositiveNumber,
       "equatorial_radius_km", PositiveNumber,
       "polar_radius_km", PositiveNumber,
       "reference", Url,
       [ "notes", ( String | Null ) ],
       MappingEnd ;
```

## Terminal

```ebnf
DirectionalBandwidth = MappingBegin,
                       "transmit", PositiveNumber,
                       "receive", PositiveNumber,
                       MappingEnd ;

RfSignal = MappingBegin,
           "band", Identifier,
           "frequency_hz", PositiveNumber,
           MappingEnd ;

OpticalSignal = MappingBegin,
                "wavelength_nm", PositiveNumber,
                MappingEnd ;

AngleRange = MappingBegin,
             "min", FiniteNumber,
             "max", FiniteNumber,
             MappingEnd ;

TerminalLimits = MappingBegin,
                 "azimuth_deg", AngleRange,
                 "elevation_deg", AngleRange,
                 "max_tracking_rate_deg_s", PositiveNumber,
                 MappingEnd ;

Terminal = MappingBegin,
           "id", Identifier,
           "display_name", String,
           "medium", ( "rf" | "optical" ),
           "signal", ( RfSignal | OpticalSignal ),
           "bandwidth_mbps", DirectionalBandwidth,
           "tracking_capacity", PositiveInteger,
           "max_range_km", PositiveNumber,
           "limits", TerminalLimits,
           "reference", Url,
           [ "notes", ( String | Null ) ],
           MappingEnd ;
```

For every `AngleRange`, `max` must be greater than or equal to `min`. An `rf`
terminal requires `RfSignal`; an `optical` terminal requires `OpticalSignal`.
A terminal declares physical capability, not a node, address, role, placement,
or route. `signal` records the authored carrier description. The current
runtime does not perform an RF link budget or compare frequency, wavelength,
channelization, or polarization; link-rule terminal selection must therefore
name the intended compatible mounts explicitly whenever medium alone is
ambiguous.

The persisted bandwidth is directional, but the current runtime exposes one
conservative shaped rate per link rather than independent directional rates.
That rate is the minimum of transmit and receive values across both selected
endpoint mounts.

## Payload

```ebnf
TerminalSlot = MappingBegin,
               "id", Identifier,
               "terminal", TerminalRef,
               [ "tags", ( Tags | Null ) ],
               MappingEnd ;

PayloadResourceGroup = MappingBegin,
                       "id", Identifier,
                       "slots", SequenceBegin, Identifier, { Identifier }, SequenceEnd,
                       "simultaneous_active", PositiveInteger,
                       MappingEnd ;

Payload = MappingBegin,
          "id", Identifier,
          [ "display_name", ( String | Null ) ],
          "terminal_slots", SequenceBegin, TerminalSlot, { TerminalSlot }, SequenceEnd,
          [ "resource_groups", SequenceBegin, { PayloadResourceGroup }, SequenceEnd ],
          [ "reference", ( Url | Null ) ],
          [ "notes", ( String | Null ) ],
          MappingEnd ;
```

Omitted `resource_groups` defaults to an empty sequence. Terminal-slot ids and
resource-group ids are unique within a payload. Every resource-group slot is
unique and names a declared terminal slot. `simultaneous_active` cannot exceed
the number of slots in its group.

## Profile

A profile is the complete workload composition for one node: the software the
node runs. The top-level image and container fields define the primary
container. `sidecars` declares additional cooperating containers in the same
pod. A profile is a reusable catalog object; where a node acquires its profile
is defined under "Workload profile assignment".

```ebnf
ArgvSequence = SequenceBegin, String, { String }, SequenceEnd ;

Capability = "AUDIT_WRITE" | "CHOWN" | "DAC_OVERRIDE" | "FOWNER" | "FSETID"
           | "KILL" | "MKNOD" | "NET_ADMIN" | "NET_BIND_SERVICE" | "NET_RAW"
           | "SETFCAP" | "SETGID" | "SETPCAP" | "SETUID" | "SYS_ADMIN"
           | "SYS_CHROOT" ;

CapabilitySequence = SequenceBegin, { Capability }, SequenceEnd ;

ProfileVolume = MappingBegin,
                "name", Identifier,
                "kind", "ephemeral",
                "medium", ( "memory" | "node" ),
                "size_mi", PositiveInteger,
                MappingEnd ;

ProfileVolumeSequence = SequenceBegin, { ProfileVolume }, SequenceEnd ;

ProfileMount = MappingBegin,
               "volume", Identifier,
               "path", MountPath,
               [ "read_only", Boolean ],
               MappingEnd ;

ProfileMountSequence = SequenceBegin, { ProfileMount }, SequenceEnd ;

ResourceAmounts = MappingBegin,
                  "cpu_m", PositiveInteger,
                  "memory_mi", PositiveInteger,
                  MappingEnd ;

ProfileResources = MappingBegin,
                   "requests", ResourceAmounts,
                   "limits", ResourceAmounts,
                   MappingEnd ;

EnvValueFrom = MappingBegin,
               "tag", Identifier,
               "interface", Identifier,
               "family", ( "ipv4" | "ipv6" ),
               MappingEnd ;

LiteralEnvEntry = MappingBegin,
                  "name", EnvName,
                  "value", String,
                  MappingEnd ;

ResolvedEnvEntry = MappingBegin,
                   "name", EnvName,
                   "value_from", EnvValueFrom,
                   MappingEnd ;

EnvEntry = LiteralEnvEntry | ResolvedEnvEntry ;

EnvSequence = SequenceBegin, { EnvEntry }, SequenceEnd ;

SshTerminalSurface = MappingBegin,
                     "surface", "ssh",
                     "authorized_keys_path", MountPath,
                     MappingEnd ;

ExecTerminalSurface = MappingBegin,
                      "surface", "exec",
                      "command", ArgvSequence,
                      MappingEnd ;

ProfileTerminal = SshTerminalSurface | ExecTerminalSurface ;

ProfileReadiness = MappingBegin,
                   "argv", ArgvSequence,
                   "timeout_seconds", PositiveInteger,
                   "period_seconds", PositiveInteger,
                   MappingEnd ;

ProfileSidecar = MappingBegin,
                 "name", Identifier,
                 [ "registry", ( RegistryHost | Null ) ],
                 "image", PinnedImage,
                 [ "command", ( ArgvSequence | Null ) ],
                 [ "args", ( ArgvSequence | Null ) ],
                 [ "env", ( EnvSequence | Null ) ],
                 [ "capabilities", ( CapabilitySequence | Null ) ],
                 [ "root_filesystem", ( "read_only" | "ephemeral_writable" ) ],
                 "resources", ProfileResources,
                 [ "mounts", ( ProfileMountSequence | Null ) ],
                 MappingEnd ;

ProfileSidecarSequence = SequenceBegin, { ProfileSidecar }, SequenceEnd ;

Profile = MappingBegin,
          "id", Identifier,
          [ "display_name", ( String | Null ) ],
          [ "adapter", ( Identifier | Null ) ],
          "registry", RegistryHost,
          "image", PinnedImage,
          [ "command", ( ArgvSequence | Null ) ],
          [ "args", ( ArgvSequence | Null ) ],
          [ "env", ( EnvSequence | Null ) ],
          [ "capabilities", ( CapabilitySequence | Null ) ],
          [ "root_filesystem", ( "read_only" | "ephemeral_writable" ) ],
          [ "config_mount", ( MountPath | Null ) ],
          [ "volumes", ( ProfileVolumeSequence | Null ) ],
          [ "mounts", ( ProfileMountSequence | Null ) ],
          "resources", ProfileResources,
          [ "readiness", ( ProfileReadiness | Null ) ],
          [ "terminal", ( ProfileTerminal | Null ) ],
          [ "sidecars", ( ProfileSidecarSequence | Null ) ],
          [ "reference", ( Url | Null ) ],
          [ "notes", ( String | Null ) ],
          MappingEnd ;
```

The image's own entrypoint starts every container. Authored `command` and
`args` are the profile author's declaration for that image; the platform never
wraps or substitutes an entrypoint it did not author. An argv sequence has at
most 64 elements and 4096 total bytes, and every element is nonempty.

`env` declares the container's environment. Each entry sets one variable into
the container at creation. A `value` entry carries the authored string. A
`value_from` entry names one fact of the resolved session: the address, by
interface name and family, of the single node carrying the named tag. The
delivered value is the host address without its prefix length. Env names are
unique within a sequence. A `value_from` entry is invalid when its tag
matches zero nodes or more than one, when the matched node has no interface
of the named identifier, or when that interface has no address of the
requested family; the session does not run. The platform sets environment
into containers and does nothing else with it: it never reads a container's
environment, never delivers it to another node, and never changes it on a
running container. A changed resolved value is a changed workload and
replaces the pod at reconciliation.

The container pull identity is the profile `registry` joined to the
digest-pinned `image`. `registry` is a profile field because user images
legitimately live in user registries; two profiles may pull from different
registries in one session. A sidecar with an omitted or null `registry` uses
the profile registry.

Omitted `capabilities`, `volumes`, `mounts`, and `sidecars` default to empty
sequences. Omitted `root_filesystem` defaults to `read_only`. Capability values
are unique and in ascending order. Volume names are unique within a profile.
Every mount names a declared volume; an omitted `read_only` defaults to
`false`. Within any one container, mount destinations must not be equal or
nested, and `config_mount` counts as a destination of the primary container.
Sidecar names are unique and never collide with the primary container.

`adapter` names the module that translates resolved per-node facts into the
image's native configuration. A profile with a null or absent `adapter` has no
rendering step; its containers receive exactly their authored configuration.
An adapter delivers configuration three ways, and the image's entrypoint stays
its own throughout: files mounted at an authored destination, per-node
environment variables set on the container, and arguments appended to the
entrypoint. Environment and argument delivery need no authored field. File
delivery is the one surface that requires an authored destination: a non-null
`config_mount` names the read-only path where rendered per-node files arrive
and therefore requires a non-null `adapter`. Which adapter values the
installed runtime provides, and which protocols each adapter renders, is
runtime support declared by the adapter modules, not structure; an
unavailable adapter fails support validation explicitly.

`terminal` declares the landing surface for platform terminal access. An `ssh`
surface requires the image to run its own SSH daemon; the platform mounts the
per-session public key at `authorized_keys_path` and proxies to it. An `exec`
surface attaches the declared command inside the primary container. A profile
without `terminal` declines terminal access, and the browser refuses the
terminal for that node instead of dialing a pod that cannot answer.

`readiness` runs `argv` in the primary container on the declared period until
it succeeds within `timeout_seconds`; the node is not treated as ready before
that. `resources.limits` must be greater than or equal to `resources.requests`
per amount, for the profile and for every sidecar.

## Orbit

```ebnf
OrbitElements = MappingBegin,
                "semi_major_axis_km", PositiveNumber,
                "eccentricity", FiniteNumber,
                MappingEnd ;

CircularShape = MappingBegin,
                "altitude_km", PositiveNumber,
                MappingEnd ;

PerigeeApogeeShape = MappingBegin,
                     "perigee_altitude_km", PositiveNumber,
                     "apogee_altitude_km", PositiveNumber,
                     MappingEnd ;

OrbitShape = CircularShape | PerigeeApogeeShape ;

OrbitOrientation = MappingBegin,
                   "inclination_deg", FiniteNumber,
                   "raan_deg", FiniteNumber,
                   "argument_of_perigee_deg", FiniteNumber,
                   MappingEnd ;

OrbitPhase = MappingBegin,
             "mean_anomaly_deg", FiniteNumber,
             MappingEnd ;

Orbit = MappingBegin,
        "id", Identifier,
        "central_body", BodyRef,
        "epoch", AwareTimestamp,
        [ "elements", ( OrbitElements | Null ) ],
        [ "shape", ( OrbitShape | Null ) ],
        "orientation", OrbitOrientation,
        "phase", OrbitPhase,
        "propagator", ( "two_body" | "j2_mean_elements" | "crtbp" ),
        "reference", Url,
        [ "notes", ( String | Null ) ],
        MappingEnd ;
```

`OrbitElements.eccentricity` is greater than or equal to zero and less than
one. In `PerigeeApogeeShape`, `apogee_altitude_km` is greater than or equal to
`perigee_altitude_km`. Exactly one of `elements` and `shape` must be non-null.

There is no session-level orbit-default object. Every orbit declares its own
propagator. SGP4 is not an `Orbit` propagator because a reusable mean-element
orbit cannot supply the spacecraft-specific TLE record SGP4 requires. TLE
placement is declared directly on a `SpaceNode`.

## Node

```ebnf
EthernetPort = MappingBegin,
               "id", Identifier,
               [ "tags", ( Tags | Null ) ],
               MappingEnd ;

NadirBoresight = MappingBegin,
                 "mode", "nadir",
                 MappingEnd ;

TerminalMount = MappingBegin,
                "id", Identifier,
                "role", ( "access" | "isl" | "crosslink" | "backbone" ),
                "terminal", TerminalRef,
                "count", PositiveInteger,
                [ "boresight", ( NadirBoresight | Null ) ],
                [ "tags", ( Tags | Null ) ],
                MappingEnd ;

PayloadMount = MappingBegin,
               "id", Identifier,
               "payload", PayloadRef,
               "count", PositiveInteger,
               [ "tags", ( Tags | Null ) ],
               MappingEnd ;

Node = MappingBegin,
       "id", Identifier,
       [ "display_name", ( String | Null ) ],
       "forwarding", ( "routed" | "host" | "bridge" | "control_only" ),
       [ "profile", ( ProfileRef | Null ) ],
       "ethernet", SequenceBegin, { EthernetPort }, SequenceEnd,
       "terminals", SequenceBegin, { TerminalMount }, SequenceEnd,
       "payloads", SequenceBegin, { PayloadMount }, SequenceEnd,
       [ "tags", ( Tags | Null ) ],
       [ "reference", ( Url | Null ) ],
       [ "notes", ( String | Null ) ],
       MappingEnd ;
```

Ethernet-port ids, terminal-mount ids, and payload-mount ids are independently
unique within a node. `TerminalMount.boresight` is valid only on an `access`
mount. A satellite access mount requires `boresight: {mode: nadir}`. A ground
access mount must omit node-level boresight because its orientation belongs to
the site installation. A node is a reusable definition and contains no
location or addressing. A node definition's `profile` is the authored workload
default for every node instantiated from it; "Workload profile assignment"
defines how segments and placed nodes override it.

The current substrate requires every ground node definition to declare exactly
one Ethernet port named `terr0`, and every space node definition to declare no
Ethernet ports. Terminal mounts produce resolver-owned WAN interfaces instead.

## Shared placement and scheduling types

```ebnf
FiniteTriple = SequenceBegin,
               FiniteNumber, FiniteNumber, FiniteNumber,
               SequenceEnd ;

StateVector = MappingBegin,
              "epoch", AwareTimestamp,
              "frame", Identifier,
              "position_km", FiniteTriple,
              "velocity_km_s", FiniteTriple,
              MappingEnd ;

SegmentClock = MappingBegin,
               [ "model", ( "session" | "affine" ) ],
               [ "offset_s", ( FiniteNumber | Null ) ],
               [ "rate", ( PositiveNumber | Null ) ],
               MappingEnd ;
```

Omitted `SegmentClock.model` defaults to `session`. A `session` clock forbids
non-null `offset_s` and `rate`. An `affine` clock requires a non-null `rate`;
`offset_s` remains optional.

```ebnf
OriginatedPrefixes = MappingBegin,
                     [ "ipv4", ( IPv4NetworkSequence | Null ) ],
                     [ "ipv6", ( IPv6NetworkSequence | Null ) ],
                     MappingEnd ;

IPv4NetworkSequence = SequenceBegin, { IPv4Network }, SequenceEnd ;
IPv6NetworkSequence = SequenceBegin, { IPv6Network }, SequenceEnd ;

HighestElevationPolicy = MappingBegin,
                         "highest_elevation", EmptyMapping,
                         MappingEnd ;

LowestElevationPolicy = MappingBegin,
                        "lowest_elevation", EmptyMapping,
                        MappingEnd ;

LongestRemainingPassPolicy = MappingBegin,
                             "longest_remaining_pass", MappingBegin,
                             "lookahead_horizon_ticks", PositiveInteger,
                             MappingEnd,
                             MappingEnd ;

SelectionPolicy = HighestElevationPolicy
                | LowestElevationPolicy
                | LongestRemainingPassPolicy ;

HysteresisPolicy = MappingBegin,
                   "hysteresis", MappingBegin,
                   "discount_factor", PositiveNumber,
                   "mask_fade_range_deg", NonNegativeNumber,
                   MappingEnd,
                   MappingEnd ;

HardReleasePolicy = MappingBegin,
                    "hard_release", EmptyMapping,
                    MappingEnd ;

HandoverPolicy = HysteresisPolicy | HardReleasePolicy ;

EmptyMapping = MappingBegin, MappingEnd ;

RankingComponent = "service_priority"
                 | "selection_score"
                 | "per_gs_rank"
                 | "satellite_ground_terminal_capacity"
                 | "lex_pair" ;

RankingOrder = SequenceBegin,
               RankingComponent, { RankingComponent },
               SequenceEnd ;

GroundScheduling = MappingBegin,
                   [ "selection_policy", ( SelectionPolicy | Null ) ],
                   [ "handover_policy", ( HandoverPolicy | Null ) ],
                   [ "handover_mode", ( "mbb" | "bbm" | Null ) ],
                   [ "mbb_overlap_ticks", ( NonNegativeInteger | Null ) ],
                   [ "mbb_reserve", ( NonNegativeInteger | Null ) ],
                   [ "handover_concurrency", ( "one_at_a_time" | "all_at_once" | Null ) ],
                   [ "ranking_order", ( RankingOrder | Null ) ],
                   [ "mbb_preemption", ( "off" | Null ) ],
                   [ "successor_abort_policy", ( "hard_release" | "soft_retain" | Null ) ],
                   [ "cross_tenant_displacement", ( "off" | Null ) ],
                   [ "bbm_acquire_timeout_ticks", ( NonNegativeInteger | Null ) ],
                   MappingEnd ;
```

`OriginatedPrefixes` must contain at least one prefix across its two families.
If `ranking_order` is present and non-null, its values are unique and its final
value is `lex_pair`.

Every ground node participating in an enabled access-link candidate must have
non-null effective values for every `GroundScheduling` field. A `bbm` node
requires `mbb_overlap_ticks: 0` and `mbb_reserve: 0`; an `mbb` node requires
both values to be positive. The effective values of `handover_concurrency`,
`ranking_order`, `mbb_preemption`, `successor_abort_policy`,
`cross_tenant_displacement`, and `bbm_acquire_timeout_ticks` must be uniform
across those access-candidate ground nodes. Ground nodes outside enabled
access-link candidates may omit scheduling.

## Site

```ebnf
VerificationMetadata = MappingBegin,
                       "source", String,
                       [ "filing", ( String | Null ) ],
                       [ "reference", ( Url | Null ) ],
                       [ "confidence", ( Identifier | Null ) ],
                       [ "notes", ( String | Null ) ],
                       MappingEnd ;

SiteLan = MappingBegin,
          [ "ipv4", ( IPv4Network | Null ) ],
          [ "ipv6", ( IPv6Network | Null ) ],
          MappingEnd ;

BodyFixedFrame = MappingBegin,
                 "body_fixed", MappingBegin,
                 "body", BodyRef,
                 MappingEnd,
                 MappingEnd ;

EphemerisAnchorFrame = MappingBegin,
                       "ephemeris_anchor", MappingBegin,
                       "frame", Identifier,
                       MappingEnd,
                       MappingEnd ;

ConfiguredStateLagrange = MappingBegin,
                          "configured_state", StateVector,
                          MappingEnd ;

ApproximateLagrange = MappingBegin,
                      "lagrange_approximation", EmptyMapping,
                      MappingEnd ;

ExternalEphemerisLagrange = MappingBegin,
                            "external_ephemeris", MappingBegin,
                            "path", RelativeAssetPath,
                            MappingEnd,
                            MappingEnd ;

LagrangeEphemeris = ConfiguredStateLagrange
                  | ApproximateLagrange
                  | ExternalEphemerisLagrange ;

LagrangeFrame = MappingBegin,
                "lagrange", MappingBegin,
                "primary_body", BodyRef,
                "secondary_body", BodyRef,
                "point", ( "l1" | "l2" | "l3" | "l4" | "l5" ),
                "ephemeris", LagrangeEphemeris,
                MappingEnd,
                MappingEnd ;

SiteFrame = BodyFixedFrame | LagrangeFrame | EphemerisAnchorFrame ;

SiteLocation = MappingBegin,
               "lat_deg", FiniteNumber,
               "lon_deg", FiniteNumber,
               "alt_m", FiniteNumber,
               MappingEnd ;

InterfaceAddress = MappingBegin,
                   [ "ipv4", ( IPv4Interface | Null ) ],
                   [ "ipv6", ( IPv6Interface | Null ) ],
                   MappingEnd ;

NodeInterfaces = MappingBegin,
                 "lo0", InterfaceAddress,
                 "terr0", InterfaceAddress,
                 MappingEnd ;

LocalVerticalBoresight = MappingBegin,
                         "mode", "local_vertical",
                         MappingEnd ;

ConfiguredTopocentricBoresight = MappingBegin,
                                 "mode", "configured_topocentric",
                                 "azimuth_deg", FiniteNumber,
                                 "elevation_deg", FiniteNumber,
                                 MappingEnd ;

SteerableEnvelopeBoresight = MappingBegin,
                             "mode", "steerable_envelope",
                             "azimuth_deg", AngleRange,
                             "elevation_deg", AngleRange,
                             MappingEnd ;

Boresight = LocalVerticalBoresight
          | ConfiguredTopocentricBoresight
          | SteerableEnvelopeBoresight ;

TerminalCapabilities = MappingBegin,
                       [ "bandwidth_mbps", ( DirectionalBandwidth | Null ) ],
                       [ "tracking_capacity", ( PositiveInteger | Null ) ],
                       [ "max_range_km", ( PositiveNumber | Null ) ],
                       [ "limits", ( TerminalLimits | Null ) ],
                       [ "boresight", ( Boresight | Null ) ],
                       MappingEnd ;

TerminalInstallation = MappingBegin,
                       "installed_count", PositiveInteger,
                       [ "capabilities", ( TerminalCapabilities | Null ) ],
                       [ "tags", ( Tags | Null ) ],
                       MappingEnd ;

PayloadInstallation = MappingBegin,
                      "installed_count", PositiveInteger,
                      [ "tags", ( Tags | Null ) ],
                      MappingEnd ;

TerminalInstallationMap = MappingBegin,
                          { Identifier, TerminalInstallation },
                          MappingEnd ;

PayloadInstallationMap = MappingBegin,
                         { Identifier, PayloadInstallation },
                         MappingEnd ;

SiteNode = MappingBegin,
           "id", Identifier,
           [ "display_name", ( String | Null ) ],
           "node", NodeRef,
           [ "profile", ( ProfileRef | Null ) ],
           "terminals", TerminalInstallationMap,
           "payloads", PayloadInstallationMap,
           "interfaces", NodeInterfaces,
           [ "originated_prefixes", ( OriginatedPrefixes | Null ) ],
           [ "tenant_id", ( Identifier | Null ) ],
           [ "service_priority", ( PositiveInteger | Null ) ],
           [ "scheduling", ( GroundScheduling | Null ) ],
           [ "tags", ( Tags | Null ) ],
           MappingEnd ;

Site = MappingBegin,
       "id", Identifier,
       [ "display_name", ( String | Null ) ],
       [ "verified", ( VerificationMetadata | Null ) ],
       "lan", SiteLan,
       [ "tags", ( Tags | Null ) ],
       "nodes", SequenceBegin, SiteNode, { SiteNode }, SequenceEnd,
       "frame", SiteFrame,
       [ "location", ( SiteLocation | Null ) ],
       MappingEnd ;
```

`SiteLan` and each `InterfaceAddress` must have at least one non-null address
family. Latitude is in `[-90, 90]`; longitude is in `[-180, 180]`. Site-node
ids are unique within a site. A body-fixed site requires a non-null `location`;
a Lagrange- or ephemeris-anchored site requires `location` to be absent or
null. Interface IP addresses are unique within a site. Each `terr0` address
requires the same family in `lan` and its host address must fall inside that
LAN.

The required `terminals` map is an exhaustive installation inventory, not a
patch over the referenced node definition. An omitted terminal mount has zero
installed instances at the site. The required `payloads` map records the
payload installation inventory. Every key in either map must be a mount id
declared by the referenced node definition; a present entry requires
`installed_count`, which cannot exceed the definition's mount count. Terminal capability overrides may
narrow, but never increase, the referenced terminal's directional bandwidth,
tracking capacity, maximum range, azimuth or elevation envelope, or maximum
tracking rate. Boresight is placement data and does not change the reusable
terminal. Every installed ground access mount requires a non-null boresight in
its site capability override; non-access mounts must not declare one.

An omitted `SiteNode.tenant_id` resolves to `default`. An omitted
`SiteNode.service_priority` remains unset in the resolved session and becomes
priority `10` when OME builds ground-allocation input. These fields are
allocator metadata. `tenant_id` is not a catalog namespace, authentication
identity, or storage-isolation boundary.

## Site set

```ebnf
SiteSet = MappingBegin,
          "id", Identifier,
          [ "display_name", ( String | Null ) ],
          "sites", SequenceBegin, SiteRef, { SiteRef }, SequenceEnd,
          [ "tags", ( Tags | Null ) ],
          [ "reference", ( Url | Null ) ],
          [ "notes", ( String | Null ) ],
          MappingEnd ;
```

Site references are unique within a site set.

## Constellation

```ebnf
PlaneParams = MappingBegin,
              "count", PositiveInteger,
              "raan_spacing_deg", NonNegativeNumber,
              MappingEnd ;

Phasing = MappingBegin,
          "mode", ( "walker_delta" | "walker_star" | "evenly_spaced_mean_anomaly" ),
          [ "phase_offset_deg", ( FiniteNumber | Null ) ],
          MappingEnd ;

NonNegativeIntegerSequence = SequenceBegin,
                             NonNegativeInteger, { NonNegativeInteger },
                             SequenceEnd ;

IdentifierSequence = SequenceBegin,
                     Identifier, { Identifier },
                     SequenceEnd ;

NodeTagRule = MappingBegin,
              "tag", Identifier,
              [ "planes", ( NonNegativeIntegerSequence | Null ) ],
              [ "slots", ( NonNegativeIntegerSequence | Null ) ],
              [ "node_ids", ( IdentifierSequence | Null ) ],
              MappingEnd ;

Constellation = MappingBegin,
                "id", Identifier,
                [ "display_name", ( String | Null ) ],
                "node", NodeRef,
                "orbit", OrbitRef,
                "planes", PlaneParams,
                "slots_per_plane", PositiveInteger,
                "phasing", Phasing,
                "node_tags", SequenceBegin, { NodeTagRule }, SequenceEnd,
                [ "tags", ( Tags | Null ) ],
                [ "reference", ( Url | Null ) ],
                [ "notes", ( String | Null ) ],
                MappingEnd ;
```

The `planes`, `slots`, and `node_ids` sequences are individually unique.
`node_ids` cannot be combined with `planes` or `slots`. Plane and slot indexes
must exist in the constellation. Generated node ids have the canonical form
`sat-p<plane>s<slot>`, with the numeric plane and slot each padded to at least
two digits, and must name an existing plane and slot. A rule with no selector
fields applies its tag to all generated nodes.

`evenly_spaced_mean_anomaly` requires exactly one plane and an absent, null, or
zero `phase_offset_deg`. `walker_delta` and `walker_star` require at least two
planes and a non-null `phase_offset_deg`.

## Space node set

```ebnf
Sgp4TlePlacement = MappingBegin,
                   "central_body", BodyRef,
                   "line_1", String,
                   "line_2", String,
                   MappingEnd ;

SpaceNode = MappingBegin,
            "id", Identifier,
            "node", NodeRef,
            [ "profile", ( ProfileRef | Null ) ],
            [ "orbit", ( OrbitRef | Null ) ],
            [ "sgp4_tle", ( Sgp4TlePlacement | Null ) ],
            [ "state_vector", ( StateVector | Null ) ],
            [ "tags", ( Tags | Null ) ],
            [ "clock", ( SegmentClock | Null ) ],
            MappingEnd ;

SpaceNodeSet = MappingBegin,
               "id", Identifier,
               "nodes", SequenceBegin, SpaceNode, { SpaceNode }, SequenceEnd,
               [ "tags", ( Tags | Null ) ],
               MappingEnd ;
```

Exactly one of `SpaceNode.orbit`, `SpaceNode.sgp4_tle`, and
`SpaceNode.state_vector` must be non-null. TLE lines are nonempty, form one
valid pair, and identify the same catalog number. The current SGP4 runtime is
Earth-only, so `sgp4_tle.central_body` must resolve to Earth. Space-node ids are
unique within a set. `SpaceNode` is embedded in a `SpaceNodeSet`; it is not a
standalone catalog family. A non-null `SpaceNode.clock` overrides the containing
space segment's clock for that fixed node; otherwise the node inherits the
segment clock, or the default `session` clock when neither is declared.

## Session and segments

```ebnf
SessionMeta = MappingBegin,
              "name", Identifier,
              [ "display_name", ( String | Null ) ],
              [ "description", ( String | Null ) ],
              MappingEnd ;

GroundPlacement = MappingBegin,
                  "from_site_set", SiteSetRef,
                  MappingEnd ;

GroundApply = MappingBegin,
              [ "scheduling", ( GroundScheduling | Null ) ],
              [ "originated_prefixes", ( OriginatedPrefixes | Null ) ],
              [ "tags", ( Tags | Null ) ],
              MappingEnd ;

GroundOverrideMatch = MappingBegin,
                      "site", Identifier,
                      MappingEnd ;

GroundOverride = MappingBegin,
                 "match", GroundOverrideMatch,
                 [ "tags", ( Tags | Null ) ],
                 [ "scheduling", ( GroundScheduling | Null ) ],
                 [ "originated_prefixes", ( OriginatedPrefixes | Null ) ],
                 MappingEnd ;

SpaceSegment = MappingBegin,
               "id", Identifier,
               [ "display_name", ( String | Null ) ],
               [ "tags", ( Tags | Null ) ],
               [ "clock", ( SegmentClock | Null ) ],
               [ "profile", ( ProfileRef | Null ) ],
               "source", SpaceSourceRef,
               MappingEnd ;

GroundSegment = MappingBegin,
                "id", Identifier,
                [ "display_name", ( String | Null ) ],
                [ "tags", ( Tags | Null ) ],
                [ "clock", ( SegmentClock | Null ) ],
                [ "profile", ( ProfileRef | Null ) ],
                "placement", GroundPlacement,
                [ "apply", ( GroundApply | Null ) ],
                [ "overrides", ( GroundOverrideSequence | Null ) ],
                MappingEnd ;

GroundOverrideSequence = SequenceBegin, { GroundOverride }, SequenceEnd ;

LagrangeSegment = MappingBegin,
                  "id", Identifier,
                  [ "display_name", ( String | Null ) ],
                  [ "tags", ( Tags | Null ) ],
                  [ "clock", ( SegmentClock | Null ) ],
                  [ "profile", ( ProfileRef | Null ) ],
                  "node", NodeRef,
                  "frame", LagrangeFrame,
                  MappingEnd ;

Segment = SpaceSegment | GroundSegment | LagrangeSegment ;
```

Ground overrides target unique site ids that exist in the segment's selected
site set. If one physical site is included by multiple ground segments, it is
instantiated once; scheduling, originated-prefix policy, and effective segment
clock must agree across those placements, while tags are combined. An omitted
segment clock is the default `session` clock for this comparison. A ground
override's non-null scheduling or originated-prefix object replaces the
corresponding segment-apply object for that site. Site-node scheduling then
overrides the resulting scheduling field by field; its unset or null fields
inherit. A site-node's originated prefixes are combined with the effective
segment-apply or ground-override prefixes rather than replacing them.

## Workload profile assignment

`profile` is readable at three levels, and resolution takes the most specific
non-null statement for each node:

1. The node definition (`Node.profile`) is the authored default for every
   node instantiated from it.
2. A segment `profile` overrides that default for every node the segment
   resolves.
3. A placed node's `profile` (`SpaceNode.profile` for a fixed space node,
   `SiteNode.profile` for an installed ground node) overrides both.

Every resolved runtime node must have an effective profile. A node with no
`profile` statement at any level fails resolution; there is no platform
default workload and no deference to any particular implementation. Inheriting
a node-model default is an authored statement, never a fallback. The resolved
session records, for each node, the effective profile reference and the level
that supplied it.

A routing domain is a declaration about routers: one set of nodes sharing a
single instance of a routing protocol. A node is a router exactly when its
effective profile's `adapter` renders routing-protocol configuration; which
adapters render which protocols and capabilities is declared by the adapter
modules and is runtime support. Domain membership derives from that router
population: the domain's selectors resolve against the session's nodes, and
its members are the routers among them whose adapter renders the domain's
protocol and declared capabilities. A node running no routing workload is
never a membership candidate, whatever its wiring class; a host is reached
through the router serving its network, which originates the host's network
into its own domain.

When `routing` is present, every router belongs to exactly one domain, and
every domain contains at least one member. With `routing` omitted, the
default domain forms over the routers whose adapter renders IS-IS.

These rules govern what the platform renders and delivers. They state
nothing about protocol behavior: what the running images do with their
configuration and their connected interfaces is the workload's own, observed
through measurement, never predicted or asserted by the platform. A profile
never creates, removes, or reclassifies nodes, links, or physics; it declares
what the node runs.

## Selectors and link rules

A selector is an explicit set expression. A mapping or sequence does not imply
AND or OR; only `all`, `any`, and `not` compose sets.

```ebnf
NodeSelector = MappingBegin,
               [ "all", ( NodeSelectorSequence | Null ) ],
               [ "any", ( NodeSelectorSequence | Null ) ],
               [ "not", ( NodeSelector | Null ) ],
               [ "segment", ( Identifier | Null ) ],
               [ "tag", ( Identifier | Null ) ],
               [ "node", ( Identifier | Null ) ],
               [ "plane", ( NonNegativeInteger | Null ) ],
               [ "slot", ( NonNegativeInteger | Null ) ],
               MappingEnd ;

NodeSelectorSequence = SequenceBegin,
                       NodeSelector, { NodeSelector },
                       SequenceEnd ;

TerminalSelector = MappingBegin,
                   [ "all", ( TerminalSelectorSequence | Null ) ],
                   [ "any", ( TerminalSelectorSequence | Null ) ],
                   [ "not", ( TerminalSelector | Null ) ],
                   [ "role", ( "access" | "isl" | "crosslink" | "backbone" | Null ) ],
                   [ "medium", ( "rf" | "optical" | Null ) ],
                   [ "mount", ( Identifier | Null ) ],
                   MappingEnd ;

TerminalSelectorSequence = SequenceBegin,
                           TerminalSelector, { TerminalSelector },
                           SequenceEnd ;

Endpoint = MappingBegin,
           "select", NodeSelector,
           "terminal", TerminalSelector,
           [ "min_elevation_deg", ( FiniteNumber | Null ) ],
           MappingEnd ;

EndpointPair = SequenceBegin, Endpoint, Endpoint, SequenceEnd ;

VisibleCandidatesTopology = MappingBegin,
                            "mode", "visible_candidates",
                            MappingEnd ;

NearestVisibleTopology = MappingBegin,
                         "mode", "nearest_visible",
                         MappingEnd ;

NearestNTopology = MappingBegin,
                   "mode", "nearest_n",
                   "n", PositiveInteger,
                   MappingEnd ;

ExplicitPair = MappingBegin,
               "a", Identifier,
               "b", Identifier,
               MappingEnd ;

ExplicitPairsTopology = MappingBegin,
                        "mode", "explicit_pairs",
                        "pairs", SequenceBegin, ExplicitPair, { ExplicitPair }, SequenceEnd,
                        MappingEnd ;

LinkTopology = VisibleCandidatesTopology
             | NearestVisibleTopology
             | NearestNTopology
             | ExplicitPairsTopology ;

MaxLinksPerNodeMap = MappingBegin,
                     Identifier, PositiveInteger,
                     { Identifier, PositiveInteger },
                     MappingEnd ;

MaxLinksPerNode = PositiveInteger | MaxLinksPerNodeMap ;

LinkRuleConstraints = MappingBegin,
                      [ "max_links_per_node", ( MaxLinksPerNode | Null ) ],
                      [ "max_range_km", ( PositiveNumber | Null ) ],
                      [ "require_mutual_visibility", ( Boolean | Null ) ],
                      MappingEnd ;

LinkRule = MappingBegin,
           "id", Identifier,
           [ "enabled", Boolean ],
           "endpoints", EndpointPair,
           "topology", LinkTopology,
           [ "constraints", ( LinkRuleConstraints | Null ) ],
           [ "tags", ( Tags | Null ) ],
           MappingEnd ;
```

Each `NodeSelector` and `TerminalSelector` has exactly one non-null field. Each
`ExplicitPair` has different endpoints, and undirected explicit pairs are
unique. Omitted `LinkRule.enabled` defaults to `true`.

Link rules declare candidate permission; orbital and terminal physics still
decide whether a candidate is usable. A node selector and terminal selector
must each resolve to non-empty compatible sets. Every endpoint terminal
selector must name exactly one positive role and at most one positive medium.
After reference resolution, every endpoint must select mounts of exactly one
actual medium, and the two endpoint media must match. Omitting `medium` does not
permit an RF-to-optical candidate; the resolver derives the matched medium and
rejects ambiguous or incompatible selections before candidate generation.
The resolver derives link class from endpoint roles and resolved endpoint
bodies. Any rule with an `access` endpoint uses the access path. A non-access
rule whose possible endpoint pairs are uniformly body-local is an `isl`; one
whose possible endpoint pairs are uniformly cross-body is `inter_body`. A rule
that mixes body-local and cross-body pairs is invalid and must be split into
body-specific rules. There is no authored `class`, `kind`, or link-label field.

`Endpoint.min_elevation_deg` is in `[0, 90]` and is valid only on the ground
endpoint of an access rule. It is invalid on a space endpoint or any non-access
rule. For each ground station, the effective OME elevation mask is the maximum
of the matching installed terminal minimum and every applicable access-rule
endpoint minimum.

`ExplicitPair.a` and `ExplicitPair.b` are local node ids within the two resolved
endpoint segments, not resolver-derived runtime node ids. Ground local ids are
site-qualified. Every pair must resolve inside the rule's selected endpoint
sets. `nearest_n` applies a deterministic, physical-distance-ranked greedy
degree cap: no selected node has more than `n` declared candidates, and a node
may receive fewer when the peer's cap has already been filled.

Every unordered runtime node pair produced by enabled link rules has exactly
one owning rule; if two rules produce the same pair, resolution fails. Each
ground terminal may bind to at most one enabled access rule. Access links
require exactly one ground endpoint and remain local to the ground site's
reference body. A per-segment `max_links_per_node` map must cover every
selected node through its segment or placement-group label.

## Addressing

```ebnf
AddressPoolAssignment = MappingBegin,
                        "id", Identifier,
                        "applies_to", NodeSelector,
                        [ "ipv4_pool", ( IPv4Network | Null ) ],
                        [ "ipv6_pool", ( IPv6Network | Null ) ],
                        [ "prefix_length", ( PositiveInteger | Null ) ],
                        [ "allocation", ( "by_node_order"
                                        | "by_attach_index"
                                        | "by_plane_slot"
                                        | "by_ground_index"
                                        | Null ) ],
                        MappingEnd ;

AddressPoolAssignmentSequence = SequenceBegin,
                                { AddressPoolAssignment },
                                SequenceEnd ;

Addressing = MappingBegin,
             [ "loopbacks", ( AddressPoolAssignmentSequence | Null ) ],
             [ "point_to_point", ( AddressPoolAssignmentSequence | Null ) ],
             [ "terrestrial_prefixes", ( AddressPoolAssignmentSequence | Null ) ],
             MappingEnd ;
```

An assignment has at least one non-null pool. A non-null `prefix_length` is not
shorter than any supplied pool prefix and does not exceed the address-family
maximum. An executed loopback assignment must match at least one node, declare
`prefix_length`, fit that prefix inside each selected pool, have enough free
addresses, and not conflict with authored loopbacks. An omitted or null
`allocation` is interpreted as `by_node_order`.

Ground-site addresses remain authored on their sites. Generated routed space
nodes without an explicit loopback assignment receive deterministic
resolver-owned IPv4 and IPv6 loopbacks. Across the fully resolved session, a
loopback host address belongs to exactly one node per address family; duplicate
authored or allocated `lo0` addresses are invalid.

## Routing

```ebnf
MplsCapability = EmptyMapping ;

SegmentRoutingCapability = MappingBegin,
                           "data_plane", "mpls",
                           MappingEnd ;

MplsDataPlaneSequence = SequenceBegin, { "mpls" }, SequenceEnd ;

TrafficEngineeringCapability = MappingBegin,
                               [ "data_planes", ( MplsDataPlaneSequence | Null ) ],
                               MappingEnd ;

RoutingCapabilities = MappingBegin,
                      [ "mpls", ( MplsCapability | Null ) ],
                      [ "segment_routing", ( SegmentRoutingCapability | Null ) ],
                      [ "traffic_engineering", ( TrafficEngineeringCapability | Null ) ],
                      MappingEnd ;

AreaMapping = MappingBegin,
              [ "planes", ( NonNegativeIntegerSequence | Null ) ],
              [ "ground_stations", ( "all" | IdentifierSequence | Null ) ],
              "area_id", RoutingAreaId,
              MappingEnd ;

AreaMappingSequence = SequenceBegin, { AreaMapping }, SequenceEnd ;

AreaAssignment = MappingBegin,
                 "strategy", ( "flat" | "per_plane" | "stripe" | "explicit" ),
                 [ "gs_area_id", ( RoutingAreaId | Null ) ],
                 [ "planes_per_stripe", ( PositiveInteger | Null ) ],
                 [ "assignments", ( AreaMappingSequence | Null ) ],
                 MappingEnd ;
```

An `AreaMapping` has a non-null `planes` or `ground_stations` target, and each
sequence is unique. For `flat` and `per_plane`, `planes_per_stripe` and
`assignments` are absent or null. `stripe` requires `planes_per_stripe` and
forbids a non-null `assignments`. `explicit` requires non-null `assignments`
and forbids a non-null `planes_per_stripe`.

Area assignment is valid only for IS-IS and OSPF. An OSPF area id is a
canonical dotted IPv4 address such as `0.0.0.0`. An IS-IS area id is a
lower-case token containing one two-hex-digit group followed by zero to six
four-hex-digit dotted groups, such as `49` or `49.0001`.

With no area assignment, or with `strategy: flat`, every node uses
`gs_area_id` when it is present; otherwise the default is `49.0001` for IS-IS
and `0.0.0.0` for OSPF. `per_plane` derives one area per satellite plane and
uses `gs_area_id` or the protocol default for ground nodes. `stripe` groups
satellite planes by `planes_per_stripe` and uses the same ground fallback.
For plane or stripe index `i`, starting at zero, the derived area is
`49.<i + 1 as four zero-padded decimal digits>` for IS-IS and
`0.0.0.<i + 1>` for OSPF. For `stripe`, `i` is integer division of the plane
index by `planes_per_stripe`. Derived OSPF area indexes cannot exceed `255`,
and derived IS-IS indexes cannot exceed `9999` under the current four-digit
format. `per_plane` and `stripe` require at least one selected satellite.
Every satellite selected by a non-flat strategy requires a resolved plane, so
orbit-placed fixed nodes without plane facts cannot use those strategies. An
`explicit` assignment may select only ground nodes; when it selects
satellites, every selected satellite plane must be mapped exactly once. Ground
station targets are site-qualified local node ids, and duplicate or unknown
targets are invalid. An explicitly unmapped ground node uses `gs_area_id` or
the protocol default.

```ebnf
SpfThrottle = MappingBegin,
              [ "init_delay_ms", NonNegativeInteger ],
              [ "short_delay_ms", NonNegativeInteger ],
              [ "long_delay_ms", NonNegativeInteger ],
              [ "holddown_ms", ( NonNegativeInteger | Null ) ],
              [ "time_to_learn_ms", ( NonNegativeInteger | Null ) ],
              MappingEnd ;

BfdConfig = MappingBegin,
            [ "enabled", Boolean ],
            [ "detect_multiplier", PositiveInteger ],
            [ "rx_interval_ms", PositiveInteger ],
            [ "tx_interval_ms", PositiveInteger ],
            MappingEnd ;

RoutingTimers = MappingBegin,
                [ "hello_interval_s", PositiveInteger ],
                [ "hold_interval_s", PositiveInteger ],
                [ "spf", SpfThrottle ],
                [ "bfd", BfdConfig ],
                MappingEnd ;

RoutingDomain = MappingBegin,
                "id", Identifier,
                "protocol", ( "isis" | "ospf" | "bgp" | "static" ),
                [ "capabilities", ( RoutingCapabilities | Null ) ],
                "selectors", SequenceBegin, NodeSelector, { NodeSelector }, SequenceEnd,
                [ "area_assignment", ( AreaAssignment | Null ) ],
                [ "timers", ( RoutingTimers | Null ) ],
                MappingEnd ;

AggregateOf = MappingBegin,
              "aggregate_of", "originated",
              MappingEnd ;

IpNetworkSequence = SequenceBegin, { IpNetwork }, SequenceEnd ;

ExportRule = MappingBegin,
             "from", Identifier,
             "to", Identifier,
             "prefixes", ( IpNetworkSequence | AggregateOf ),
             [ "export_node_loopbacks", ( Boolean | Null ) ],
             [ "install_via", ( "peer_loopback" | NonEmptyToken | Null ) ],
             MappingEnd ;

RoutingBoundary = MappingBegin,
                  "over", Identifier,
                  "adapter", ( "static_ip" | "bgp" | "dtn_bundle" ),
                  "export", SequenceBegin, ExportRule, { ExportRule }, SequenceEnd,
                  MappingEnd ;

Routing = MappingBegin,
          "domains", SequenceBegin, RoutingDomain, { RoutingDomain }, SequenceEnd,
          [ "boundaries", ( RoutingBoundarySequence | Null ) ],
          MappingEnd ;

RoutingBoundarySequence = SequenceBegin, { RoutingBoundary }, SequenceEnd ;
```

The first three `SpfThrottle` fields default, in field order, to `50`, `200`,
and `1000` milliseconds. For IS-IS, the resolver supplies `2000` and `500`
milliseconds when `holddown_ms` and `time_to_learn_ms` are omitted. OSPF
forbids non-null `holddown_ms` and `time_to_learn_ms`. `BfdConfig` defaults are
`false`, `3`, `300`, and `300`.
`RoutingTimers.hello_interval_s` defaults to `1`, `hold_interval_s` defaults to
`3`, and omitted `spf` and `bfd` fields default to their respective default
objects. The hold interval must exceed the hello interval. A non-null `timers`
field is valid only for `isis` and `ospf`.

Routing-domain ids are unique. When `routing` is present, every router
belongs to exactly one domain, and every domain contains at least one member;
"Workload profile assignment" defines the router population domain membership
derives from. A boundary names an existing enabled non-access
link rule and exports between two different, existing domains on opposite
sides of that rule. Every enabled non-access rule spanning multiple domains
requires a boundary.

An export's literal prefix sequence supplies the declared set by address
family. `aggregate_of: originated` instead derives the `from` domain's authored
originated prefixes. `export_node_loopbacks: true` adds every routed node
loopback in that domain. An omitted or null `install_via` is `peer_loopback`;
the receiving boundary node uses the opposite endpoint's loopback for each
available address family. A literal prefix family without that peer loopback is
an error; an aggregate simply has no installable route in that family. A
non-`peer_loopback` token is passed as the explicit next-hop or interface
value. Materialization omits a route to the receiving node's own loopback and
the peer-loopback seed route.

When `routing` is omitted, the resolver creates `default_domain`, running
IS-IS over the routers whose adapter renders IS-IS, and requires at least one
such node. A session with no `routing` block whose routers cannot render
IS-IS is invalid; declare routing explicitly.

## Simulation, time, ephemeris, and dispatch

```ebnf
CandidateLimits = MappingBegin,
                  "max_pairs_per_rule", PositiveInteger,
                  "max_pairs_per_tick", PositiveInteger,
                  MappingEnd ;

Simulation = MappingBegin,
             [ "candidate_limits", ( CandidateLimits | Null ) ],
             [ "ground_link_model", ( "geometry_only" | "terminal_physics" ) ],
             [ "acknowledge_geometry_only", Boolean ],
             MappingEnd ;

TimeConfig = MappingBegin,
             "start_time", AwareTimestamp,
             "step_seconds", PositiveNumber,
             "compression", PositiveNumber,
             MappingEnd ;

BodyRefSequence = SequenceBegin, BodyRef, { BodyRef }, SequenceEnd ;

EphemerisKernel = MappingBegin,
                  "id", Identifier,
                  "path", RelativeAssetPath,
                  [ "sha256", ( Sha256Hex | Null ) ],
                  "targets", BodyRefSequence,
                  "frame", Identifier,
                  [ "coverage_start", ( AwareTimestamp | Null ) ],
                  [ "coverage_end", ( AwareTimestamp | Null ) ],
                  MappingEnd ;

Ephemeris = MappingBegin,
            "provider", ( "skyfield_bsp" | "spice_kernel_stack" | "operator_supplied_spk" ),
            "quality_tier", Identifier,
            "kernels", SequenceBegin, EphemerisKernel, { EphemerisKernel }, SequenceEnd,
            MappingEnd ;

Dispatch = MappingBegin,
           "latency_authority", "ome",
           "max_latency_age_ticks", PositiveInteger,
           MappingEnd ;

Session = MappingBegin,
          "session", SessionMeta,
          "segments", SequenceBegin, Segment, { Segment }, SequenceEnd,
          [ "link_rules", ( LinkRuleSequence | Null ) ],
          [ "addressing", ( Addressing | Null ) ],
          [ "routing", ( Routing | Null ) ],
          [ "simulation", ( Simulation | Null ) ],
          "time", TimeConfig,
          [ "ephemeris", ( Ephemeris | Null ) ],
          [ "dispatch", ( Dispatch | Null ) ],
          MappingEnd ;

LinkRuleSequence = SequenceBegin, { LinkRule }, SequenceEnd ;
```

Segment ids and link-rule ids are independently unique. A session with more
than one segment and at least one link rule must declare
`simulation.candidate_limits`; the static and materialized candidate counts
must remain within both limits. If `dispatch` is omitted, the resolver supplies
`latency_authority: ome` and `max_latency_age_ticks: 3`.

`simulation.ground_link_model` defaults to `terminal_physics`, and
`acknowledge_geometry_only` defaults to `false`. Selecting `geometry_only`
requires `acknowledge_geometry_only: true`; the acknowledgement is invalid for
`terminal_physics`. Geometry-only mode is an explicit experimental waiver, not
an inferred fallback.

Every active body other than Earth requires an ephemeris manifest target. Earth
state is implicit in the current runtime. The current Skyfield runtime
additionally requires exactly one local kernel, a matching SHA-256, and both
coverage fields. `coverage_start` and `coverage_end` must be declared together,
and `coverage_end` must be later than `coverage_start`. Resolution validates
that `time.start_time` lies within the inclusive declared coverage; during
playback, seek, or lookahead, the provider also rejects every requested time
outside that coverage instead of extrapolating.

## Resolver-wide constraints

The following relationships are semantic parts of the language even though
they span more than one YAML document and therefore are not expressible by the
context-free EBNF alone:

- Every reference loads exactly one document of the declared family.
- A session resolves at least one runtime node and at least one active body.
- Conflicting physical facts for the same body id are invalid.
- A `lo0` host address is globally unique per address family across all
  resolved nodes, independent of which site, segment, or pool supplied it.
- Runtime node ids are resolver-owned. Space ids are the normalized segment id
  plus the local space-node id; ground ids are the normalized site id plus the
  site-node id, independent of which ground segment placed the site.
  Underscores normalize to hyphens. The result must be globally unique,
  lower-case DNS-label safe, and no longer than 63 characters; otherwise
  resolution fails before Kubernetes objects are created.
- A link endpoint resolves nodes from a coherent segment or shared placement
  group and finds compatible installed terminal mounts.
- Each unordered candidate node pair from enabled link rules is owned by
  exactly one link rule.
- Runtime node-selector tags come from segment and placement facts: generated
  space nodes receive space-segment tags, constellation tags, and matching
  `node_tags`; fixed space nodes receive space-segment and `SpaceNode.tags`;
  ground nodes receive ground-segment/apply/override, site, and site-node tags.
  Tags on reusable node definitions, mounts, site sets, and space-node sets
  remain catalog metadata. Tag text never defines physics, link class, routing,
  addressing, scheduling, or actuation.
- Site-level scheduling overrides segment scheduling field by field. A matching
  ground override replaces the segment apply value for scheduling and
  originated-prefix intent; at most one override targets a site.
- `originated_prefixes` is routing-injection intent. It does not allocate or
  infer address ownership.
- Every resolved node has exactly one effective workload profile, taken from
  the most specific of its own node entry, its segment, and its node model. A
  node with no profile statement at any level fails resolution. The resolved
  session records the effective reference and the supplying level.
- Routing-domain membership derives from the router population: the routers
  among a domain's selected nodes whose adapter renders the domain protocol
  and declared capabilities. Every router belongs to exactly one domain when
  `routing` is present; nodes running no routing workload are never
  membership candidates. These checks validate platform rendering only; they
  assert nothing about protocol behavior.
- Every `value_from` entry of every effective profile resolves against the
  session's nodes: exactly one node carries the tag, that node declares the
  named interface, and the interface carries the requested address family.
  Any mismatch refuses resolution.
- Adapter availability is runtime support. Structural validity of an
  `adapter` value never implies the installed runtime provides that adapter.
- Routing failure, lack of convergence, and unreachable destinations remain
  valid experimental results. Resolution does not fabricate routes or links.

## Current runtime support

Structural validity does not imply that the installed runtime can execute a
construct. The production Earth-Luna profile currently supports:

- `constellation`, `space_node_set` with orbit- or Earth-TLE-placed nodes, and
  body-fixed ground-site-set segments;
- Earth and Luna body facts;
- `two_body` and `j2_mean_elements` reusable-orbit propagation;
- Earth `sgp4_tle` placement on fixed space nodes;
- `session` clocks;
- `visible_candidates`, `nearest_n`, and `explicit_pairs` topology modes;
- the `max_links_per_node` link constraint;
- loopback address pools using `by_node_order` allocation;
- IS-IS, OSPF, and static FRR routing domains;
- MPLS, segment routing, and traffic engineering on IS-IS and OSPF domains;
- `static_ip` routing boundaries;
- serialized ground handovers with `handover_concurrency: one_at_a_time`, at
  most one reserved MBB overlap, and one-tick BBM acquisition;
- `skyfield_bsp` ephemeris;
- workload profiles at all three assignment levels, with the `frr` adapter
  and adapter-free application profiles.

The following structurally valid constructs are currently rejected before
runtime execution:

- Lagrange segments and non-body-fixed ground sites;
- raw state-vector space-node placement;
- `crtbp` propagation;
- payload execution;
- `affine` clocks;
- `nearest_visible` topology;
- `max_range_km` and `require_mutual_visibility` link constraints;
- `handover_concurrency: all_at_once`, `mbb_reserve` above 1, and
  `bbm_acquire_timeout_ticks` values other than 1;
- point-to-point and terrestrial-prefix pools;
- `by_attach_index`, `by_plane_slot`, and `by_ground_index` allocation;
- BGP routing domains;
- routing capabilities on BGP or static domains;
- `bgp` and `dtn_bundle` routing boundaries;
- `spice_kernel_stack` and `operator_supplied_spk` ephemeris providers;
- bodies outside the supported Earth-Luna profile;
- workload adapters other than `frr`;
- the profile `env` field: defined ahead of the installed models, which
  still reject it structurally; the matching model and resolver change
  follows this definition.

Unsupported features fail explicitly. They are never silently removed,
flattened, translated to another feature, or treated as successful execution.
