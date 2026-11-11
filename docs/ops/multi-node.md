# Multi-Node Deployment

Deploy across several Kubernetes nodes to run larger sessions and to exercise the links that cross between servers (VXLAN tunnels, substrate latency compensation).

## Requirements

- 2+ Kubernetes nodes that pass node qualification (the Node Agent's readiness probe reports what a node lacks)
- For a NodalArc release: every node reaches ghcr.io, where the release's images are published
- For images built from source: a container registry every node and the build machine reach
- UDP 14789 (NodalArc's VXLAN; chart value `network.vxlanPort`) open between nodes
- Recommended: low-latency network between nodes (GbE or better)

## Building the Cluster

`build-k3s-cluster.sh --published-images`, attached to every release, builds K3s on each server
over ssh, joins them into one cluster and writes the kubeconfig; [Getting
Started](getting-started.md#step-1-machines) has the commands. Then install the release with Helm
as on a single machine. Every server pulls the release's images from ghcr.io.

## Container Registry Setup (Building From Source)

A developer who builds NodalArc's images from source needs a registry for them. Single-node deployments import images directly into the local container runtime. A cluster of several servers takes the images from one registry: the build machine pushes them there and every server pulls them.

`scripts/build-k3s-cluster.sh`, run on the build machine, does steps 1 to 3 for the cluster it builds: it runs the registry on the first server. The steps below are what it does, for a cluster built another way or a registry you already run.

### 1. Run the registry and point every server at it

`scripts/build-k3s-node.sh` does both. On the server that runs the registry:

```bash
sudo ./build-k3s-node.sh --registry <registry host>:<port> --serve-registry
```

and on every other server `--registry <registry host>:<port>` alone. `--serve-registry` installs Ubuntu's `docker-registry` package, configured with no credentials (NodalArc sends none) and with deletion enabled (`make nuke` removes NodalArc's images); `--registry` writes `/etc/rancher/k3s/registries.yaml` so K3s pulls from the registry over plain HTTP. Any other OCI registry works too: leave out `--serve-registry`.

### 2. Let the build machine push to it

Docker pushes over HTTPS unless told otherwise. On the build machine, name the registry in `/etc/docker/daemon.json`:

```json
{ "insecure-registries": ["<registry host>:<port>"] }
```

then restart Docker with `sudo systemctl restart docker`.

### 3. Point the build at it

In the source checkout on the build machine:

```bash
cp config.mk.example config.mk
```

Edit `config.mk`:

```makefile
# Hostname:port that every K3s node can pull from.
REGISTRY_HOST ?= <registry host>:<port>
```

With `REGISTRY_HOST` set, `make load` pushes NodalArc runtime images to the registry instead of importing locally. Helm image values are generated from the shared runtime image inventory; the installer accepts no other Helm arguments.

### 4. Build and deploy

```bash
make all
```

Works the same way from a clean NodalArc state: images are pushed to the registry and Helm references them there.

For full square-one validation on an existing K3s cluster:

```bash
make nuke && make all
```

For an already-running platform, use `make build && make load && make upgrade` rather than `make all`.

## Choosing the Nodes

By default the Node Agent runs on every node the scheduler admits, and session pods go only to nodes whose Node Agent is ready. Nothing needs a label. To keep NodalArc to a dedicated pool, set the chart values:

```yaml
placement:
  nodeSelector:
    nodepool: nodalarc
  tolerations:
    - key: nodalarc
      operator: Exists
      effect: NoSchedule
```

Pass them at install with `helm install ... --values placement.yaml`. `nodeSelector` limits the Node Agent, and with it session pods, to the nodes that carry the label. `tolerations` are needed only when the pool is tainted; the Node Agent and every session pod carry the same list. Control-plane nodes that carry the usual `node-role.kubernetes.io/control-plane` taint get no Node Agent unless the list tolerates it.

## Placement Policies

The placement policy controls how satellite pods are distributed across nodes. Set it in the session YAML:

```yaml
placement:
  policy: planePerNode
```

### allOnOne

All session pods on a single node. No cross-node traffic. This must be requested explicitly and is intended for single-node debugging or isolating cross-node behavior.

### planePerNode

Each orbital plane assigned to a separate K8s node. This is the default and recommended policy for multi-node deployments:

- **Intra-plane ISLs** (isl0, isl1) - pod-to-pod on the same node. Fast, no encapsulation.
- **Cross-plane ISLs** (isl2, isl3) - traverse VXLAN tunnels between nodes. Adds real network traversal.

This models realistic satellite constellation networking where intra-plane communication is "free" (same orbital shell) but cross-plane communication traverses real infrastructure.

### planeGroupPerNode

Groups of adjacent orbital planes share a node. Reduces the number of VXLAN tunnels while still distributing pods across nodes. Use when you have fewer nodes than orbital planes.

## How Cross-Node Links Work

When two pods are on different K8s nodes, the Node Agent creates a VXLAN tunnel between them:

```
Node A                                      Node B
+----------------------+                    +----------------------+
| leo-sat-p00s03 pod   |                    | leo-sat-p01s03 pod   |
|   isl2               |                    |               isl3   |
+-----|----------------+                    +----------------|-----+
      veth -- vxlan ------- UDP 14789 ------- vxlan -- veth
```

The VXLAN tunnel encapsulates Ethernet frames in UDP, carrying them across the physical network between nodes. From the perspective of the FRR routing daemon inside each pod, `isl2`/`isl3` look like normal network interfaces - FRR doesn't know or care that the physical path goes through a VXLAN tunnel.

### Substrate Latency Compensation

The physical network between nodes adds real latency to VXLAN-encapsulated packets. NodalArc compensates for this:

1. The Node Agent measures the physical round-trip time between each pair of nodes continuously (ICMP probes every 60 seconds)
2. The Scheduler subtracts the measured physical latency from the desired orbital latency when setting tc netem delay values
3. Result: `total_packet_delay = netem_delay + physical_network_delay = orbital_latency`

The emulated latency is always accurate regardless of the physical network topology between your nodes. If nodes are 1ms apart, netem subtracts 1ms. If they're 10ms apart, netem subtracts 10ms. The user sees the orbital latency.

## Capacity Planning

Per-node capacity is primarily memory-limited:

```
Available satellites per node ~ (node_RAM - 2 GB platform) / 18 MB per satellite
```

| Node RAM | Approximate satellite capacity |
|----------|-------------------------------|
| 8 GB | ~330 satellites |
| 16 GB | ~780 satellites |
| 32 GB | ~1,660 satellites |
| 64 GB | ~3,440 satellites |

Platform services (OME, Scheduler, NATS, etc.) only run once regardless of cluster size. They consume approximately 2 GB total.

### Long-delay queue capacity estimate

A long-delay link is one whose one-way delay can exceed `network.longDelayBoundSeconds` (10 s by default), such as an Earth-Mars trunk. Its packets in flight wait in kernel queues on the servers of its two ends: each end has a receive queue that delays what its peer sends, and a transmit queue before its rate shaper. NodalArc sizes every delay queue, local or long-delay, from the session's terminals: the transmitting terminal's configured transmit rate times the delay, at 1,500-byte frames, plus 1,000 packets. A terminal changed to a different rate in its catalog file changes its queues. Traffic above a queue's limit is dropped and reported as substrate loss.

NodalArc refuses a session whose long-delay queues would need more than a share of a server's memory, once before it creates the session's pods and again before it wires them, where the pods actually run. The refusal names the server, its budget, and each long-delay end with its peer, the peer's terminal and configured rate, the longest delay, and the bytes in flight and counted.

The check is an estimate and it counts long-delay queues only. It does not count local link queues (every link whose delay stays under the bound), the memory of session pods and platform services, or anything else the server runs. It cannot guarantee that a server never runs out of memory. Queued packets belong to no pod's memory limit, so raising a workload's memory limit does not make room for them.

| Chart value | Default | Meaning |
|-------------|---------|---------|
| `network.queueMemory.bytesPerBuffer` | 73728 | Memory counted per buffer of each transmit queue |
| `network.queueMemory.serverShare` | 0.1 | Share of each server's allocatable memory the long-delay queues may hold |

Each long-delay end counts:

- its receive queue: the peer terminal's transmit rate times the link's longest delay (the largest one-way delay the link can have over the session's ephemeris data), plus the rate shaper's burst (4 ms at the rate, at least one 8,800-byte frame), plus 1,000 frames of 8,800 bytes, all times 2.5. The factor covers the kernel's accounting for a queued frame, measured at 1.52 times a 1,514-byte frame and 1.89 times an 8,814-byte frame, with headroom.
- its transmit queue: 1,000 buffers of `bytesPerBuffer`. The rate does not bound this queue, and buffers up to 64 KiB (the kernel's default GSO and GRO limit) can enter it. The default of 73,728 bytes covers them on servers with 4 KiB pages. Node qualification marks a server's Node Agent not ready when its page size or its node interface's GSO or GRO limit needs more, and names the value it needs: 82,944 bytes for 16 KiB pages, 132,096 bytes for 64 KiB pages. The IPv4 BIG TCP limits (`gso_ipv4_max_size`, `gro_ipv4_max_size`) are not checked; the estimate assumes them at their default of 65,536.

When the Node Agent starts, it also checks that the kernel counts the packets a long-delay stop discards. A Node Agent that fails this check does not start and logs `STARTUP_PURGE_COUNT_INEXACT`.

The shipped Mars sessions at the defaults:

| Session | Estimate | Allocatable memory needed at the default share |
|---------|----------|-----------------------------------------------|
| `earth-mars-hdtn-relay` | 0.96 GiB | about 9.6 GiB on the server that holds both trunk ends |
| `earth-luna-mars-dtn` | 0.96 GiB | about 9.6 GiB on the server that holds both trunk ends |
| `mars-ion-relay-network` | none (no long-delay links) | none |

The shipped trunk terminal (`optical-mars-trunk`) transmits 1 Mbit/s, NASA's Mars Telecommunications Network requirement for the return link at the largest Earth-Mars range. Each trunk end counts 159 MiB in flight at the longest delay (1,335 s) and 489 MiB in all. With the default `planePerNode` placement both trunk ends run on one server. A session refused on a server can run there with terminals of lower rates in its catalog files, or with a higher `network.queueMemory.serverShare` when the server has memory to spare.

A TCP sender over a long-delay link keeps its unacknowledged data for two light times (about 45 minutes between Earth and Mars at their farthest). That data counts against the sending pod's memory limit, which its workload profile sets, not against this estimate. When the kernel kills a container for exceeding its limit, the container stays stopped (session pods do not restart containers), and the session's status message names it, for example `earth-relay-geo/hdtn OOMKilled (exit 137)`.

## VXLAN Tunnel Limits

Each cross-plane ISL between pods on different nodes creates one VXLAN tunnel. For a 176-satellite constellation with planePerNode on 4 nodes, approximately 176 VXLAN tunnels are created across the cluster. Linux handles thousands of VXLAN interfaces without issue.

Initial tunnel creation on cold start takes about 30 seconds for 176 tunnels. This is a one-time cost at session deployment, not a runtime bottleneck.

## Monitoring Cross-Node Health

```bash
# Count NodalArc VXLAN tunnels on a specific node (tunnel devices are named
# vx plus six hex digits by lib/nodalarc/runtime_naming.py)
ssh <server> "ip -o link show type vxlan | grep -c ': vx'"

# Check substrate latency measurements
kubectl logs -l app=nodalarc-node-agent -n nodalarc | grep substrate

# Verify cross-plane adjacencies form
kubectl exec leo-sat-p00s05 -n nodalarc -c frr-router -- vtysh -c "show isis neighbor" | grep isl2
```

## Network Requirements

| Traffic | Protocol | Port | Between |
|---------|----------|------|---------|
| VXLAN tunnels | UDP | 14789 (`network.vxlanPort`) | All compute nodes |
| NATS | TCP | 4222 | All pods -> NATS service |
| K8s API | TCP | 6443 | All nodes -> control plane |
| Registry | TCP | 5000 (typical) | All nodes -> registry |

Ensure your network firewall allows UDP 14789 between all nodes that will run session pods. The port is the chart value `network.vxlanPort`; choose one outside the hosts' ephemeral port range (32768 to 60999 by default) and the cluster's NodePort range (30000 to 32767 by default).
