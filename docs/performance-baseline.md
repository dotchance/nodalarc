# Performance and Scaling

NodalArc runs real routing stacks on real network interfaces, so scaling depends on the same things that matter in real networks: memory per node, CPU for route computation, and the number of links the control plane manages.

## What We've Tested

**176 satellites + 7 ground stations across 2 physical nodes:**

| Metric | Value |
|--------|-------|
| Total pods | 192 (176 satellites + 7 ground stations + 9 platform) |
| Active ISL links | 352 |
| Active GS links | 5-7 (varies with orbital geometry) |
| Nodes | 2 (nodal: 95 pods, nodal03: 88 pods) |
| CPU utilization | 5% nodal, 2% nodal03 |
| Memory utilization | 24% nodal, 8% nodal03 |
| OME window computation | 46 seconds for one 95-minute orbital period |
| OME event pacing | 12,661 events paced at 1:1 real-time |
| Deploy time (make all) | ~3 minutes from clean checkout |
| Session ready | ~3 minutes from CRD apply to all pods wired |

**Per-satellite footprint:**

| Resource | Per satellite pod |
|----------|------------------|
| CPU | ~2-5 millicores at steady state |
| Memory | ~18 Mi (FRR with IS-IS + zebra) |

At 18 Mi per satellite, a 32 GB machine can run 1000+ satellite pods on memory alone. Platform overhead (OME, Scheduler, NATS, etc.) adds about 2 GB. CPU at steady state is negligible. The practical limit on a single node is memory, and the math is straightforward: `(available_ram - 2 GB platform overhead) / 18 Mi per sat`.

## Scaling

| Configuration | Satellites | What we know |
|---------------|-----------|-------------|
| Single node, 32 GB | 200+ | Tested. 24% memory utilization at 176 sats. Plenty of headroom. |
| 2 nodes, 32 GB each | 176 across both | Tested. Cross-plane ISLs over VXLAN, IS-IS adjacencies forming across nodes. |
| Single node, 32 GB | 1000+ | The per-pod math supports it. ~18 GB for sats + 2 GB platform = 20 GB, well within 32 GB. |
| Multi-node cluster | 2000+ | Add nodes. planePerNode distributes pods. Each node handles its share of the memory and VXLAN tunnels. |

## What Scales Well

**FRR routing daemons.** FRR is designed for large networks. IS-IS with multi-area design (per-plane areas) keeps the LSDB per node small regardless of total constellation size. SPF recomputation completes in under 1 second even at 176 nodes.

**NATS JetStream messaging.** The OME publishes events, the Scheduler consumes them, and the Node Agent executes kernel operations. NATS handles thousands of messages per second with microsecond latency. The messaging layer is not a bottleneck at any scale we've tested.

**Multi-node pod distribution.** The planePerNode placement policy puts each orbital plane on a separate physical node. Intra-plane ISLs are fast local veth pairs. Only cross-plane ISLs need VXLAN tunnels. Adding nodes scales linearly.

**Kernel networking.** Linux handles thousands of veth pairs and tc netem qdiscs without issue. The Node Agent wires 183 pods (with ISL interfaces, ground interfaces, sysctls, MPLS config) in under 60 seconds.

## Future Optimization Areas

**OME window computation.** At 176 satellites the OME computes a full orbital window in 46 seconds. This scales with the number of satellite pairs that need visibility checks. For constellations above 500 satellites, spatial indexing (octree or KD-tree) would reduce computation time significantly. This is an optimization opportunity, not a current limitation.

**Initial VXLAN batch on cold start.** Creating 176 VXLAN tunnels on a fresh node takes about 30 seconds. This is a one-time cost at session start, not a runtime bottleneck. For very large constellations on many nodes, parallelizing VXLAN creation across the thread pool would help.

**Routing protocol choice.** IS-IS handles large flat topologies better than OSPF due to simpler flooding mechanics. For constellations above 200 satellites, IS-IS with per-plane areas is recommended. The platform supports both, so you can compare.

## How to Measure

With a running session:

```bash
# Node-level resources
kubectl top nodes
```

```bash
# Per-pod resources
kubectl top pods -n nodalarc --sort-by=memory
```

```bash
# OME computation timing
kubectl logs -n nodalarc -l app=nodalarc-ome | grep 'computing\|pacing'
```

```bash
# Active link count
TOKEN=$(curl -s http://localhost:8080/api/v1/auth/token | python3 -c "import json,sys; print(json.load(sys.stdin)['token'])")
curl -s -H "Authorization: Bearer $TOKEN" http://localhost:8080/api/v1/state | python3 -c "import json,sys; d=json.load(sys.stdin); print(f'{len(d[\"links\"])} active links')"
```
