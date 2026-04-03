# Getting Started with NodalArc

Deploy a satellite constellation emulation lab from scratch. This guide walks through every step from a bare Linux machine to a running 176-satellite Starlink constellation with IS-IS routing, ground station handoffs, and a 3D visualization.

## Prerequisites

- Linux host: Ubuntu 22.04+ or Debian 12+
- Minimum: 8GB RAM, 4 CPU cores, 40GB disk
- Recommended: 32GB RAM, 8 cores (for 176+ satellite constellations)
- A Kubernetes cluster (any distribution: K3s, K8s, EKS, etc.)
- Root access (sudo) for kernel module configuration

NodalArc runs on any Kubernetes cluster. If you already have one, skip to "Already have Kubernetes?" below.

## Step 1: Bootstrap the Host

For a fresh machine with no Kubernetes cluster:

```bash
sudo scripts/bootstrap-host.sh
```

This script installs the following. Each step is skipped if the tool is already present:

| Component | What | Why |
|-----------|------|-----|
| Docker | Container build engine | Builds NodalArc images |
| K3s | Lightweight Kubernetes | Runs the constellation pods (skipped if K3s/kubectl already present) |
| kubectl | Kubernetes CLI | Cluster management |
| Helm | Kubernetes package manager | Deploys the NodalArc platform chart |
| Node.js 22 | JavaScript runtime | Builds the visualization frontend |
| uv | Python package manager | Installs Python dependencies |
| MPLS kernel modules | `mpls_router`, `mpls_iptunnel` | Enables MPLS label forwarding in the emulated network |
| Sysctls | IP forwarding, MPLS labels | Written to `/etc/sysctl.d/99-nodalarc.conf` |

The script is idempotent. Safe to run multiple times. It does NOT modify an existing Kubernetes installation.

### Already have Kubernetes?

Skip the bootstrap script. Make sure you have:
- Docker (for building images)
- Node.js 22+ (for frontend builds)
- Helm (for deploying the platform chart)
- uv (Python package manager: `curl -LsSf https://astral.sh/uv/install.sh | sh`)
- MPLS kernel modules loaded (`modprobe mpls_router mpls_iptunnel`)
- `KUBECONFIG` pointing to your cluster

Then proceed to Step 2.

## Step 2: Build and Deploy

```bash
make all
```

This builds all container images, deploys the platform, and launches a constellation session. About 3-5 minutes from a fresh checkout.

When complete you'll see:
```
=== NodalArc is running ===
VF:     http://localhost:3000
VS-API: http://localhost:8080
```

## Step 3: Explore the Constellation

Open http://localhost:3000 in a browser. This is the primary interface for working with NodalArc.

### 3D Globe View

The main view is a 3D globe showing the full constellation in real time.

![3D Globe View](images/vf-globe-view.png)
<!-- TODO: Screenshot showing globe with satellite orbits, ISL links, and ground stations -->

Satellites orbit the Earth in their configured planes. ISL links are drawn between connected satellites, both intra-plane links (within the same orbital plane) and cross-plane links (between adjacent planes). Ground stations are shown at their geographic locations with active uplink connections drawn when a satellite is overhead.

As you watch, you'll see links appear and disappear as satellites move in and out of line-of-sight range. Ground station connections hand off from one satellite to the next as the constellation moves overhead.

### Topology Graph

The topology view shows the network as a traditional graph (nodes and links) with real-time link state and latency.

![Topology View](images/vf-topology-view.png)
<!-- TODO: Screenshot showing 2D topology graph with latency labels -->

### Satellite and Ground Station Details

Click any satellite or ground station to see its details: position, routing neighbors, active interfaces, and link metrics.

![Node Detail Panel](images/vf-node-detail.png)
<!-- TODO: Screenshot showing detail panel for a satellite with IS-IS neighbors -->

### Event Log

The event log shows real-time link state changes, ground station handoffs, and convergence events as they happen.

![Event Log](images/vf-event-log.png)
<!-- TODO: Screenshot showing event log with LinkUp/LinkDown/handoff events -->

### Running Router Commands

Select any satellite or ground station and use the command panel to run standard routing commands directly from the browser:

- `show isis neighbor` - see which neighbors this node has formed adjacencies with
- `show ip route` - view the full routing table
- `show isis database` - inspect the link-state database
- `show ip route 0.0.0.0/0` - check the default route (ground stations should be preferred)

![Router Command Panel](images/vf-router-commands.png)
<!-- TODO: Screenshot showing the command panel with vtysh output -->

This is the easiest way to inspect routing state across the constellation.

### Path Tracing

Trace the forwarding path between any two nodes in the constellation. Select a source and destination, and NodalArc shows the hop-by-hop path with per-hop latency.

![Path Trace](images/vf-path-trace.png)
<!-- TODO: Screenshot showing path trace results between two ground stations -->

## Step 4: Launch a Different Session

### From the UI

The session wizard lets you configure and launch new constellation sessions from the browser. Choose a constellation (Starlink, OneWeb, Kuiper, or custom), select a routing stack (IS-IS, OSPF, SR-MPLS), pick ground stations, and deploy.

![Session Wizard](images/vf-session-wizard.png)
<!-- TODO: Screenshot showing the session wizard with constellation selection -->

### From the Command Line

```bash
sudo make session DEFAULT_SESSION=configs/sessions/starlink-176-nodalpath.yaml
```

No teardown needed. The platform switches sessions automatically.

Available sessions:
- `starlink-176-isis-te.yaml` - IS-IS with traffic engineering (default)
- `starlink-176-nodalpath.yaml` - NodalPath centralized path computation

## Step 5: Shut Down

```bash
sudo make teardown
```

This cleanly stops the session and removes all platform resources. Wait for "Teardown complete" before starting a new session.

To remove everything including built images and dependencies:

```bash
sudo make nuke
```

## Using the API

The VS-API at http://localhost:8080 provides programmatic access to all constellation state: node positions, active links, latencies, and path traces. This is useful for scripting, automation, or building custom dashboards.

API requests require a Bearer token. Fetch it first:

```bash
TOKEN=$(curl -s http://localhost:8080/api/v1/auth/token | python3 -c "import json,sys; print(json.load(sys.stdin)['token'])")
```

Then use it in all requests:

```bash
# Get the full constellation state
curl -s -H "Authorization: Bearer $TOKEN" http://localhost:8080/api/v1/state | python3 -m json.tool | head -30
```

```bash
# Trace the forwarding path between two ground stations
curl -s -X POST -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  http://localhost:8080/api/v1/trace \
  -d '{"src_node": "gs-hawthorne", "dst_node": "gs-frankfurt"}'
```

See the [VS-API Reference](vs-api-reference.md) for full documentation.

## Multi-Node Setup

For clusters with multiple nodes, you need a container registry accessible to all nodes. Copy `config.mk.example` to `config.mk` and configure your registry:

```bash
cp config.mk.example config.mk
```

See `config.mk.example` for a complete template with all the settings you need to change. Once configured, `make all` works the same way. Images are pushed to your registry and all nodes pull from it automatically.

### Placement Policies

Multi-node sessions distribute satellite pods across nodes:

- **allOnOne** - all pods on one node (default)
- **planePerNode** - each orbital plane on a separate node
- **planeGroupPerNode** - groups of adjacent planes per node

Set the policy in the session YAML:
```yaml
placement:
  policy: planePerNode
```

## Troubleshooting

**Session not starting:** Check the status with `sudo make status`. If pods are stuck, try `sudo make teardown` followed by `sudo make session`.

**Visualization not loading:** Make sure http://localhost:3000 is accessible. If running on a remote machine, you may need to forward port 3000.

**No routing adjacencies forming:** Wait 30-60 seconds for the routing protocol to converge on large topologies. Use the router command panel in the UI to run `show isis neighbor` on any node.

**Teardown stuck:** The teardown script handles stuck states automatically. If it still fails, run `sudo make nuke` for a complete reset.

## Next Steps

- [Architecture Overview](architecture.md) - how the system works under the hood
- [VS-API Reference](vs-api-reference.md) - REST and WebSocket API for automation
- [Configuration Reference](configuration-reference.md) - session YAML, constellation, ground station schemas
- [CLI Reference](cli-reference.md) - command-line examples for power users
- [Developer Guide](developer-guide.md) - rebuilding services, running tests, code changes
