# Troubleshooting

Diagnosis and resolution for common deployment issues.

## Session Won't Start

### Pods Stuck in Pending

**Symptom:** `kubectl get pods -n nodalarc` shows session pods in `Pending` state.

**Cause:** Not enough resources on the target nodes, or no nodes match the placement policy.

**Diagnose:**
```bash
sudo KUBECONFIG=/etc/rancher/k3s/k3s.yaml kubectl describe pod <stuck-pod> -n nodalarc | tail -20
```

Look for events like `Insufficient memory` or `node(s) didn't match Pod's node affinity`.

**Fix:**
- Check node resources: `kubectl top nodes`
- Reduce constellation size
- Add more nodes and label them: `kubectl label node <name> nodalarc.io/node-agent=true`
- Switch to `allOnOne` placement if using `planePerNode` with insufficient nodes

### Pods Stuck in ImagePullBackOff

**Symptom:** Pods show `ImagePullBackOff` or `ErrImagePull`.

**Cause:** Images aren't available on the target node.

**Fix:**
- Run `make status` first; it checks whether images are missing locally, missing from the registry, or present but not pullable.
- If images are built but not distributed, run `make load`.
- If source changed and the platform is already installed, run `make build && make load && make upgrade`.
- Multi-node: verify the registry is accessible from all nodes and check `config.mk` registry settings.
- Check: `sudo KUBECONFIG=/etc/rancher/k3s/k3s.yaml kubectl describe pod <pod> -n nodalarc | grep -A3 Events`

### Session Stuck in "Creating" Phase

**Symptom:** ConstellationSpec phase stays `Creating` for more than 3 minutes.

**Cause:** Operator can't create pods or deliver config.

**Diagnose:**
```bash
sudo KUBECONFIG=/etc/rancher/k3s/k3s.yaml kubectl logs -l app=nodalarc-operator -n nodalarc --tail=100
```

Look for errors in pod creation or ConfigMap operations.

### Session Stuck in "Wiring" Phase

**Symptom:** All pods are Running but phase stays `Wiring`.

**Cause:** Node Agent can't wire network interfaces.

**Diagnose:**
```bash
sudo KUBECONFIG=/etc/rancher/k3s/k3s.yaml kubectl logs -l app=nodalarc-node-agent -n nodalarc --tail=100
```

Common causes:
- Node Agent can't find pod PIDs (container runtime issue)
- Kernel modules not loaded (`mpls_router`, `mpls_iptunnel`)
- Missing permissions (hostPID, hostNetwork not enabled)
- Typed wiring status reports a failed phase or dirty kernel
- Required `HOST_IP` is missing from the Node Agent pod environment

**Fix:**
```bash
# Verify kernel modules
lsmod | grep mpls

# Load if missing
sudo modprobe mpls_router mpls_iptunnel
```

Check the typed wiring gate and Node Agent evidence:
```bash
sudo KUBECONFIG=/etc/rancher/k3s/k3s.yaml kubectl get configmap nodalarc-wiring-status -n nodalarc -o yaml
sudo KUBECONFIG=/etc/rancher/k3s/k3s.yaml kubectl exec -n nodalarc -l app=nodalarc-node-agent -- \
  tail -100 /var/lib/nodalarc/node-agent/ops-events.jsonl
```

Any failed phase or `dirty_kernel=true` is authoritative. Fix the reported
kernel or manifest problem before expecting the Scheduler gate to open.

## No Routing Adjacencies

**Symptom:** `show isis neighbor` or `show ip ospf neighbor` returns empty.

### Check Interface State

```bash
NODE=$(sudo KUBECONFIG=/etc/rancher/k3s/k3s.yaml kubectl get pods -n nodalarc \
  -o name | sed 's#pod/##' | grep -E -- '-sat-|-gs-' | head -1)
sudo KUBECONFIG=/etc/rancher/k3s/k3s.yaml kubectl exec "$NODE" -n nodalarc -c frr -- \
  ip -br link show
```

- ISL interfaces should show `UP` state
- `gnd0`, `gnd1`, ... show `UP` when a compatible ground link is active and
  `LOWERLAYERDOWN` when no link is active
- If interfaces show `DOWN`, check Scheduler logs first. A stale substrate
  measurement, unverified ACK, or dirty Node Agent response stops dispatch by
  design.

### Check FRR is Running

```bash
sudo KUBECONFIG=/etc/rancher/k3s/k3s.yaml kubectl exec "$NODE" -n nodalarc -c frr -- \
  vtysh -c "show daemons"
```

Should list `zebra`, `isisd` (or `ospfd`), and possibly `staticd`. If daemons aren't running, check the FRR config delivery:

```bash
sudo KUBECONFIG=/etc/rancher/k3s/k3s.yaml kubectl exec "$NODE" -n nodalarc -c frr -- \
  cat /etc/frr/frr.conf
```

### Give It Time

Routing convergence takes 10-60 seconds depending on constellation size. IS-IS hello interval is 1 second; hold time is 3 seconds. A new adjacency forms in 3-4 seconds after interfaces are UP. Full SPF convergence for the entire constellation takes longer.

## Teardown Issues

### Teardown Hung

**Symptom:** `make teardown` appears stuck.

The teardown script has built-in timeouts and force-delete logic for every failure mode. If it's taking more than 5 minutes, something unexpected is happening.

**Emergency reset:**
```bash
make nuke
```

This is the square-one reset - it removes NodalArc state, images, build artifacts, and dependencies while leaving K3s installed. Run `make all` afterward.

`make force-teardown` is a break-glass target only. It removes Kubernetes resources without deterministic host cleanup, so run `make nuke` before redeploying if you use it.

### Namespace Stuck in Terminating

**Symptom:** `kubectl get namespace nodalarc` shows `Terminating` indefinitely.

**Cause:** Finalizers blocking deletion.

The teardown script handles this automatically by patching finalizers. If running manually:

```bash
sudo KUBECONFIG=/etc/rancher/k3s/k3s.yaml kubectl get namespace nodalarc -o json | \
  python3 -c "import json,sys; ns=json.load(sys.stdin); ns['spec']['finalizers']=[]; print(json.dumps(ns))" | \
  sudo KUBECONFIG=/etc/rancher/k3s/k3s.yaml kubectl replace --raw "/api/v1/namespaces/nodalarc/finalize" -f -
```

### Kernel State Left Behind After Teardown

**Symptom:** VXLAN tunnels or veth pairs from a previous session interfere with a new deployment.

**Check:**
```bash
ip link show | grep -E "vx[0-9]{5}|vh[0-9]{5}|vp[0-9]{5}"
```

**Fix:** The teardown script cleans these, but if state persists:
```bash
# Remove all nodalarc-created interfaces
for iface in $(ip link show | grep -oE "(vx|vh|vp)[0-9]{5}" | sort -u); do
  sudo ip link del $iface
done
```

## Performance Issues

### High OME CPU Usage

**Expected:** The OME uses significant CPU during window computation (computing visibility for all satellite pairs). This is a burst at startup and at each orbital window boundary. Between windows, the pacing thread uses minimal CPU.

**Concern:** If CPU stays high continuously, check for crash-restart loops:
```bash
sudo KUBECONFIG=/etc/rancher/k3s/k3s.yaml kubectl get pod -l app=nodalarc-ome -n nodalarc
```

Look at RESTARTS count. If > 0, check logs for the crash reason.

### Scheduler Falling Behind

**Symptom:** Links in the visualization don't match the event log timing.

**Check:**
```bash
sudo KUBECONFIG=/etc/rancher/k3s/k3s.yaml kubectl logs -l app=nodalarc-scheduler -n nodalarc | \
  grep -i "timeout\|slow\|backlog"
```

The Scheduler dispatches to the Node Agent via NATS request/reply with a
60-second timeout. It does not paper over dirty or unverified state with
retries. If the dispatch worker reports a block reason, fix that cause before
expecting link state to advance.

Cross-node dispatch also requires a current generation-scoped substrate
measurement from the Node Agent. Logs mentioning stale, missing, or wrong
generation substrate measurements mean the Scheduler is refusing to apply an
unproven netem value.

### Visualization Slow

If the browser UI is slow:
- Check satellite count - very large constellations (500+) with all visual effects enabled use more GPU
- Toggle off trails (T), orbital paths (P), and labels (;) to reduce rendering load
- Check Chrome's Task Manager (Shift+Esc) for the tab's CPU/memory usage

## NATS Issues

### Stream Not Created

**Symptom:** Scheduler or VS-API logs show "stream not found" errors.

**Cause:** The OME init container creates all streams. If it fails, downstream consumers can't subscribe.

**Fix:**
```bash
sudo KUBECONFIG=/etc/rancher/k3s/k3s.yaml kubectl logs -l app=nodalarc-ome -n nodalarc -c init
```

Check the init container logs for stream creation errors. Common cause: NATS pod not ready when OME init ran.

```bash
# Restart OME to retry stream creation
sudo KUBECONFIG=/etc/rancher/k3s/k3s.yaml kubectl rollout restart deployment/nodalarc-ome -n nodalarc
```

## Getting More Help

1. Check service logs (see commands above)
2. Check `make status` output
3. Verify kernel modules are loaded: `lsmod | grep mpls`
4. Verify sysctls are set: `sysctl net.ipv4.ip_forward` (should be 1)
5. File an issue on the GitHub repository with:
   - `make status` output
   - Relevant service logs (last 100 lines)
   - Constellation size and session config being deployed
   - Number of nodes and placement policy
