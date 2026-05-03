# Multi-Node Deployment

For constellations larger than ~200 satellites, or when you need to test multi-node networking behavior (VXLAN tunnels, substrate latency compensation), deploy across multiple Kubernetes nodes.

## Requirements

- 2+ Kubernetes nodes with the `nodalarc.io/node-agent=true` label
- A container registry accessible from all nodes
- UDP 4789 (VXLAN) open between nodes
- Recommended: low-latency network between nodes (GbE or better)

## Container Registry Setup

Single-node deployments import images directly into the local container runtime. Multi-node deployments need a registry so all nodes can pull images.

### 1. Create config.mk

```bash
cp config.mk.example config.mk
```

### 2. Configure the registry

Edit `config.mk`:

```makefile
# Your container registry (include trailing slash)
REGISTRY_PREFIX ?= myregistry.local:5000/nodalarc/

# Tell Helm where to find images
HELM_EXTRA_ARGS ?= --set imagePullPolicy=IfNotPresent \
    --set images.frr=$(REGISTRY_PREFIX)frr:latest \
    --set images.probe=$(REGISTRY_PREFIX)probe:latest \
    --set images.fwd=$(REGISTRY_PREFIX)fwd:latest \
    --set images.ome=$(REGISTRY_PREFIX)ome:latest \
    --set images.scheduler=$(REGISTRY_PREFIX)scheduler:latest \
    --set images.nodeAgent=$(REGISTRY_PREFIX)node-agent:latest \
    --set images.vsApi=$(REGISTRY_PREFIX)vs-api:latest \
    --set images.operator=$(REGISTRY_PREFIX)operator:latest \
    --set images.vf=$(REGISTRY_PREFIX)vf:latest
```

With `REGISTRY_PREFIX` set, `make load` pushes to the registry instead of importing locally. All nodes pull from the registry automatically.

### 3. Build and deploy

```bash
make all
```

Works the same way — images are now pushed to the registry and Helm references them there.

## Node Labeling

The Node Agent runs on every node with the label `nodalarc.io/node-agent=true`. Label your compute nodes:

```bash
kubectl label node node02 nodalarc.io/node-agent=true
kubectl label node node03 nodalarc.io/node-agent=true
kubectl label node node04 nodalarc.io/node-agent=true
```

Don't label your control-plane-only nodes unless you want session pods running there.

## Placement Policies

The placement policy controls how satellite pods are distributed across nodes. Set it in the session YAML:

```yaml
placement:
  policy: planePerNode
```

### allOnOne (default)

All session pods on a single node. No cross-node traffic. This is the default and works identically to a single-node deployment — use it when you have multiple nodes but don't need cross-node testing.

### planePerNode

Each orbital plane assigned to a separate K8s node. This is the recommended policy for multi-node deployments:

- **Intra-plane ISLs** (isl0, isl1) — pod-to-pod on the same node. Fast, no encapsulation.
- **Cross-plane ISLs** (isl2, isl3) — traverse VXLAN tunnels between nodes. Adds real network traversal.

This models realistic satellite constellation networking where intra-plane communication is "free" (same orbital shell) but cross-plane communication traverses real infrastructure.

### planeGroupPerNode

Groups of adjacent orbital planes share a node. Reduces the number of VXLAN tunnels while still distributing pods across nodes. Use when you have fewer nodes than orbital planes.

## How Cross-Node Links Work

When two pods are on different K8s nodes, the Node Agent creates a VXLAN tunnel between them:

```
Node A                                    Node B
┌─────────────────┐                      ┌─────────────────┐
│ sat-P00S03 pod  │                      │ sat-P01S03 pod  │
│   isl2 ←────── veth ── vxlan ──────── veth ──────→ isl3 │
└─────────────────┘      UDP 4789        └─────────────────┘
```

The VXLAN tunnel encapsulates Ethernet frames in UDP, carrying them across the physical network between nodes. From the perspective of the FRR routing daemon inside each pod, `isl2`/`isl3` look like normal network interfaces — FRR doesn't know or care that the physical path goes through a VXLAN tunnel.

### Substrate Latency Compensation

The physical network between nodes adds real latency to VXLAN-encapsulated packets. NodalArc compensates for this:

1. The Node Agent measures the physical round-trip time between each pair of nodes continuously (ICMP probes every 60 seconds)
2. The Scheduler subtracts the measured physical latency from the desired orbital latency when setting tc netem delay values
3. Result: `total_packet_delay = netem_delay + physical_network_delay = orbital_latency`

The emulated latency is always accurate regardless of the physical network topology between your nodes. If nodes are 1ms apart, netem subtracts 1ms. If they're 10ms apart, netem subtracts 10ms. The user sees the orbital latency.

## Capacity Planning

Per-node capacity is primarily memory-limited:

```
Available satellites per node ≈ (node_RAM - 2 GB platform) / 18 MB per satellite
```

| Node RAM | Approximate satellite capacity |
|----------|-------------------------------|
| 8 GB | ~330 satellites |
| 16 GB | ~780 satellites |
| 32 GB | ~1,660 satellites |
| 64 GB | ~3,440 satellites |

Platform services (OME, Scheduler, NATS, etc.) only run once regardless of cluster size. They consume approximately 2 GB total.

## VXLAN Tunnel Limits

Each cross-plane ISL between pods on different nodes creates one VXLAN tunnel. For a 176-satellite constellation with planePerNode on 4 nodes, approximately 176 VXLAN tunnels are created across the cluster. Linux handles thousands of VXLAN interfaces without issue.

Initial tunnel creation on cold start takes about 30 seconds for 176 tunnels. This is a one-time cost at session deployment, not a runtime bottleneck.

## Monitoring Cross-Node Health

```bash
# Count VXLAN tunnels on a specific node
ssh node02 "ip link show | grep -cE 'vx[0-9]{5}'"

# Check substrate latency measurements
sudo KUBECONFIG=/etc/rancher/k3s/k3s.yaml kubectl logs -l app=nodalarc-node-agent -n nodalarc | grep substrate

# Verify cross-plane adjacencies form
sudo KUBECONFIG=/etc/rancher/k3s/k3s.yaml kubectl exec sat-P00S05 -n nodalarc -c frr -- vtysh -c "show isis neighbor" | grep isl2
```

## Network Requirements

| Traffic | Protocol | Port | Between |
|---------|----------|------|---------|
| VXLAN tunnels | UDP | 4789 | All compute nodes |
| NATS | TCP | 4222 | All pods → NATS service |
| K8s API | TCP | 6443 | All nodes → control plane |
| Registry | TCP | 5000 (typical) | All nodes → registry |

Ensure your network firewall allows UDP 4789 between all nodes that will run session pods.
