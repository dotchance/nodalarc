# Scaling and Performance

NodalArc runs real routing stacks on real network interfaces. Scaling depends on the same factors that matter in real networks: memory per node, CPU for route computation, and link count.

## Measured Performance

**176 satellites + 7 ground stations on a 4-node cluster:**

| Metric | Value |
|--------|-------|
| Total pods | 192 (176 sats + 7 GS + 9 platform) |
| Active ISL links | 352 |
| Active ground links | 5-7 (varies with orbital geometry) |
| CPU utilization per node | 2-5% at steady state |
| Memory per satellite pod | ~18 MB |
| Platform services overhead | ~2 GB total |
| OME window computation | 46 seconds for one 95-minute orbital period |
| Session deployment time | ~3 minutes (CRD apply to all pods wired) |
| Initial VXLAN batch (cold start) | ~30 seconds for 176 tunnels |
| Visualization draw calls | 47/frame (O(1) rendering architecture) |

## Per-Satellite Footprint

| Resource | Per Satellite Pod |
|----------|------------------|
| Memory | ~18 MB (FRR with IS-IS/OSPF + zebra) |
| CPU (steady state) | 2-5 millicores |
| CPU (SPF reconvergence) | Brief spike, <1 second |

## Capacity Planning

### Memory Formula

```
max_satellites_per_node = (available_RAM - 2 GB) / 18 MB
```

| Node RAM | Max Satellites (approx) |
|----------|------------------------|
| 8 GB | 330 |
| 16 GB | 780 |
| 32 GB | 1,660 |
| 64 GB | 3,440 |

Platform overhead (OME, Scheduler, NATS, VS-API, Operator, Node Agent) uses approximately 2 GB regardless of constellation size. This overhead runs on one node; other nodes dedicate nearly all memory to satellite pods.

### Multi-Node Linear Scaling

With `planePerNode` placement:
- 4 nodes × 32 GB = up to ~6,000 satellites theoretically
- Practical limit depends on OME computation time and Scheduler dispatch rate

### OME Computation Scaling

The OME computes visibility between all satellite pairs. Computation time scales with pair count:

| Satellites | Pairs to Check | Computation Time |
|-----------|---------------|-----------------|
| 36 | 630 | < 1 second |
| 176 | 15,400 | ~46 seconds |
| 576 | 165,600 | ~5 minutes (estimated) |
| 1,584 | 1,254,336 | Would benefit from spatial indexing |

For constellations above 500 satellites, spatial indexing (octree/KD-tree) would significantly reduce computation time. This is an optimization opportunity, not a hard limit - the OME precomputes the full orbital window before pacing, so computation time adds to startup latency but doesn't affect runtime performance.

## What Scales Well

**FRR routing daemons.** IS-IS with per-plane areas keeps the LSDB per node bounded regardless of total constellation size. Each node only holds LSPs from its own area plus inter-area summaries. SPF recomputation completes in under 1 second even at 176 nodes.

**NATS JetStream.** Handles thousands of messages per second with microsecond latency. The OME publishes ~12,000 events per orbital window, and the Scheduler consumes them in real time without backpressure.

**Linux kernel networking.** Thousands of veth pairs and tc netem qdiscs run without measurable overhead. The Node Agent wires 183 pods (all interfaces, sysctls, MPLS config) in under 60 seconds.

**Multi-node pod distribution.** Adding nodes scales linearly. Each node handles its own satellite pods, VXLAN tunnels to peers, and local kernel state. No shared state beyond NATS messages.

**Frontend rendering.** O(1) draw call architecture - all links, trails, boundaries, and orbit paths are batched into shared geometries. Performance is constant regardless of constellation size (47 draw calls for 10 nodes or 10,000).

## What to Watch

**OME startup time.** Scales quadratically with satellite count. For very large constellations, the initial window computation takes minutes. Once computed, runtime performance is unaffected.

**Routing protocol choice.** IS-IS handles large flat topologies better than OSPF due to simpler flooding mechanics. For 100+ satellites, IS-IS with per-plane areas is strongly recommended. OSPF with multi-area is also viable but IS-IS is the industry standard for carrier-scale networks.

**VXLAN tunnel count.** Each cross-node ISL creates one tunnel. With planePerNode on 4 nodes and 16 planes, you get ~176 tunnels. Linux handles this easily, but initial creation takes ~30 seconds. For very large constellations on many nodes, this is a one-time startup cost.

## How to Measure

```bash
# Node-level resource usage
sudo KUBECONFIG=/etc/rancher/k3s/k3s.yaml kubectl top nodes

# Per-pod resource usage (sorted by memory)
sudo KUBECONFIG=/etc/rancher/k3s/k3s.yaml kubectl top pods -n nodalarc --sort-by=memory

# OME computation timing
sudo KUBECONFIG=/etc/rancher/k3s/k3s.yaml kubectl logs -n nodalarc -l app=nodalarc-ome | grep -E 'computing|pacing|window'

# Active link count via API
TOKEN=$(curl -s http://localhost:8080/api/v1/auth/token | python3 -c "import json,sys; print(json.load(sys.stdin)['token'])")
curl -s -H "Authorization: Bearer $TOKEN" http://localhost:8080/api/v1/state | \
  python3 -c "import json,sys; d=json.load(sys.stdin); print(f'{len(d[\"links\"])} active links')"

# VXLAN tunnel count per node
ssh node02 "ip link show | grep -cE 'vx[0-9]{5}'"
```
