# Developer Guide

This guide is for developers modifying NodalArc source code. If you're using NodalArc as-is, see the [Getting Started](getting-started.md) guide instead.

## Development Workflow

After making code changes, rebuild and restart the affected service without tearing down the running session:

```bash
# Rebuild and restart a single service
sudo make deploy-scheduler
sudo make deploy-ome
sudo make deploy-node-agent
sudo make deploy-vs-api
sudo make deploy-operator
sudo make deploy-vf

# Rebuild and restart all core services at once
sudo make deploy-all
```

These targets build the Docker image, push/load it, and do a rolling restart. The running session stays up. No teardown needed.

## Running Tests

```bash
# Unit tests (867+, no sudo needed)
make test

# Integration tests (requires a running session)
sudo make test-integration
```

Unit tests must pass before any commit touching backend code.

## Build Targets

```bash
# Install dependencies only
make deps

# Build all images (installs deps automatically)
make build

# Build just the base/infrastructure images (FRR, probe)
make build-base-images

# Build a single service image
make build-ome
make build-scheduler
make build-node-agent
make build-vs-api
make build-operator
make build-vf
make build-nodalpath
```

## Cleanup Targets

```bash
# Remove build artifacts (frontend dist, caches)
make clean

# Remove all nodalarc Docker images and build cache
make clean-images

# Remove Python .venv and node_modules
make clean-deps

# Everything - teardown + clean + clean-images + clean-deps
sudo make nuke
```

## Project Structure

```
lib/nodalarc/           Shared library - models, proto, geo, channels
services/ome/           Orbital Mechanics Engine
services/scheduler/     Topology dispatcher (reconcile-based)
services/node_agent/    DaemonSet - kernel netlink operations
services/vs_api/        Visualization State API (FastAPI)
services/nodalarc_operator/  K8s Operator (kopf)
services/measurement/   Measurement infrastructure (probes, adapters)
frontend/               Visualization Frontend (React + Three.js)
nodalpath/              NodalPath extension (self-contained)
images/                 Base container images (FRR, probe)
deploy/helm/            Helm chart templates and values
configs/                Runtime configs (constellations, sessions, templates)
tools/                  CLI tools (teardown, scenario inject)
tests/unit/             Unit tests
tests/integration/      Integration tests
```

## Key Files

| File | What it does |
|------|-------------|
| `services/scheduler/dispatcher.py` | The live topology dispatcher. `_reconcile_links` is the single path to the Node Agent |
| `services/node_agent/handlers.py` | BatchLinkUp/Down RPC handlers: veth, VXLAN, bridge, tc operations |
| `services/node_agent/vxlan.py` | VXLAN tunnel creation/destruction for cross-node links |
| `services/node_agent/namespace_ops.py` | setns-based namespace entry. All kernel ops go through here |
| `services/nodalarc_operator/session_deployer.py` | Session creation: pod placement, FRR config delivery, wiring manifest |
| `services/nodalarc_operator/handlers.py` | Kopf handlers for CRD create/delete/resume lifecycle |
| `lib/nodalarc/nats_channels.py` | All NATS subject definitions (single source of truth) |
| `lib/nodalarc/proto/node_agent.proto` | Protobuf definitions for Scheduler ↔ Node Agent |
| `configs/templates/frr/*.j2` | Jinja2 templates for FRR configuration generation |
| `tools/na-teardown.sh` | The only permitted teardown mechanism |

## Multi-Node Development

If developing with multiple K3s nodes, set up a local container registry and configure `config.mk`. See [Getting Started - Multi-Node Setup](getting-started.md#multi-node-setup) for registry configuration.

The `REGISTRY_PREFIX` variable controls whether `make load` imports locally (single-node) or pushes to a registry (multi-node). The `deploy-*` targets respect this setting automatically.

## Code Conventions

- Python 3.14+ for all backend code
- Pydantic v2 for all structured data across component boundaries
- pyroute2 for all kernel netlink operations. Never shell out to `ip`, `tc`, or `bridge`
- All NATS subjects defined in `lib/nodalarc/nats_channels.py`. No string literals
- `model_config = ConfigDict(frozen=True)` for all event models
- f-strings for formatting (except logging lazy format)
