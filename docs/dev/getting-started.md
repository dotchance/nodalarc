# Development Setup

## Prerequisites

- Linux (Ubuntu 22.04+ or Debian 12+)
- Python 3.14+
- Node.js 22+
- Docker
- Kubernetes cluster (K3s recommended for development)
- uv (Python package manager)

## First-Time Setup

```bash
# Clone the repository
git clone https://github.com/dotchance/nodalarc.git
cd nodalarc

# Bootstrap the host (installs K3s, Docker, Helm, kernel modules)
# Skip if you already have these
sudo scripts/bootstrap-host.sh

# Install dependencies and build everything
make all
```

`make all` is the clean-state bring-up path. It installs Python/Node.js dependencies, builds all Docker images, loads them into the selected image destination, deploys the platform via Helm, starts a default session, and prints status. ~3-5 minutes from scratch.

If a previous NodalArc deployment may still exist, validate from square one instead:

```bash
make nuke && make all
```

If the platform is already running and you only need to update code, use:

```bash
make build && make load && make upgrade
```

## Verify the Installation

```bash
# Check everything is running
make status

# Run unit tests
make test
```

Unit tests should pass on a fresh checkout. If any fail, something is wrong with the setup or the code.

Open http://localhost:3000 to verify the visualization loads and satellites are visible.

## Development Loop

The typical development cycle:

1. **Edit code** in your service/component
2. **Run unit tests** to verify correctness: `make test`
3. **Build and deploy** the changed service: `make deploy-<service>`
4. **Verify** the change works in the running system (browser, logs, kubectl)

For broad platform changes that need a Helm upgrade, use `make build && make load && make upgrade`. For a destructive refresh, use `make build && make load && make reinstall && make session`.

### Available deploy targets

```bash
make deploy-ome          # Orbital Mechanics Engine
make deploy-scheduler    # Topology Dispatcher
make deploy-node-agent   # Node Agent DaemonSet
make deploy-vs-api       # VS-API server
make deploy-operator     # K8s Operator
make deploy-vf           # Visualization Frontend
make deploy-all          # All services at once
```

Each target builds the Docker image, loads it into the cluster, and does a rolling restart. The running session stays up - no teardown needed.

### Frontend development

The VF frontend can be developed with hot reload:

```bash
cd frontend
npm run dev
```

This starts a Vite dev server on port 5173 with hot module replacement. Changes to React components appear instantly in the browser without rebuilding the Docker image.

For final verification, build and deploy the full image:
```bash
make deploy-vf
```

## Project Structure

```
lib/nodalarc/           Shared Python library (models, proto, geo, NATS channels)
services/ome/           Orbital Mechanics Engine
services/scheduler/     Topology Dispatcher
services/node_agent/    Node Agent (DaemonSet, kernel netlink ops)
services/vs_api/        VS-API (FastAPI REST + WebSocket)
services/nodalarc_operator/  K8s Operator (kopf, session lifecycle)
services/measurement/   Measurement Infrastructure (probes, adapters)
frontend/               Visualization Frontend (React 19 + R3F + Three.js)
images/                 Base container images (FRR, probe)
deploy/helm/            Helm chart (templates, values)
catalog/nodalarc/       Reusable config primitives (bodies, terminals, orbits, nodes, sites, constellations)
catalog/nodalarc/sessions/      Assembled, deployable sessions
configs/                Platform config, ephemerides, and FRR templates
scripts/                Lifecycle and operational scripts
tools/                  Python report, scenario, compare, and reconfig CLIs
tests/unit/             Unit tests
tests/integration/      Integration tests
```

## Key Files (Start Here)

| File | Why It Matters |
|------|---------------|
| `lib/nodalarc/nats_channels.py` | All NATS subject definitions. Single source of truth. |
| `lib/nodalarc/models/events.py` | Pydantic event models shared across all services |
| `services/scheduler/dispatcher.py` | `_reconcile_links` - the automatic schedule-progression path to the Node Agent; explicit operator repair is the serialized exception |
| `services/node_agent/handlers.py` | BatchLinkUp/Down: the kernel operations |
| `services/nodalarc_operator/session_deployer.py` | Session creation: pods, placement, config delivery |
| `services/ome/main.py` | OME entry point, pacing thread, publisher thread |
| `frontend/src/App.tsx` | Frontend entry point |
| `frontend/src/globe/r3f/Links.tsx` | ISL/ground link rendering (batched LineSegments2) |
| `configs/templates/frr/` | Jinja2 templates for FRR config generation |
| `deploy/helm/values.yaml` | Helm chart default values |

## Branch Discipline

**Never commit directly to main.** Always work on a feature branch:

```bash
git checkout -b feature/my-change
# ... make changes, test ...
git add <specific files>
git commit -m "Description of what changed and why"
```

Merge to main only when the change is verified and approved.

## What's Running in the Cluster

When a session is deployed, the `nodalarc` namespace contains:

| Component | Kind | Count | Role |
|-----------|------|-------|------|
| nodalarc-ome | Deployment | 1 | Orbital mechanics, event publishing |
| nodalarc-scheduler | Deployment | 1 | Topology dispatch to Node Agent |
| nodalarc-node-agent | DaemonSet | 1 per node | Kernel ops (veth, VXLAN, tc) |
| nodalarc-vs-api | Deployment | 1 | REST/WebSocket API |
| nodalarc-operator | Deployment | 1 | Session lifecycle, pod creation |
| nodalarc-nats | StatefulSet | 1 | NATS JetStream |
| nodalarc-vf | Deployment | 1 | Frontend (nginx + React) |
| {namespace}-sat-p{nn}s{nn} | Pod | per satellite | FRR routing daemon |
| {site}-{node} (e.g. earth-cl-santiago-gw1) | Pod | per ground node | FRR routing daemon |

## Next Steps

- [Architecture](architecture.md) - understand how the components interact
- [Development Workflow](dev-workflow.md) - make targets, build caching, iterative development
- [Invariants](invariants.md) - the rules you cannot break
- [Testing](testing.md) - what to test and how
