# Development Workflow

## Build and Deploy Cycle

### Rebuild a Single Service

```bash
make deploy-scheduler
```

This builds the Docker image, loads it into the cluster, and does a rolling restart. The running session stays up. Takes 15-30 seconds.

### Rebuild All Services

```bash
make deploy-all
```

Rebuilds and restarts all core services (OME, Scheduler, Node Agent, VS-API, Operator, VF).

### Frontend Hot Reload

For VF development, use the Vite dev server:

```bash
cd frontend
npm run dev
```

Hot module replacement on port 5173. No Docker rebuild needed during iteration. Deploy the full image when ready for integration testing:

```bash
make deploy-vf
```

## Docker Build Cache

Docker BuildKit caches COPY layers aggressively using content-addressable hashing. This means edits to source files sometimes don't make it into the built image - BuildKit serves the cached layer because it believes the content hasn't changed.

**For backend services:** This rarely causes problems. The cache is reliable.

**For the VF (frontend):** The frontend build context is separate from the main repo context. BuildKit can serve stale layers.

If you suspect the deployed image doesn't contain your latest changes:

```bash
# Nuclear option: clear ALL BuildKit cache
docker builder prune -f

# Then rebuild
make deploy-vf
```

**Verify your code is deployed:**
```bash
# Check the running image contains your change
sudo KUBECONFIG=/etc/rancher/k3s/k3s.yaml kubectl exec deploy/nodalarc-vf -- \
  grep "something_unique_from_your_change" /usr/share/nginx/html/assets/index-*.js
```

## Make Targets

### Primary Workflow

| Target | What It Does |
|--------|--------------|
| `make all` | Clean-state pipeline: deps → build → load → install → session → status |
| `make build` | Build frontend + all Docker images |
| `make load` | Import images into K3s or push them to the registry |
| `make install` | Install the platform; refuses existing platform state |
| `make upgrade` | In-place Helm upgrade for an existing platform |
| `make reinstall` | Destructive platform reinstall through official teardown |
| `make test` | Run backend and frontend unit tests |
| `make deploy-<service>` | Build, load, and restart one service |
| `make deploy-all` | Build, load, and restart all services |
| `make teardown` | Full platform/session teardown and host cleanup |
| `make session` | Deploy a session |
| `make status` | Show pod states and session phase |

Valid state transitions:

```bash
# Clean K3s or freshly nuked state
make all

# Prove square-one recovery
make nuke && make all

# Existing platform, update service images/chart values
make build && make load && make upgrade

# Existing platform, destructive refresh
make build && make load && make reinstall && make session
```

### Build Targets

| Target | Builds |
|--------|--------|
| `make build-ome` | OME image |
| `make build-scheduler` | Scheduler image |
| `make build-node-agent` | Node Agent image |
| `make build-vs-api` | VS-API image |
| `make build-operator` | Operator image |
| `make build-vf` | Frontend + nginx image |
| `make build-base-images` | FRR, probe, fwd base images |
| `make build-frontends` | VF React app (no Docker) |

### Cleanup

| Target | Removes |
|--------|---------|
| `make clean` | Frontend dist, Python caches |
| `make clean-images` | All local NodalArc Docker images |
| `make clean-deps` | Python .venv, node_modules |
| `make nuke` | Square-one reset: K3s remains, NodalArc state/deps/images/artifacts are removed |

## Git Workflow

### Branch Naming

```
feature/description     # new functionality
fix/description         # bug fix
refactor/description    # structural change
```

### Commit Messages

Write what changed and why. No conventional commit prefixes (`feat:`, `fix:`, `chore:`). No boilerplate attribution, generated footer lines, tool signatures, or transcript fragments.

Good:
```
Session switch uses LAST_PER_SUBJECT for retained NATS messages

DeliverPolicy.NEW skips messages retained in JetStream. The ephemeris
is published once and retained - NEW never sees it. LAST_PER_SUBJECT
returns the latest retained message, which is what switch mode needs.
```

Bad:
```
fix: update delivery policy
```

### Before Committing

1. Run unit tests: `make test`
2. Run frontend tests (if frontend changed): `cd frontend && npm test`
3. Verify the change works in the running system
4. Commit specific files (not `git add .`)

## Kubectl

All kubectl commands require the K3s kubeconfig:

```bash
sudo KUBECONFIG=/etc/rancher/k3s/k3s.yaml kubectl <command>
```

This is verbose but explicit. Never modify the default kubeconfig or set it globally - it's a K3s cluster detail that shouldn't leak into your shell.

## Debugging a Running Service

### Logs

```bash
# Tail OME logs
sudo KUBECONFIG=/etc/rancher/k3s/k3s.yaml kubectl logs -l app=nodalarc-ome -n nodalarc -f

# Tail Scheduler logs
sudo KUBECONFIG=/etc/rancher/k3s/k3s.yaml kubectl logs -l app=nodalarc-scheduler -n nodalarc -f

# Tail Node Agent (on a specific node)
sudo KUBECONFIG=/etc/rancher/k3s/k3s.yaml kubectl logs -l app=nodalarc-node-agent -n nodalarc -f
```

### Exec Into a Service Pod

```bash
sudo KUBECONFIG=/etc/rancher/k3s/k3s.yaml kubectl exec -it deploy/nodalarc-scheduler -n nodalarc -- bash
```

### Inspect Session Pods

```bash
# Check a satellite's interfaces
sudo KUBECONFIG=/etc/rancher/k3s/k3s.yaml kubectl exec sat-P00S00 -n nodalarc -c frr -- ip -br link show

# Check routing state
sudo KUBECONFIG=/etc/rancher/k3s/k3s.yaml kubectl exec sat-P00S00 -n nodalarc -c frr -- vtysh -c "show isis neighbor"

# Check latency shaping
sudo KUBECONFIG=/etc/rancher/k3s/k3s.yaml kubectl exec sat-P00S00 -n nodalarc -c frr -- tc qdisc show dev isl0
```

## Multi-Node Development

If developing with multiple K3s nodes, configure a container registry in `config.mk`. See the [Multi-Node Deployment](../ops/multi-node.md) guide for registry setup.

With `REGISTRY_HOST` set, all `deploy-*` targets push to the registry and images are available on all nodes. Runtime image values are generated from the shared image inventory.
