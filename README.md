# NodalArc

Orbital network emulation platform. Deploys real routing stacks (IS-IS, OSPF, SR-MPLS, BGP) on satellite constellation topologies driven by real orbital mechanics. Every satellite and ground station runs a real FRR routing daemon with emulated ISL and ground links. Latency, bandwidth, and link-state dynamics are driven by orbital geometry.

![NodalArc Globe View](docs/images/vf-globe-overview.png)
<!-- TODO: Hero screenshot - 3D globe with full constellation, ISL links, ground stations -->

## Quick Start

NodalArc runs on any Kubernetes cluster: full K8s, K3s, or any conformant distribution. The bootstrap script is provided for fresh machines that don't already have a cluster:

```bash
# One-time host setup (see below for what this does)
sudo scripts/bootstrap-host.sh

# Build and deploy everything
make all
```

Open http://localhost:3000. The constellation is live.

**Already have Kubernetes?** Skip the bootstrap script. You need Docker (for building images), Node.js 22+, Helm, and uv (Python). Then `make all` works against your existing cluster via `KUBECONFIG`.

**What does `bootstrap-host.sh` do?** It installs the following on a bare Ubuntu/Debian machine. Each step is skipped if already present:
- **Docker** - for building container images (via `get.docker.com`)
- **K3s** - lightweight Kubernetes (via `get.k3s.io`, with Traefik disabled)
- **kubectl** and **Helm** - Kubernetes CLI tools
- **Node.js 22** - for building the visualization frontend
- **uv** - Python package manager
- **Kernel modules** - `mpls_router` and `mpls_iptunnel` for MPLS forwarding
- **Sysctls** - IPv4/IPv6 forwarding and MPLS platform labels (written to `/etc/sysctl.d/99-nodalarc.conf`)

The script does NOT modify an existing Kubernetes installation. If K3s or kubectl are already present, those steps are skipped.

`make all` installs dependencies, builds container images, loads them into the cluster, deploys the platform via Helm, and launches a 36-satellite constellation with OSPF routing and 6 ground stations. About 2 minutes from a fresh checkout on a single machine.

## What You Get

### Real-Time 3D Visualization

The web UI shows the full constellation orbiting the Earth in real time. Watch ISL links form and break as satellites move, ground stations hand off between overhead satellites, and routing adjacencies react to topology changes.

![3D Globe with Links](docs/images/vf-globe-links.png)
<!-- TODO: Screenshot showing globe with ISL links and ground station connections -->

### Network Topology View

See the constellation as a traditional network graph with real-time link state, latency, and routing metrics.

![Topology Graph](docs/images/vf-topology-graph.png)
<!-- TODO: Screenshot showing 2D topology graph with latency labels -->

### Interactive Router CLI from the Browser

Open a persistent SSH terminal to any satellite or ground station directly from the UI. You land in vtysh — the same CLI experience as a real router. Run `show ip route`, `configure terminal`, `write memory`, or any FRR command. Multiple sessions stay alive in tabs — switch between nodes instantly.

Power users can also SSH directly with PuTTY, iTerm, SecureCRT, or any SSH client. See [Terminal Access](docs/terminal-access.md) for details.

![Router Commands](docs/images/vf-router-commands.png)
<!-- TODO: Screenshot showing terminal panel with vtysh session -->

### Deploy Constellations from the Wizard

Configure and launch new sessions from the browser. Choose a constellation geometry, routing stack, and ground station set, then deploy without touching the command line.

![Session Wizard](docs/images/vf-session-wizard.png)
<!-- TODO: Screenshot showing session wizard -->

### Programmable API

The VS-API provides full REST and WebSocket access to all constellation state: node positions, link metrics, routing tables, path traces. Build custom dashboards, automated tests, or integration scripts.

```bash
# Fetch API token (auto-generated on startup)
TOKEN=$(curl -s http://localhost:8080/api/v1/auth/token | python3 -c "import json,sys; print(json.load(sys.stdin)['token'])")
```

```bash
# Get full constellation state
curl -s -H "Authorization: Bearer $TOKEN" http://localhost:8080/api/v1/state | python3 -m json.tool
```

```bash
# Trace the forwarding path between two ground stations
curl -s -X POST -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  http://localhost:8080/api/v1/trace \
  -d '{"src_node": "gs-hawthorne", "dst_node": "gs-frankfurt"}'
```

## Commands

| Command | What it does |
|---------|-------------|
| `make all` | Build everything and start a constellation session |
| `sudo make session` | Start a session (or switch to a different one) |
| `sudo make teardown` | Stop the session and clean up |
| `sudo make nuke` | Remove everything including local registry and K3s image cache — true fresh slate |

Start a specific session:
```bash
sudo make session DEFAULT_SESSION=configs/sessions/starlink-176-nodalpath.yaml
```

Session configs live in `configs/sessions/`. Each defines a constellation, ground stations, and routing stack. Or use the session wizard in the UI to configure and deploy from the browser.

## Multi-Node Deployment

For clusters with multiple Kubernetes nodes, configure a container registry in `config.mk`:

```makefile
REGISTRY_PREFIX ?= myregistry.local:5000/
HELM_EXTRA_ARGS ?= --set imagePullPolicy=IfNotPresent \
    --set images.frr=myregistry.local:5000/nodalarc/frr:latest \
    ...
```

See `config.mk.example` for a complete template. With `REGISTRY_PREFIX` set, `make load` pushes to the registry instead of importing locally. All nodes pull from the registry.

Placement policies control pod distribution across nodes:
- `allOnOne` - all pods on one node (default, single-node)
- `planePerNode` - one orbital plane per node, cross-plane ISLs via VXLAN
- `planeGroupPerNode` - adjacent planes grouped per node

## Repository Structure

```
lib/          Shared Python library (nodalarc package)
services/     Backend services (ome, scheduler, node_agent, vs_api, operator, measurement)
frontend/     Visualization frontend (React + Three.js)
nodalpath/    NodalPath extension (self-contained subtree)
images/       Base container images (FRR, probe)
deploy/       Helm chart
configs/      Runtime configuration (constellations, ground stations, sessions)
tools/        CLI tools (teardown, scenario inject)
scripts/      Host bootstrap and infra scripts
tests/        Unit and integration tests
docs/         Documentation
```

## Documentation

- [Getting Started](docs/getting-started.md) - install, deploy, and explore the UI
- [Architecture](docs/architecture.md) - system design, data flow, how it works
- [Configuration Reference](docs/configuration-reference.md) - sessions, constellations, ground stations, routing stacks
- [VS-API Reference](docs/vs-api-reference.md) - REST and WebSocket API for automation
- [Building Visualization Clients](docs/building-visualization-clients.md) - WebSocket/REST integration guide
- [Extending Propagators](docs/extending-propagators.md) - replacing or extending the orbital propagator
- [Adding Routing Stacks](docs/adding-routing-stacks.md) - integrating new routing daemons
- [Performance and Scaling](docs/performance-baseline.md) - resource usage and scaling characteristics
- [Teardown and Cleanup](docs/operations/teardown.md) - session switching, teardown, cleanup levels
- [CLI Reference](docs/cli-reference.md) - command-line examples for power users
- [Developer Guide](docs/developer-guide.md) - rebuilding services, running tests, code conventions

## License

NodalArc is source available under the [NodalArc Source Available License 1.0](LICENSE). You can use, modify, and distribute it freely. You cannot offer it as a hosted service or build a competing commercial product from it. See the LICENSE file for full terms.

Copyright 2024-2026 .chance (dotchance)
