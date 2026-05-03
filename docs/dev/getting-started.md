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
git clone https://github.com/nodalarc/nodalarc.git
cd nodalarc

# Bootstrap the host (installs K3s, Docker, Helm, kernel modules)
# Skip if you already have these
sudo scripts/bootstrap-host.sh

# Install dependencies and build everything
make all
```

`make all` installs Python/Node.js dependencies, builds all Docker images, deploys the platform via Helm, and starts a default session. ~3-5 minutes from scratch.

## Verify the Installation

```bash
# Check everything is running
sudo make status

# Run unit tests
make test
```

Unit tests should report 996+ passing. If any fail on a fresh checkout, something is wrong with the setup, not the code.

Open http://localhost:3000 to verify the visualization loads and satellites are visible.

## Development Loop

The typical development cycle:

1. **Edit code** in your service/component
2. **Run unit tests** to verify correctness: `make test`
3. **Build and deploy** the changed service: `sudo make deploy-<service>`
4. **Verify** the change works in the running system (browser, logs, kubectl)

### Available deploy targets

```bash
sudo make deploy-ome          # Orbital Mechanics Engine
sudo make deploy-scheduler    # Topology Dispatcher
sudo make deploy-node-agent   # Node Agent DaemonSet
sudo make deploy-vs-api       # VS-API server
sudo make deploy-operator     # K8s Operator
sudo make deploy-vf           # Visualization Frontend
sudo make deploy-all          # All services at once
```

Each target builds the Docker image, loads it into the cluster, and does a rolling restart. The running session stays up — no teardown needed.

### Frontend development

The VF frontend can be developed with hot reload:

```bash
cd frontend/vf
npm run dev
```

This starts a Vite dev server on port 5173 with hot module replacement. Changes to React components appear instantly in the browser without rebuilding the Docker image.

For final verification, build and deploy the full image:
```bash
sudo make deploy-vf
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
frontend/vf/            Visualization Frontend (React 19 + Three.js)
nodalpath/              NodalPath engine (self-contained)
images/                 Base container images (FRR, probe, forwarding sidecar)
deploy/helm/            Helm chart (templates, values)
configs/                Runtime configs (constellations, sessions, FRR templates)
tools/                  Operational tools (teardown, scenario inject)
tests/unit/             Unit tests
tests/integration/      Integration tests
```

## Key Files (Start Here)

| File | Why It Matters |
|------|---------------|
| `lib/nodalarc/nats_channels.py` | All NATS subject definitions. Single source of truth. |
| `lib/nodalarc/models/events.py` | Pydantic event models shared across all services |
| `services/scheduler/dispatcher.py` | `_reconcile_links` — the only path to the Node Agent |
| `services/node_agent/handlers.py` | BatchLinkUp/Down: the kernel operations |
| `services/nodalarc_operator/session_deployer.py` | Session creation: pods, placement, config delivery |
| `services/ome/main.py` | OME entry point, pacing thread, publisher thread |
| `frontend/vf/src/App.tsx` | Frontend entry point |
| `frontend/vf/src/globe/links.ts` | ISL/ground link rendering (batched LineSegments2) |
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
| sat-P{nn}S{nn} | Pod | per satellite | FRR routing daemon |
| gs-{name} | Pod | per ground station | FRR routing daemon |

## Next Steps

- [Architecture](architecture.md) — understand how the components interact
- [Development Workflow](dev-workflow.md) — make targets, build caching, iterative development
- [Invariants](invariants.md) — the rules you cannot break
- [Testing](testing.md) — what to test and how
