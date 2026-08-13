# Adding Routing Stacks

A routing stack is a workload. A node runs one because its workload profile
says so, not because the platform assumes routers. FRR is the reference
stack and the shipped `frr-router` profile is the reference composition,
but nothing in the substrate defaults to it.

Adding routing capability means one of two things:

1. **Enabling another FRR protocol** through the frr adapter
2. **Bringing a different routing stack** (cRPD, cEOS, XRd, BIRD, your own)
   as a vendor container behind a new profile

## How Routing Stacks Work

Three pieces cooperate, and each lives in a different place:

1. A **profile** (a catalog object) declares the composition: the vendor
   image pinned by digest, the command that starts it, capabilities,
   volumes, the terminal surface, and optionally the `adapter` that renders
   its native configuration
2. An **adapter** (a module under `adapters/`) translates resolved per-node
   facts into the image's native config format. The frr adapter renders the
   Jinja2 templates in `configs/templates/frr/` into per-node files
3. The **session** declares `routing.domains`, each naming a `protocol` and
   selectors. Domain membership derives from the routers: the nodes whose
   effective profile's adapter renders that protocol

Rendered files arrive in the pod as a read-only mount at the path the
profile declares as `config_mount`. The workload's own entrypoint decides
what to do with them. The platform never execs into a container to
configure it.

## Adding an FRR Protocol (Simplest Path)

### Step 1: Create a Jinja2 template

```
configs/templates/frr/{protocol_name}.conf.j2
```

Templates receive resolved facts: the node's loopbacks, its WAN interfaces
with peer and bandwidth context, its Ethernet segment interfaces with
allocated addresses and origination flags, area assignments, and SID
indexes. `lib/nodalarc/template_vars.py` is the authority on what exists;
read it before inventing a variable.

### Step 2: Teach the frr adapter

The frr adapter in `adapters/frr/` selects which templates render for a
node from its routing domain's protocol and declared capabilities. Add your
protocol to that selection.

### Step 3: Declare runtime support

The resolver refuses a session whose domain names a protocol the runtime
cannot execute. Extend the supported protocol set and the adapter's
renderability declaration in `lib/nodalarc/runtime_support.py` so the gate
admits it. Until then the refusal is the correct behavior, not a bug.

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
    apply:
      scheduling:
        selection_policy: {highest_elevation: {}}
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
simulation:
  candidate_limits:
    max_pairs_per_rule: 500
    max_pairs_per_tick: 2000
time:
  start_time: '2026-06-08T00:00:00Z'
  step_seconds: 10
  compression: 1
```

Deploy and verify adjacencies form.

## Bringing a Different Routing Stack

A vendor NOS needs no platform code until rendered configuration enters the
picture.

1. **Author a profile.** The vendor image by digest from the vendor's own
   registry, the command that starts it, the capabilities it needs, and a
   terminal surface: `ssh` if the image runs its own SSH daemon, `exec`
   into its CLI otherwise. If the stack is configured entirely by its
   authored command and environment, stop here. The profile is the whole
   integration.
2. **With rendering.** If the stack needs per-node native configuration
   generated from resolved facts, write an adapter module under
   `adapters/`, add it to the registry in `adapters/registry.py`, and
   declare it in runtime support. That single edit is the whole coupling
   surface between core and your technology.

### Constraints

- The Node Agent wiring model does not change. Your stack receives
  manifest-named interfaces with carrier-gated link state, same as FRR:
  WAN interfaces such as `isl0` and `gnd0`, Ethernet ports such as `terr0`
  or `bus0`, and `lo`.
- The OME and Scheduler do not change. They publish events and dispatch
  links regardless of what runs inside pods.
- Your stack must react to interface carrier state (UP, DOWN,
  LOWERLAYERDOWN). That reaction is the experiment.
- Terminal access is whatever the profile declares. Nothing assumes vtysh.

## Capabilities

IS-IS and OSPF domains can declare capabilities in the session, and the frr
templates branch on them:

| Capability | What it adds |
|-----------|-------------|
| `traffic_engineering` | IS-IS/OSPF TE TLVs, bandwidth advertisement |
| `segment_routing` | SID advertisement, SRGB/SRLB |
| `mpls` | MPLS forwarding, label tables, label distribution |

BGP is structurally defined in the grammar and gated by runtime support
until its execution path exists. DTN bundle *boundary adapters* are gated
the same way. DTN *daemons* need no gate at all: a bundle protocol is a
workload, and the shipped uD3TN profiles run one today without touching the
routing grammar.

## Testing a New Stack

1. Deploy a small curated session, such as `earth-leo-simple.yaml`
2. Verify adjacencies form: `show {protocol} neighbor` on multiple nodes
3. Verify routing works: ping between non-adjacent nodes
4. Verify ground station reachability: ping from GS to satellite loopback
5. Verify reconvergence: wait for a link to go down, verify traffic reroutes
