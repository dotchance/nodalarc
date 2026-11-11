# Development Setup

## Prerequisites

- Linux (Ubuntu 22.04+ or Debian 12+)
- git, to clone the repository (`sudo apt-get install git`)
- Python 3.14+
- Node.js 22+, for work on the page (`make test`, `make test-frontend`, `npm run dev`); the bootstrap does not install it
- Docker
- Kubernetes cluster (K3s recommended for development)
- uv (Python package manager)

## First-Time Setup

On the build machine:

```bash
git clone https://github.com/dotchance/nodalarc.git
cd nodalarc
# make, Docker, kubectl, Helm, uv and crane; skip it if they are installed
sudo scripts/bootstrap-dev-machine.sh
```

When the bootstrap adds your account to the docker group, it says so: log out
and back in so your account can use Docker.

### A single machine

When the build machine is also the only K3s server, build it with
`sudo scripts/build-k3s-node.sh` and run `make all`.

### A cluster of several servers

From the checkout on the build machine, name every server by its ssh address,
the first one first. Your account on each server needs sudo. The script runs
unattended when the build machine logs in to every server with an ssh key and
sudo there asks for no password. Without that, it asks for each password and
waits: the ssh logins at the start, the sudo password as each server is built.
An INPUT NEEDED notice marks every such prompt; ssh gives up on an unanswered
login after 2 minutes and sudo after 5 (their default limits).

```bash
scripts/build-k3s-cluster.sh <first server> <server> ...
make all
```

The script runs `scripts/build-k3s-node.sh` on every server; the first one
starts the cluster and runs the image registry on port 5000
(`--registry-port` chooses another). It then connects the build machine: the
kubeconfig in `~/.kube/nodalarc.yaml`, `KUBECONFIG` and `REGISTRY_HOST` in
`config.mk`, and the registry in Docker's `insecure-registries`.

The registry can also run elsewhere (any OCI registry that serves plain HTTP on
the private network); see
[Container Registry Setup](../ops/multi-node.md#container-registry-setup).

`make all` is the clean-state bring-up path. It installs the Python dependencies, builds all Docker images (the page compiles inside its own image), loads them into the selected image destination, deploys the platform via Helm, starts a default session, and prints status. ~3-5 minutes from scratch.

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

Open the Visualization address `make status` prints (http://localhost:3000 on a single machine) to verify the visualization loads and satellites are visible.

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

## Make Target Reference

### Primary Targets

| Target | Requires sudo | Description |
|--------|:---:|-------------|
| `make all` | no | Clean-state pipeline: deps -> build -> load -> install -> session -> status |
| `make deps` | no | Install Python dependencies |
| `make build` | no | Build all Docker images |
| `make load` | no | Import images to K3s or push to registry |
| `make install` | no | Helm install the platform chart; refuses existing platform state |
| `make upgrade` | no | In-place Helm upgrade for an existing platform |
| `make reinstall` | no | Destructive platform reinstall through official teardown |
| `make session` | no | Deploy a constellation session |
| `make status` | no | Show cluster status |
| `make teardown` | no | Full teardown |
| `make nuke` | no | Square-one reset; K3s remains |

### Valid Lifecycle Sequences

| Current state | Command |
|---------------|---------|
| Clean checkout/K3s state | `make all` |
| Prove full square-one recovery | `make nuke && make all` |
| Existing platform, update images/chart | `make build && make load && make upgrade` |
| Existing platform, destructive refresh | `make build && make load && make reinstall && make session` |
| Existing platform, switch session | `make session DEFAULT_SESSION=catalog/nodalarc/sessions/<name>.yaml` |

### Build Targets

| Target | What it builds |
|--------|---------------|
| `make build-frontends` | VF React app |
| `make build-images` | All service Docker images |
| `make build-base-images` | Session images (base, FRR, probe, HDTN, ION, picoquic, the uD3TN tools) |
| `make build-ome` | OME image |
| `make build-scheduler` | Scheduler image |
| `make build-node-agent` | Node Agent image |
| `make build-vs-api` | VS-API image |
| `make build-operator` | Operator image |
| `make build-vf` | VF (frontend + nginx) image |

### Cleanup Targets

| Target | What it removes |
|--------|----------------|
| `make clean` | Build artifacts (dist/, caches) |
| `make clean-images` | All nodalarc Docker images |
| `make clean-deps` | Python .venv and node_modules |
| `make clean-registry` | Images from registry |
| `make purge-containerd` | NodalArc images from K3s containerd cache |

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
configs/                Platform config and ephemerides
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
| `adapters/frr/` | The FRR workload adapter: support declaration, daemon selection, templates |
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
