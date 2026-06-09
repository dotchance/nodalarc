# Adding Routing Stacks

NodalArc uses FRRouting as the routing engine inside every session pod today.
Adding a new routing protocol means either:

1. **Enabling an FRR daemon** that is in the image but not yet templated
   end-to-end
2. **Integrating a different routing daemon** (e.g., BIRD, OpenBGPd, cRPD)

## How Routing Stacks Work

Each session pod runs FRR with a configuration generated from Jinja2 templates at session creation time. The template system:

1. Session YAML declares `routing.domains`, each naming a `protocol` and the nodes it covers
2. The shared resolver builds the frozen runtime session
3. The Operator's `frr_renderer.py` resolves which template to use
4. The template receives variables: loopback IPs, interface IPs, area assignments, SID indexes, ground prefixes, segment metadata, and terminal-derived interface facts
4. Rendered output is `frr.conf` + `daemons` file delivered to the pod

## Adding an FRR Protocol (Simplest Path)

### Step 1: Create a Jinja2 template

```
configs/templates/frr/{protocol_name}.conf.j2
```

The template has access to:

| Variable | Type | Content |
|----------|------|---------|
| `node_id` | str | e.g., `space-sat-p00s00`, `leo-sat-p00s00`, or `earth-cl-santiago-gw1` |
| `node_type` | str | `satellite` or `ground_station` |
| `loopback_ip` | str | Node's loopback address |
| `interfaces` | list | All interfaces with peer info, IP, bandwidth |
| `area_id` | str | Routing area this node belongs to |
| `plane` | int | Orbital plane index |
| `slot` | int | Slot within plane |
| `terrestrial_prefixes` | list | Ground-node prefix advertisements |
| `extensions` | list | Enabled extensions (e.g., `traffic-engineering`, `sr`) |
| `config_overrides` | dict | User-provided key-value overrides |

### Step 2: Create a daemons template

```
configs/templates/frr/{protocol_name}.daemons.j2
```

This specifies which FRR daemons to enable:

```
zebra=yes
{protocol}d=yes
staticd=yes
```

### Step 3: Register the protocol

In `services/nodalarc_operator/frr_renderer.py`, add the protocol to the template resolver so it maps a routing domain's `protocol: yourprotocol` to your template files.

### Step 4: Test

```yaml
# catalog/nodalarc/sessions/test-newprotocol.yaml
session:
  name: test-newprotocol
segments:
  - id: leo
    source: nodalarc:constellations/earth/leo/earth-leo-ring-36.yaml
  - id: ground
    placement:
      from_site_set: nodalarc:site-sets/earth/leo/earth-leo-starlink-pop-sites.yaml
link_rules:
  - id: leo_access
    topology: {mode: visible_candidates}
    endpoints:
      - select:   {all: [{segment: ground}, {tag: leo}]}
        terminal: {all: [{role: access}, {medium: rf}]}
        min_elevation_deg: 25
      - select:   {segment: leo}
        terminal: {all: [{role: access}, {medium: rf}]}
routing:
  domains:
    - id: earth_domain
      protocol: yourprotocol
      selectors: [{any: [{segment: leo}, {segment: ground}]}]
time:
  start_time: '2026-06-08T00:00:00Z'
  step_seconds: 10
  compression: 1
```

Deploy and verify adjacencies form.

## Adding a Non-FRR Routing Daemon

This requires a new container image and changes to the session pod spec.

### What you need:

1. **Container image** - your routing daemon packaged as a Docker image in `images/`
2. **Interface compatibility** - must work with generated terminal interfaces
   (`islX`, `gndX`, `lo`, `terr0`)
3. **Carrier detection** - must detect link carrier state on interfaces (UP/DOWN/LOWERLAYERDOWN)
4. **Config delivery** - mechanism to inject configuration at session start
5. **Operator integration** - pod spec generation and config delivery in
   `session_deployer.py`

### Constraints:

- The Node Agent wiring model doesn't change. Your daemon receives interfaces with carrier-gated link state, same as FRR.
- The OME and Scheduler don't change. They publish events and dispatch links regardless of what runs inside pods.
- SSH terminal access assumes vtysh. A different routing daemon needs its own CLI mechanism.

## Extension Points

Extensions modify an existing protocol's behavior without replacing it:

| Extension | What it adds |
|-----------|-------------|
| `traffic-engineering` | IS-IS/OSPF TE TLVs, bandwidth advertisement |
| `sr` | Segment routing SID advertisement, SRGB/SRLB |
| `mpls` | MPLS forwarding, label tables, LDP or SR label distribution |

BGP and DSN/DTN-style adapters are future routing families. They should be
added as explicit runtime-supported protocols with templates, resolver
validation, and tests; do not expose them as selectable working modes before
that path exists.

Extensions are handled in the Jinja2 template via conditional blocks:

```jinja2
{% if 'traffic-engineering' in extensions %}
  mpls-te on
  mpls-te router-address {{ loopback_ip }}
{% endif %}
```

## Testing a New Stack

1. Deploy a small curated session, such as `earth-leo-simple.yaml`
2. Verify adjacencies form: `show {protocol} neighbor` on multiple nodes
3. Verify routing works: ping between non-adjacent nodes
4. Verify ground station reachability: ping from GS to satellite loopback
5. Verify reconvergence: wait for a link to go down, verify traffic reroutes
