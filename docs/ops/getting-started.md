# Getting Started — Deployment

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

The script is idempotent — safe to run multiple times. It does NOT modify an existing Kubernetes installation.

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

This executes the full pipeline:

1. Install Python and Node.js dependencies
2. Build the visualization frontend
3. Build all Docker images (6 services + base images)
4. Load images into the cluster (K3s import or registry push)
5. Install the Helm chart
6. Deploy a default session (36 satellites, OSPF, 6 ground stations)

Total time: 3-5 minutes from a fresh checkout on a single machine.

### What "make all" Creates

- Kubernetes namespace: `nodalarc`
- ConstellationSpec CRD (cluster-scoped)
- Platform services: OME, Scheduler, Node Agent (DaemonSet), VS-API, Operator, NATS
- Frontend: VF (nginx serving the React app)
- Session: 36 satellite pods + 6 ground station pods
- ConfigMaps: FRR configs, topology wiring manifest, platform config
- Secrets: SSH terminal keys
- PVC: NATS JetStream file storage

## Step 3: Verify

```bash
sudo make status
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
sudo KUBECONFIG=/etc/rancher/k3s/k3s.yaml kubectl exec sat-P00S00 -n nodalarc -c frr -- vtysh -c "show ip ospf neighbor"
```

You should see adjacent satellites listed as FULL neighbors. If running IS-IS instead of OSPF, use `show isis neighbor`.

## Step 4: Deploy a Different Session

```bash
# Switch to a 176-satellite IS-IS constellation
sudo make session DEFAULT_SESSION=configs/sessions/starlink-176-isis-te.yaml
```

Or users can deploy sessions from the browser wizard at http://localhost:3000.

Available session configs:

| Session | Satellites | Routing | Description |
|---------|-----------|---------|-------------|
| `demo-36-ospf.yaml` | 36 | OSPF | Default. Single ring, fast deploy |
| `starlink-176-isis-te.yaml` | 176 | IS-IS + TE | Full Walker constellation |
| `starlink-176-nodalpath.yaml` | 176 | NodalPath | Centralized path computation |
| `starlink-576-isis-te.yaml` | 576 | IS-IS + TE | Large-scale testing |
| `iridium-66.yaml` (constellation) | 66 | varies | Polar orbit topology |

## Step 5: Teardown

```bash
sudo make teardown
```

Cleanly stops the session, removes all pods, and cleans kernel state. Wait for "Teardown complete" before deploying again.

To remove everything (images, dependencies, K3s image cache):

```bash
sudo make nuke
```

## Makefile Target Reference

### Primary Targets

| Target | Requires sudo | Description |
|--------|:---:|-------------|
| `make all` | no | Full pipeline: deps → build → load → install → session |
| `make deps` | no | Install Python/Node.js dependencies |
| `make build` | no | Build frontend and all Docker images |
| `make load` | no | Import images to K3s or push to registry |
| `make install` | sudo | Helm install/upgrade the platform chart |
| `make session` | sudo | Deploy a constellation session |
| `make status` | sudo | Show cluster status |
| `make teardown` | sudo | Full teardown |
| `make nuke` | sudo | Remove everything |

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
| `make clean-registry` | Images from registry + K3s containerd cache |

## Next Steps

- [Configuration](configuration.md) — understand session YAML and building blocks
- [Multi-Node Deployment](multi-node.md) — for clusters with multiple compute nodes
- [Scaling](scaling.md) — capacity planning and performance characteristics
- [Operations](operations.md) — day-to-day management and upgrades
