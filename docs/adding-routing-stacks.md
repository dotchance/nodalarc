# Adding a New Routing Stack

NodalArc's routing stack is pluggable. The default stack is FRRouting (FRR), but the architecture supports replacing it with any containerized routing daemon: Juniper cRPD, Arista cEOS, Cisco IOS-XE, BIRD, or custom implementations.

This document describes the integration points. We are actively working on adding Juniper cRPD as the second supported stack. This guide will be updated with tested, validated steps as that work progresses.

## How It Works Today

The current stack resolution is in `lib/nodalarc/stack_resolver.py`. A session config specifies a protocol and optional extensions:

```yaml
routing:
  protocol: isis          # isis, ospf, or nodalpath
  extensions:
    - traffic-engineering  # sr, te, mpls
```

The stack resolver maps this to:
- Which FRR daemons to enable (zebra, isisd, ospfd, pathd, ldpd, staticd)
- Which Jinja2 config templates to render (in `configs/templates/frr/`)
- Which sysctls and capabilities the pod needs
- Whether segment routing or MPLS is active

Session pods run the official FRR container image (`quay.io/frrouting/frr:10.3.1`) with a NodalArc entrypoint that waits for config delivery, then hands off to FRR's `watchfrr` daemon supervisor.

## Integration Points for a New Stack

To add a new routing daemon, you need to provide:

**1. Container image.** A Docker image containing your routing daemon that can:
- Start from a config file delivered to a known path
- Run with NET_ADMIN, NET_RAW, and SYS_ADMIN capabilities
- Operate on interfaces created by the Node Agent (isl0, isl1, isl2, isl3, gnd0, lo, terr0)

**2. Config templates.** Jinja2 templates in `configs/templates/<stack>/` that generate per-node configuration. Templates receive variables from `build_template_vars()` including:

- `node_id`, `node_type` (satellite or ground_station)
- `plane`, `slot` (orbital indices)
- `hostname`, `router_id`, `ipv4_loopback`, `ipv6_loopback`
- `area_id` (routing area for this node)
- `interface_info` (dict of interface name to link properties: bandwidth, peer node, cross-area flag)
- `gnd_interfaces` (list of ground interface names)
- Segment routing variables if applicable (`sr_enabled`, `srgb_start`, `srgb_end`, `prefix_sid_index`)

**3. Stack resolver entry.** A function in `stack_resolver.py` that returns a `ResolvedStack` with:
- `daemons` list (controls which daemons the entrypoint starts)
- `template_files` (which Jinja2 templates to render)
- `image` (container image name)
- `sysctls` (kernel parameters the pod needs)
- `segment_routing` flag
- `env` (environment variables for sidecars if needed)

**4. Entrypoint compatibility.** The session pod entrypoint needs to know how to start your daemon. Currently the FRR entrypoint waits for a config-ready sentinel, then runs `watchfrr`. A different daemon would need its own entrypoint or a compatible startup mechanism.

## What We Haven't Built Yet

- A generic entrypoint that works across different routing daemons (currently FRR-specific)
- A way to specify the container image in the session YAML (currently hardcoded per stack in the resolver)
- MI adapters for non-FRR daemons (the measurement infrastructure polls vtysh, which is FRR-specific)
- Validated integration with any non-FRR routing stack

## Coming Soon

Juniper cRPD integration is the next routing stack we're adding. That work will validate and refine the integration points described above and produce a tested, repeatable process for adding third-party routing daemons.
