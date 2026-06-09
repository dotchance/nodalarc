# Getting Started - Deployment

## Prerequisites

### Hardware

| Configuration | RAM | CPU | Disk | Satellites |
|--------------|-----|-----|------|-----------|
| Minimum (demo) | 8 GB | 4 cores | 40 GB | 36 |
| Recommended | 32 GB | 8 cores | 80 GB | 200+ |
| Multi-node | 32 GB per node | 8 cores per node | 80 GB per node | 1000+ |

Each satellite pod uses approximately 18 MB RAM and 2-5 millicores CPU at steady state. Platform overhead (services + NATS) adds about 2 GB.

### Software

- Linux: Ubuntu 22.04+ or Debian 12+
- Root access (sudo)
- A Kubernetes cluster (any conformant distribution: K3s, K8s, EKS, GKE, etc.)

If you don't already have Kubernetes, the bootstrap script installs K3s.

### Network

- Port 3000: Visualization frontend (VF)
- Port 8080: VS-API (REST/WebSocket)
- Ports 22 (per pod IP): SSH terminal access (internal cluster network)
- Inter-node: UDP 4789 (VXLAN) for multi-node deployments

## Step 1: Bootstrap (Fresh Machine)

If you already have Kubernetes, skip to Step 2.

```bash
sudo scripts/bootstrap-host.sh
```

This installs:

| Component | Purpose |
|-----------|---------|
| Docker | Builds container images |
| K3s | Lightweight Kubernetes (skipped if K8s/kubectl already present) |
| kubectl + Helm | Cluster management and chart deployment |
| Node.js 22 | Builds the visualization frontend |
| uv | Python package manager |
| Kernel modules | `mpls_router`, `mpls_iptunnel` for MPLS forwarding |
| Sysctls | IPv4/IPv6 forwarding, MPLS platform labels |

The script is idempotent - safe to run multiple times. It does NOT modify an existing Kubernetes installation.

### What the Sysctls Do

Written to `/etc/sysctl.d/99-nodalarc.conf`:

```
net.ipv4.ip_forward = 1
net.ipv6.conf.all.forwarding = 1
net.mpls.platform_labels = 1048575
net.mpls.conf.lo.input = 1
```

These enable packet forwarding and MPLS label processing in the kernel. Required for the emulated network to forward traffic between pods.

## Step 2: Build and Deploy

```bash
make all
```

Use `make all` from a clean NodalArc state: no existing `nodalarc` namespace or Helm release. To prove a full from-scratch lifecycle on a machine that already has K3s installed, run:

```bash
make nuke && make all
```

`make all` executes the full bring-up pipeline:

1. Install Python and Node.js dependencies
2. Build the visualization frontend
3. Build all Docker images (6 services + base images)
4. Load images into the cluster (K3s import or registry push)
5. Install the Helm chart
6. Deploy the default session (`earth-leo-simple.yaml`: 36 LEO satellites,
   OSPF, and MBB-capable Earth ground nodes)
7. Print status

Total time: 3-5 minutes from a fresh checkout on a single machine.

If the platform is already installed, `make all` is the wrong transition because `make install` refuses existing platform state. Use `make build && make load && make upgrade` for an in-place platform update, or `make build && make load && make reinstall && make session` for a destructive platform refresh through the official teardown path.

### What "make all" Creates

- Kubernetes namespace: `nodalarc`
- ConstellationSpec CRD (cluster-scoped)
- Platform services: OME, Scheduler, Node Agent (DaemonSet), VS-API, Operator, NATS
- Frontend: VF (nginx serving the React app)
- Session: 36 satellite pods + 7 Earth ground-node pods
- ConfigMaps: FRR configs, topology wiring manifest, platform config
- Secrets: SSH terminal keys
- PVC: NATS JetStream file storage

## Step 3: Verify

```bash
make status
```

This shows:
- Pod counts and states
- Session phase (Creating → Wiring → Ready)
- Active link count

When the session phase shows "Ready" and links are active, the system is fully operational.

Open http://localhost:3000 to see the visualization. If deploying on a remote machine, forward or expose port 3000.

### Verifying Routing

```bash
# Quick check: do satellites have routing neighbors?
NODE=$(sudo KUBECONFIG=/etc/rancher/k3s/k3s.yaml kubectl get pods -n nodalarc \
  -o name | sed 's#pod/##' | grep '^space-sat-' | head -1)
sudo KUBECONFIG=/etc/rancher/k3s/k3s.yaml kubectl exec "$NODE" -n nodalarc -c frr -- \
  vtysh -c "show ip ospf neighbor"
```

You should see adjacent satellites listed as FULL neighbors. If running IS-IS instead of OSPF, use `show isis neighbor`.

## Step 4: Deploy a Different Session

```bash
# Switch to a 176-satellite IS-IS constellation
make session DEFAULT_SESSION=catalog/nodalarc/sessions/earth-leo-walker.yaml
```

Or users can deploy sessions from the browser wizard at http://localhost:3000.

Available session configs:

| Session | Space Nodes | Description |
|---------|------------:|-------------|
| `earth-leo-simple.yaml` | 36 | Default. MBB-capable single-shell LEO starter |
| `earth-leo-walker.yaml` | 176 | Walker-delta LEO starter |
| `earth-leo-polar.yaml` | 36 | Polar LEO starter with high-latitude gateway sites |
| `earth-meo-gps.yaml` | 24 | GPS-altitude MEO starter with long-range RF gateways |
| `earth-geo-inmarsat.yaml` | 4 | Representative GEO commercial-relay-style starter |
| `earth-geo-tdrs.yaml` | 6 | Representative GEO relay/TDRS-style starter |
| `earth-leo-heo-geo-luna-reachability.yaml` | — | Multi-regime LEO, HEO, GEO, lunar relay, and lunar ground reachability |

These examples run IS-IS (the default). OSPF is also supported, selected per
routing domain — see [Configuration](configuration.md).

## Step 5: Teardown

```bash
make teardown
```

Cleanly stops the session, removes all pods, and cleans kernel state. Wait for "Teardown complete" before deploying again.

Next valid transition:

```bash
make install && make session
```

To remove everything (images, dependencies, K3s image cache):

```bash
make nuke
```

Next valid transition:

```bash
make all
```

## Makefile Target Reference

### Primary Targets

| Target | Requires sudo | Description |
|--------|:---:|-------------|
| `make all` | no | Clean-state pipeline: deps → build → load → install → session → status |
| `make deps` | no | Install Python/Node.js dependencies |
| `make build` | no | Build frontend and all Docker images |
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
| `make build-base-images` | Infrastructure images (FRR, probe, fwd) |
| `make build-ome` | OME image |
| `make build-scheduler` | Scheduler image |
| `make build-node-agent` | Node Agent image |
| `make build-vs-api` | VS-API image |
| `make build-operator` | Operator image |
| `make build-vf` | VF (frontend + nginx) image |

### Service Deploy Targets

These build, load, and restart a single service without tearing down the session:

| Target | What it restarts |
|--------|-----------------|
| `make deploy-ome` | OME |
| `make deploy-scheduler` | Scheduler |
| `make deploy-node-agent` | Node Agent |
| `make deploy-vs-api` | VS-API |
| `make deploy-operator` | Operator |
| `make deploy-vf` | VF frontend |
| `make deploy-all` | All core services |

### Cleanup Targets

| Target | What it removes |
|--------|----------------|
| `make clean` | Build artifacts (dist/, caches) |
| `make clean-images` | All nodalarc Docker images |
| `make clean-deps` | Python .venv and node_modules |
| `make clean-registry` | Images from registry |
| `make purge-containerd` | NodalArc images from K3s containerd cache |

## Next Steps

- [Configuration](configuration.md) - understand session YAML and building blocks
- [Multi-Node Deployment](multi-node.md) - for clusters with multiple compute nodes
- [Scaling](scaling.md) - capacity planning and performance characteristics
- [Operations](operations.md) - day-to-day management and upgrades
