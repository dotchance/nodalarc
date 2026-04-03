# CLI Reference

Advanced command-line examples for interacting with a running NodalArc constellation. Most users will use the web UI at http://localhost:3000 instead. These commands are for debugging, scripting, and automation.

All `kubectl` commands require the K3s kubeconfig:

```bash
export KUBECONFIG=/etc/rancher/k3s/k3s.yaml
# Or prefix each command with:
# sudo KUBECONFIG=/etc/rancher/k3s/k3s.yaml kubectl ...
```

## System Status

```bash
# Quick status - pod counts, session phase, active links
sudo make status

# List all running pods
kubectl get pods -n nodalarc -o wide

# Check which satellites are on which compute node (multi-node)
kubectl get pods -n nodalarc -o wide --no-headers | grep "^sat" | awk '{print $1, $7}' | sort

# Check the session phase
kubectl get constellationspec current-session -n nodalarc \
  -o jsonpath='Phase: {.status.phase}  Pods: {.status.readyPods}/{.status.podCount}'

# View Operator logs (session creation, config delivery)
kubectl logs -l app=nodalarc-operator -n nodalarc --tail=50

# View Scheduler logs (link dispatch, reconciliation)
kubectl logs -l app=nodalarc-scheduler -n nodalarc --tail=50

# View Node Agent logs (kernel operations, VXLAN creation)
kubectl logs -l app=nodalarc-node-agent -n nodalarc --tail=50

# View OME logs (orbital computation, event pacing)
kubectl logs -l app=nodalarc-ome -n nodalarc --tail=50
```

## Routing Protocol Inspection

Every satellite and ground station runs FRR. You can exec into any pod and use `vtysh` to inspect routing state, the same CLI you'd use on a physical router.

### IS-IS

```bash
# View IS-IS neighbors on a satellite
kubectl exec sat-p00s00 -n nodalarc -c frr -- vtysh -c "show isis neighbor"

# View the full IS-IS link-state database
kubectl exec sat-p00s00 -n nodalarc -c frr -- vtysh -c "show isis database detail"

# Check IS-IS routing table
kubectl exec sat-p00s00 -n nodalarc -c frr -- vtysh -c "show ip route isis"

# View IS-IS topology (SPF tree)
kubectl exec sat-p00s00 -n nodalarc -c frr -- vtysh -c "show isis topology"

# Check a ground station's IS-IS neighbors
kubectl exec gs-hawthorne -n nodalarc -c frr -- vtysh -c "show isis neighbor"

# View what a ground station is advertising (including default route)
kubectl exec gs-hawthorne -n nodalarc -c frr -- vtysh -c "show isis database detail gs-hawthorne"
```

### OSPF

```bash
# View OSPF neighbors
kubectl exec sat-p00s00 -n nodalarc -c frr -- vtysh -c "show ip ospf neighbor"

# View OSPF database
kubectl exec sat-p00s00 -n nodalarc -c frr -- vtysh -c "show ip ospf database"

# View OSPF routes
kubectl exec sat-p00s00 -n nodalarc -c frr -- vtysh -c "show ip route ospf"
```

### General Routing

```bash
# Full routing table
kubectl exec sat-p00s00 -n nodalarc -c frr -- vtysh -c "show ip route"

# Route to a specific destination
kubectl exec sat-p00s00 -n nodalarc -c frr -- vtysh -c "show ip route 10.1.0.1"

# Check the default route (should prefer ground station when connected)
kubectl exec sat-p00s00 -n nodalarc -c frr -- vtysh -c "show ip route 0.0.0.0/0"

# View the running FRR configuration
kubectl exec sat-p00s00 -n nodalarc -c frr -- vtysh -c "show running-config"

# MPLS label table (SR-MPLS sessions)
kubectl exec sat-p00s00 -n nodalarc -c frr -- vtysh -c "show mpls table"
```

## Connectivity Testing

```bash
# Ping between two satellites
kubectl exec sat-p00s00 -n nodalarc -c frr -- ping -c 5 10.1.0.1

# Ping from a ground station to a satellite loopback
kubectl exec gs-hawthorne -n nodalarc -c frr -- ping -c 5 10.0.0.1

# Ping across the full constellation (ground station to ground station)
kubectl exec gs-hawthorne -n nodalarc -c frr -- ping -c 5 10.255.4.1

# Traceroute showing the hop-by-hop path
kubectl exec gs-hawthorne -n nodalarc -c frr -- traceroute 10.255.4.1
```

## Interface and Link Inspection

```bash
# List all interfaces in a satellite pod
kubectl exec sat-p00s00 -n nodalarc -c frr -- ip -br link show

# Check interface status (UP/DOWN/LOWERLAYERDOWN)
kubectl exec sat-p00s00 -n nodalarc -c frr -- ip link show gnd0

# View tc qdisc (latency shaping) on an ISL interface
kubectl exec sat-p00s00 -n nodalarc -c frr -- tc qdisc show dev isl0

# View tc qdisc on a cross-node VXLAN interface
kubectl exec sat-p00s00 -n nodalarc -c frr -- tc qdisc show dev isl2

# Check host-side VXLAN interfaces (run on the host, not in a pod)
sudo ip link show | grep -E "vx[0-9]{5}|vh[0-9]{5}" | head -10

# Count VXLAN tunnels on the local node
sudo ip link show | grep -cE "vx[0-9]{5}"
```

## Ground Station Monitoring

```bash
# Which ground stations currently have active satellite connections?
for gs in $(kubectl get pods -n nodalarc -l nodalarc.io/role=ground-station \
    -o jsonpath='{.items[*].metadata.name}'); do
  nbr=$(kubectl exec $gs -n nodalarc -c frr -- vtysh -c "show isis neighbor" 2>&1 | \
    grep "Up" | awk '{print $1}')
  echo "$gs → ${nbr:-(no connection)}"
done

# Watch ground station handoffs in real time (poll every 10 seconds)
while true; do
  echo "=== $(date) ==="
  for gs in gs-hawthorne gs-ashburn gs-frankfurt; do
    nbr=$(kubectl exec $gs -n nodalarc -c frr -- vtysh -c "show isis neighbor" 2>&1 | \
      grep "Up" | awk '{print $1}')
    echo "  $gs → ${nbr:-(idle)}"
  done
  sleep 10
done

# Verify a ground station is originating a default route
kubectl exec gs-hawthorne -n nodalarc -c frr -- \
  vtysh -c "show isis database detail gs-hawthorne" | grep "0.0.0.0/0"
```

## VS-API Queries

The VS-API at http://localhost:8080 provides REST access to all constellation state. See the [VS-API Reference](vs-api-reference.md) for full documentation.

All API requests require a Bearer token. Fetch it first:

```bash
TOKEN=$(curl -s http://localhost:8080/api/v1/auth/token | python3 -c "import json,sys; print(json.load(sys.stdin)['token'])")
```

Then use `$TOKEN` in all requests:

```bash
# Full constellation state snapshot
curl -s -H "Authorization: Bearer $TOKEN" http://localhost:8080/api/v1/state | python3 -m json.tool
```

```bash
# Count nodes and links
curl -s -H "Authorization: Bearer $TOKEN" http://localhost:8080/api/v1/state | python3 -c "
import json, sys
s = json.load(sys.stdin)
sats = sum(1 for n in s['nodes'] if n['node_type'] == 'satellite')
gs = sum(1 for n in s['nodes'] if n['node_type'] == 'ground_station')
print(f'{sats} satellites, {gs} ground stations, {len(s[\"links\"])} active links')
"
```

```bash
# Find active ground station links with latency
curl -s -H "Authorization: Bearer $TOKEN" http://localhost:8080/api/v1/state | python3 -c "
import json, sys
s = json.load(sys.stdin)
for link in s['links']:
    if link.get('link_type') and 'ground' in link['link_type']:
        print(f\"{link['node_a']} <-> {link['node_b']}  {link['latency_ms']:.1f}ms  {link['range_km']:.0f}km\")
"
```

```bash
# Look up a specific satellite's position and neighbors
curl -s -H "Authorization: Bearer $TOKEN" http://localhost:8080/api/v1/state | python3 -c "
import json, sys
node = next((n for n in json.load(sys.stdin)['nodes'] if n['node_id'] == 'sat-P00S00'), None)
if node:
    print(f\"Position: {node['lat_deg']:.2f}N {node['lon_deg']:.2f}E  Alt: {node['alt_km']:.0f}km\")
    print(f\"Neighbors: {node['neighbor_count']}  ISL: {node['isl_count']}  Ground: {node['gnd_count']}\")
"
```

```bash
# Trace the forwarding path between two nodes
curl -s -X POST -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  http://localhost:8080/api/v1/trace \
  -d '{"src_node": "gs-hawthorne", "dst_node": "gs-frankfurt"}'
```

```bash
# Query link events in a time window
curl -s -H "Authorization: Bearer $TOKEN" \
  'http://localhost:8080/api/v1/links?start=2026-01-01T00:00:00Z&end=2026-01-01T00:10:00Z' | \
  python3 -m json.tool
```

```bash
# Stream live state over WebSocket
python3 -c "
import asyncio, json, websockets, urllib.request
token = json.loads(urllib.request.urlopen('http://localhost:8080/api/v1/auth/token').read())['token']
async def main():
    async with websockets.connect(f'ws://localhost:8080/ws/v1/state?token={token}') as ws:
        async for msg in ws:
            s = json.loads(msg)
            print(f\"[{s['sim_time'][:19]}] {len(s['links'])} links\")
asyncio.run(main())
"
```

## Latency Monitoring

```bash
# Watch latency change on a cross-plane ISL over time
while true; do
  lat=$(kubectl exec sat-p00s00 -n nodalarc -c frr -- tc qdisc show dev isl2 2>&1 | \
    grep -oP 'delay \K[0-9.]+ms')
  echo "$(date +%H:%M:%S) isl2: $lat"
  sleep 10
done

# Compare local ISL latency vs cross-node VXLAN ISL latency
echo "isl0 (local):"
kubectl exec sat-p00s00 -n nodalarc -c frr -- tc qdisc show dev isl0 | grep netem
echo "isl2 (cross-node VXLAN):"
kubectl exec sat-p00s00 -n nodalarc -c frr -- tc qdisc show dev isl2 | grep netem

# Check substrate latency compensation value
kubectl get configmap nodalarc-substrate-latency -n nodalarc -o jsonpath='{.data}'
```

## Session Management

```bash
# Start a session
sudo make session DEFAULT_SESSION=configs/sessions/starlink-176-isis-te.yaml

# Tear down the current session
sudo make teardown

# Rebuild and restart all core services (hot reload after code changes)
sudo make deploy-all

# Rebuild and restart just the scheduler
sudo make deploy-scheduler

# Full nuke - teardown + remove all images and dependencies
sudo make nuke
```
