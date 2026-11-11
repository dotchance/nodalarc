# Getting Started - Deployment

## Prerequisites

### Hardware

| Configuration | RAM | CPU | Disk | Satellites |
|--------------|-----|-----|------|-----------|
| Minimum (demo) | 8 GB | 4 cores | 40 GB | 36 |
| Recommended | 32 GB | 8 cores | 80 GB | 200+ |
| Multi-node | 32 GB per node | 8 cores per node | 80 GB per node | 1000+ |

In a 704-satellite session, a satellite router used about 35 MiB of server memory, and a router at rest uses about 15 millicores of CPU. The platform services use about 2 GiB, and the Node Agent about 0.4 GiB on each server. See the recent [scaling document](scaling.md) for more info.

### Software

- A Kubernetes cluster whose nodes admit privileged pods, or Ubuntu or Debian machines with root
  access (sudo) to build K3s on (Step 1)
- [kubectl](https://kubernetes.io/docs/tasks/tools/) and [Helm](https://helm.sh/docs/intro/install/)
  on the machine you work from

### Network

- Port 3000: Visualization frontend (VF), on the server that runs VS-API
- Port 8080: VS-API (REST/WebSocket), on the same server
- Ports 22 (per pod IP): SSH terminal access (internal cluster network)
- Inter-node: UDP 14789 (NodalArc's VXLAN, chart value `network.vxlanPort`) for multi-node deployments
- Host MTU: the emulated link MTU plus 50 bytes (70 over IPv6 node
  addresses) on the interface that carries cluster traffic and across the
  network between the hosts. The emulated link MTU is the chart value
  `network.linkMtu`, 8800 by default, so hosts carry 8850-byte packets. Every emulated
  interface has that MTU wherever its pods run: between hosts the packets
  travel inside VXLAN, and the hosts carry the overhead. When a session
  deploys, the Node Agent sends unfragmentable packets of the full wrapped
  size to every other host the session uses, and refuses to wire the session
  when the host network does not carry them.

## Step 1: Machines

Each NodalArc release on the [Releases](https://github.com/dotchance/nodalarc/releases) page
names its number and carries two scripts that build K3s for NodalArc. Pick the release once:

```bash
RELEASE=0.8.1
curl -fsSLO https://github.com/dotchance/nodalarc/releases/download/$RELEASE/build-k3s-cluster.sh
curl -fsSLO https://github.com/dotchance/nodalarc/releases/download/$RELEASE/build-k3s-node.sh
chmod +x build-k3s-cluster.sh build-k3s-node.sh
```

For a single machine, run the node builder on it as root. Its kubeconfig is K3s's own:

```bash
sudo ./build-k3s-node.sh
export KUBECONFIG=/etc/rancher/k3s/k3s.yaml
```

For several machines, run the cluster builder from the machine you work from, with every
machine's ssh address, the first one first. It runs the node builder on each machine over ssh,
joins them into one cluster and writes the cluster's kubeconfig:

```bash
./build-k3s-cluster.sh --published-images <first server> <server> ...
export KUBECONFIG=~/.kube/nodalarc.yaml
```

It runs unattended when your machine logs in to each machine with an ssh key and sudo there
needs no password; otherwise it asks for each password, under an INPUT NEEDED notice, and waits
for you.

The node builder lists the changes it will make and asks once: the inotify limits NodalArc needs (raised only when a limit is below the minimum), the MTU of the interface that carries the node's traffic (raised only when it is below the emulated link MTU plus the VXLAN overhead, persistent through netplan), a K3s drop-in with the pods a server may hold (1000), no kubelet image pull limit and a /22 pod range per server, and K3s from its official installer. Your own K3s settings belong in `/etc/rancher/k3s/config.yaml`, which the builder never touches. It writes no kernel setting: the Node Agent loads the MPLS kernel modules when a session needs them, and forwarding and MPLS settings are written inside each session pod. It is idempotent.

If you already have Kubernetes, skip this step: the Node Agent's readiness probe reports what a node lacks once NodalArc is installed.

On a cluster that enforces Pod Security admission (RKE2 with its CIS profile, many shared clusters), the Node Agent's privileged, host-PID pods are rejected unless NodalArc's namespace allows them. Create the namespace with the label before installing, and install into it without `--create-namespace`:

```bash
kubectl create namespace nodalarc
kubectl label namespace nodalarc pod-security.kubernetes.io/enforce=privileged
```

K3s enforces nothing by default, so on K3s the label changes nothing.

## Step 2: Install

Install the release's chart from ghcr.io. Every image it runs is published with the release and
pinned by digest; nothing is built.

```bash
helm install nodalarc oci://ghcr.io/dotchance/nodalarc --version $RELEASE \
  --namespace nodalarc --create-namespace --wait
```

On a cluster that enforces Pod Security admission, leave out `--create-namespace` and install
into the namespace labelled in Step 1.

### What the Install Creates

- Kubernetes namespace: `nodalarc`
- ConstellationSpec CRD (cluster-scoped)
- Platform services: OME, Scheduler, Node Agent (DaemonSet), VS-API, Operator, NATS
- Frontend: VF (nginx serving the React app)
- ConfigMaps: platform config and the NATS stream inventory
- PVCs: NATS JetStream file storage, and VS-API's saved session data
- The service account the chart's test and its uninstall hook run under

No session runs until you deploy one (Step 4). A session adds one pod per satellite, relay,
ground node or carried host, its ConfigMaps (rendered workload configs, the topology wiring
manifest) and the Secret with its SSH terminal keys.

## Step 3: Verify

```bash
helm test nodalarc -n nodalarc --logs
```

The test checks that every platform Deployment is available, that the Node Agent runs and is
ready on at least one server, and that every platform container runs the exact image digest the
chart names. It ends with `OK`.

The browser interface runs on port 3000 of the server that runs VS-API. When the install
finishes, Helm prints how to find that server (`helm status nodalarc -n nodalarc` prints it
again); this command gives its address:

```bash
kubectl -n nodalarc get pod -l app=nodalarc-vf -o jsonpath='{.items[0].status.hostIP}'
```

Open `http://<that address>:3000`.

## Step 4: Deploy a Session

Deploy a session from the Sessions window of the browser interface: pick one of the sessions
NodalArc ships, or one you built in the Session Builder. The page shows the session's phase while
its pods start, then the satellites and links.

### Verifying Routing

With `earth-leo-simple` running, a ground station forms an IS-IS adjacency with the satellite it
is linked to:

```bash
kubectl exec -n nodalarc earth-de-frankfurt-gw1 -c frr-router -- vtysh -c "show isis neighbor"
```

The satellite is listed with state `Up` on the station's terminal interface, `term0`. A
terminal on any router, opened from the browser interface, gives the same router CLI.

Sessions NodalArc ships:

| Session | Nodes | Description |
|---------|------:|-------------|
| `earth-leo-simple` | 41 | Single 36-satellite LEO ring with MBB-capable Starlink-style gateway sites |
| `earth-leo-walker` | 181 | Walker-delta LEO shell |
| `earth-leo-polar` | 45 | Polar LEO shell with high-latitude gateway sites |
| `earth-leo-polar-seam` | 45 | Polar seam experiment: range-driven seam link transitions |
| `earth-leo-polar-seam-tracking` | 45 | Polar seam experiment: tracking-limit exceedance and recovery |
| `earth-meo-gps` | 31 | GPS-altitude MEO shell with long-range RF gateways |
| `earth-geo-inmarsat` | 8 | GEO commercial-relay-style constellation |
| `earth-geo-tdrs` | 10 | GEO relay, TDRS-style |
| `earth-leo-heo-geo-luna-reachability` | 132 | LEO, HEO, GEO, a lunar relay constellation and lunar ground sites |
| `earth-luna-quic` | 81 | A QUIC client on Earth reaches a QUIC server on the lunar surface |
| `earth-luna-dtn` | 86 | The same sky with a uD3TN DTN relay vehicle carrying bundles to the Moon |
| `earth-luna-dtn-custody` | 86 | The DTN relay with bundle custody: bundles held while a peer is occluded |
| `earth-mars-hdtn-relay` | 18 | Three Mars bases reach Earth through Mars relays and an optical trunk, running NASA HDTN over LTP |
| `mars-ion-relay-network` | 12 | Three Mars bases exchange bundles through the Mars relays, running NASA JPL ION |
| `earth-luna-mars-dtn` | 104 | The Earth-Moon DTN sky and the Earth-Mars HDTN chain in one session |

Every routing domain in these sessions runs IS-IS. OSPF is also supported, selected per routing
domain; see the [Configuration Guide](configuration.md).

## Step 5: Remove

```bash
helm uninstall nodalarc -n nodalarc
```

Before Helm removes anything, the chart's uninstall hook stops the running session and has the
Node Agent on every server remove NodalArc's network devices and prove the server clean. Helm
then removes the platform. It keeps VS-API's saved session data and the ConstellationSpec
definition; these remove them:

```bash
kubectl delete namespace nodalarc
kubectl delete crd constellationspecs.nodalarc.io
```

Developers who build NodalArc from source install it with `make` instead; the
[Developer Guide](../dev/getting-started.md) lists the targets.

## Next Steps

- [Configuration Guide](configuration.md) - understand session YAML and building blocks
- [Multi-Node Deployment](multi-node.md) - for clusters with multiple compute nodes
- [Scaling](scaling.md) - capacity planning and performance characteristics
- [Operations](operations.md) - day-to-day management and upgrades
