#!/bin/bash
# M9 health check — run after letting the system soak.
# Usage: bash tools/check-m9-health.sh
set -euo pipefail
export KUBECONFIG="${KUBECONFIG:-/etc/rancher/k3s/k3s.yaml}"

echo "=== Pod Status ==="
sudo kubectl get pods -n nodalarc --no-headers | grep -v "^sat-\|^gs-"

echo ""
echo "=== CRD ==="
sudo kubectl get constellationspec -n nodalarc -o wide

echo ""
echo "=== Scheduler Last 5 Snapshots ==="
sudo kubectl logs -n nodalarc deployment/nodalarc-scheduler --tail=20 2>/dev/null | grep "LinkStateSnapshot applied" | tail -5

echo ""
echo "=== GS Link Distances ==="
KEY=$(curl -s http://localhost:8080/api/v1/auth/token 2>/dev/null | python3 -c "import sys,json; print(json.load(sys.stdin).get('token',''))")
curl -s http://localhost:8080/api/v1/state -H "Authorization: Bearer $KEY" 2>/dev/null | python3 -c "
import sys, json, math
snap = json.load(sys.stdin)
nodes = {n['node_id']: n for n in snap.get('nodes',[])}
links = snap.get('links',[])
gs_links = [l for l in links if l['node_a'].startswith('gs-') or l['node_b'].startswith('gs-')]
isl_links = [l for l in links if not l['node_a'].startswith('gs-') and not l['node_b'].startswith('gs-')]
print(f'ISL: {len(isl_links)}, GS: {len(gs_links)}')
max_dist = 0
for l in gs_links:
    gs_id = l['node_a'] if l['node_a'].startswith('gs-') else l['node_b']
    sat_id = l['node_b'] if l['node_a'].startswith('gs-') else l['node_a']
    gs = nodes.get(gs_id,{})
    sat = nodes.get(sat_id,{})
    glat, glon = gs.get('lat_deg',0), gs.get('lon_deg',0)
    slat, slon = sat.get('lat_deg',0), sat.get('lon_deg',0)
    dlat = math.radians(slat - glat)
    dlon = math.radians(slon - glon)
    a = math.sin(dlat/2)**2 + math.cos(math.radians(glat))*math.cos(math.radians(slat))*math.sin(dlon/2)**2
    dist_km = 6371 * 2 * math.atan2(math.sqrt(a), math.sqrt(1-a))
    max_dist = max(max_dist, dist_km)
    flag = ' *** FAIL >2500km' if dist_km > 2500 else ''
    print(f'  {gs_id} -> {sat_id} dist={dist_km:.0f}km{flag}')

print()
if len(gs_links) > 7:
    print(f'FAIL: {len(gs_links)} GS links (expected <=7, 1 per station)')
elif max_dist > 2500:
    print(f'FAIL: max GS distance {max_dist:.0f}km > 2500km')
else:
    print(f'PASS: {len(gs_links)} GS links, max distance {max_dist:.0f}km')
"

echo ""
echo "=== NATS Stream State ==="
NATS_IP=$(sudo kubectl get pod -n nodalarc -l app=nodalarc-nats -o jsonpath='{.items[0].status.podIP}')
curl -s "http://${NATS_IP}:8222/jsz?streams=true" | python3 -c "
import sys, json
data = json.load(sys.stdin)
for acct in data.get('account_details', []):
    for stream in acct.get('stream_detail', []):
        name = stream.get('name', '')
        state = stream.get('state', {})
        print(f'{name}: {state.get(\"messages\",0)} msgs, {state.get(\"num_subjects\",0)} subjects')
"

echo ""
echo "=== OME Window ==="
sudo kubectl logs -n nodalarc deployment/ome --tail=5 2>/dev/null | grep -E "window|pacing"
