# Adding a New Routing Stack

Nodal Arc supports pluggable routing stacks. Each stack is a container image with configuration templates and a measurement adapter. This guide walks through adding a new stack.

## Overview

A routing stack consists of three parts:

1. **Container image** - the routing daemon (e.g., FRR, BIRD, OVS)
2. **Config templates** - Jinja2 templates that generate per-node configuration
3. **MI adapter** - Python module that collects protocol events from the running daemon

## Directory Structure

Create a new directory under `configs/routing-stacks/`:

```
configs/routing-stacks/my-stack/
  stack.yaml          # Stack metadata
  zebra.conf.j2       # Template for each daemon
  mydaemon.conf.j2
```

## Step 1: Create stack.yaml

Define the container image and daemons:

```yaml
name: my-stack
image: myregistry/my-router:latest
daemons:
  - zebra
  - mydaemon
```

For FRR-based stacks, add segment routing fields if applicable:

```yaml
srgb_start: 16000
srgb_end: 23999
```

## Step 2: Write Jinja2 Templates

Templates receive the full variable namespace from `build_template_vars()`. Key variables available:

```
node_id          - "sat-P02S03" or "gs-hawthorne"
node_type        - "satellite" or "ground_station"
plane            - orbital plane index (int)
slot             - slot index within plane (int)
hostname         - same as node_id
router_id        - loopback IPv4 address (dotted decimal)
loopback_ipv4    - "10.2.3.1"
loopback_ipv6    - "fd00::2:3:1"
area_id          - routing area (format depends on protocol)
isis_net         - IS-IS NET address (hex string)
interfaces       - list of interface dicts (see below)
srgb_start       - SR global block start label
srgb_end         - SR global block end label
prefix_sid_index - per-node SID index
```

Each interface dict contains:

```
name             - "eth1", "eth2", etc.
peer_id          - neighbor node_id
link_type        - "intra_plane_isl", "cross_plane_isl", "ground_uplink", etc.
ipv4             - interface IPv4 address
ipv6             - interface IPv6 address
cross_area       - True if this link crosses routing areas
peer_area_id     - neighbor's area_id
peer_loopback_ipv4 - neighbor's loopback (for SR label stacks)
```

### Example: FRR IS-IS Template

See `configs/routing-stacks/frr-isis-sr/isisd.conf.j2` for a complete example. Key patterns:

```jinja2
router isis NA
  net {{ isis_net }}
  is-type level-2-only
  metric-style wide
  segment-routing on
  segment-routing global-block {{ srgb_start }} {{ srgb_end }}
  segment-routing node-msd 8
  segment-routing prefix {{ loopback_ipv4 }}/32 index {{ prefix_sid_index }}
!
{% for iface in interfaces %}
interface {{ iface.name }}
  ip router isis NA
  isis circuit-type level-2-only
  isis network point-to-point
!
{% endfor %}
```

### Example: FRR OSPF Template

See `configs/routing-stacks/frr-ospf-te/ospfd.conf.j2`. OSPF uses dotted-decimal area IDs (e.g., `0.0.0.0`), not IS-IS hex format.

## Step 3: Write the MI Adapter

Create a Python module in `measurement/adapters/`:

```python
"""MI adapter for my-stack routing daemon."""

import logging
from nodalarc.models.metrics import AdapterEvent

log = logging.getLogger(__name__)


class MyStackAdapter:
    """Collect protocol events from my-stack daemon."""

    def __init__(self, node_id: str, container_pid: int):
        self.node_id = node_id
        self.container_pid = container_pid

    def poll(self) -> list[AdapterEvent]:
        """Poll the daemon for new events.

        Called periodically by the MI main loop. Return a list of
        AdapterEvent instances for any new protocol state changes
        (adjacency up/down, route changes, etc.).
        """
        events = []
        # Implementation: exec vtysh commands, parse log files, etc.
        return events
```

The adapter must:

- Accept `node_id` and `container_pid` in its constructor
- Implement a `poll() -> list[AdapterEvent]` method
- Return `AdapterEvent` instances with `event_type` and `event_data` fields
- Handle transient failures gracefully (log warnings, don't crash)

### Existing Adapters for Reference

- `measurement/adapters/frr_isis.py` - IS-IS adapter (vtysh poll + log parse)
- `measurement/adapters/frr_ospf.py` - OSPF adapter (vtysh poll + log parse)

Both adapters demonstrate:
- Parsing `vtysh -c "show isis neighbor"` / `vtysh -c "show ip ospf neighbor"` output
- Watching FRR log files for adjacency state changes
- Producing clean `AdapterEvent` records from messy daemon output

## Step 4: Test with custom-example

Start small. Deploy with the custom-example constellation (4 satellites, 2 ground stations):

1. Create a session config that references your new stack:

```yaml
session:
  name: my-stack-test

constellation: configs/constellations/custom-example.yaml
ground_stations: configs/ground-stations/sets/us-conus.yaml

routing:
  stack: configs/routing-stacks/my-stack
  area_assignment:
    strategy: flat
    gs_area_id: "49.0001"
```

2. Deploy:

```bash
sudo make session DEFAULT_SESSION=configs/sessions/my-stack-test.yaml
```

3. Exec into a satellite and verify the daemon is running:

```bash
kubectl exec -it -n nodalarc sat-p00s00 -- vtysh -c "show running-config"
```

4. Run the ISL failure scenario to test convergence detection:

```bash
uv run python -m tools.na_scenario --scenario configs/scenarios/isl-failure.yaml
```

5. Inspect the session database for adapter events:

```bash
sqlite3 /var/nodalarc/sessions/my-stack-test/nodalarc.db \
  "SELECT * FROM adapter_events LIMIT 10"
```
