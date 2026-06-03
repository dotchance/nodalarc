# Adding Routing Stacks

NodalArc uses FRRouting as the routing engine inside every session pod. Adding a new routing protocol means either:

1. **Enabling an FRR daemon** that's already in the image but not yet templated (e.g., `bgpd`, `ldpd`, `eigrpd`)
2. **Integrating a different routing daemon** (e.g., BIRD, OpenBGPd, cRPD)

## How Routing Stacks Work

Each session pod runs FRR with a configuration generated from Jinja2 templates at session creation time. The template system:

1. Session YAML specifies `routing.protocol` and `routing.extensions`
2. The Operator's `frr_renderer.py` resolves which template to use
3. The template receives variables: loopback IPs, interface IPs, area assignments, SID indexes, ground station prefixes
4. Rendered output is `frr.conf` + `daemons` file delivered to the pod

## Adding an FRR Protocol (Simplest Path)

### Step 1: Create a Jinja2 template

```
configs/templates/frr/{protocol_name}.conf.j2
```

The template has access to:

| Variable | Type | Content |
|----------|------|---------|
| `node_id` | str | e.g., `space-sat-p00s00` or `ground-gs-hawthorne` |
| `node_type` | str | `satellite` or `ground_station` |
| `loopback_ip` | str | Node's loopback address |
| `interfaces` | list | All interfaces with peer info, IP, bandwidth |
| `area_id` | str | Routing area this node belongs to |
| `plane` | int | Orbital plane index |
| `slot` | int | Slot within plane |
| `terrestrial_prefixes` | list | Ground station prefix advertisements |
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

In `services/nodalarc_operator/frr_renderer.py`, add the protocol to the template resolver so it maps `routing.protocol: yourprotocol` to your template files.

### Step 4: Test

```yaml
# configs/sessions/test-newprotocol.yaml
session:
  name: test-newprotocol
identity:
  mode: segment_namespaced
segments:
  - id: space
    kind: constellation
    source: configs/constellations/demo-36.yaml
    namespace: space
    central_body: earth
  - id: ground
    kind: ground_set
    source: configs/ground-stations/sets/demo.yaml
    namespace: ground
    reference_body: earth
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
  protocol: yourprotocol
```

Deploy and verify adjacencies form.

## Adding a Non-FRR Routing Daemon

This requires a new container image and changes to the session pod spec.

### What you need:

1. **Container image** - your routing daemon packaged as a Docker image in `images/`
2. **Interface compatibility** - must work with the existing interface model (isl0-3, gnd0, lo, terr0)
3. **Carrier detection** - must detect link carrier state on interfaces (UP/DOWN/LOWERLAYERDOWN)
4. **Config delivery** - mechanism to inject configuration at session start
5. **Operator integration** - pod spec generation in `session_deployer.py`

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

Extensions are handled in the Jinja2 template via conditional blocks:

```jinja2
{% if 'traffic-engineering' in extensions %}
  mpls-te on
  mpls-te router-address {{ loopback_ip }}
{% endif %}
```

## Testing a New Stack

1. Deploy a small constellation (demo-36)
2. Verify adjacencies form: `show {protocol} neighbor` on multiple nodes
3. Verify routing works: ping between non-adjacent nodes
4. Verify ground station reachability: ping from GS to satellite loopback
5. Verify reconvergence: wait for a link to go down, verify traffic reroutes
