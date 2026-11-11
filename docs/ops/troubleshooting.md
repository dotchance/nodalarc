# Troubleshooting

Diagnosis and resolution for common deployment issues.

## Session Won't Start

### Pods Stuck in Pending

**Symptom:** `kubectl get pods -n nodalarc` shows session pods in `Pending` state.

**Cause:** Not enough resources on the target nodes, or no nodes match the placement policy.

**Diagnose:**
```bash
kubectl describe pod <stuck-pod> -n nodalarc | tail -20
```

Look for events like `Insufficient memory` or `node(s) didn't match Pod's node affinity`.

**Fix:**
- Check node resources: `kubectl top nodes`
- Reduce constellation size
- Add more nodes, or widen `placement.nodeSelector` in the chart values; a node is used once its Node Agent is ready
- Switch to `allOnOne` placement if using `planePerNode` with insufficient nodes

### Pods Stuck in ImagePullBackOff

**Symptom:** Pods show `ImagePullBackOff` or `ErrImagePull`.

**Cause:** The server cannot pull the image. A release pulls every image from ghcr.io, and
the uD3TN profiles pull D3TN's own image from registry.gitlab.com.

**Fix:**
- Check the pull error: `kubectl describe pod <pod> -n nodalarc | grep -A3 Events`
- Check that every server reaches ghcr.io (and registry.gitlab.com for the uD3TN sessions).
- On a cluster built from source, see the
  [Developer Guide](../dev/getting-started.md): `make status` reports images missing from the
  registry.

### Session Stuck in "Creating" Phase

**Symptom:** ConstellationSpec phase stays `Creating` for more than 3 minutes.

**Cause:** Operator can't create pods or deliver config.

**Diagnose:**
```bash
kubectl logs -l app=nodalarc-operator -n nodalarc --tail=100
```

Look for errors in pod creation or ConfigMap operations.

### Session Stuck in "Wiring" Phase

**Symptom:** All pods are Running but phase stays `Wiring`.

**Cause:** Node Agent can't wire network interfaces.

**Diagnose:**
```bash
kubectl logs -l app=nodalarc-node-agent -n nodalarc --tail=100
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
kubectl get pods -n nodalarc -l nodalarc.io/node-id \
  -o custom-columns='POD:.metadata.name,PROOF:.metadata.annotations.nodalarc\.io/wiring-status'
kubectl exec -n nodalarc -l app=nodalarc-node-agent -- \
  tail -100 /var/lib/nodalarc/node-agent/ops-events.jsonl
```

Any failed phase or `dirty_kernel=true` is authoritative. Fix the reported
kernel or manifest problem before expecting the Scheduler gate to open.

A pod held in `Init` whose annotation already reports ready did not receive
the proof file. Its `wiring-gate` log names the pod UID, run and network
namespace it waits for. The file is
`/var/lib/kubelet/pods/<pod uid>/volumes/kubernetes.io~empty-dir/wiring-status/status.json`
on the pod's host, and it must hold the same text as the annotation.

## No Routing Adjacencies

**Symptom:** `show isis neighbor` or `show ip ospf neighbor` returns empty.

### Check Interface State

```bash
NODE=$(kubectl get pods -n nodalarc \
  -o name | sed 's#pod/##' | grep -E -- '-sat-|-gw[0-9]+$' | head -1)
kubectl exec "$NODE" -n nodalarc -c frr-router -- \
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
kubectl exec "$NODE" -n nodalarc -c frr-router -- \
  vtysh -c "show daemons"
```

Should list `zebra`, `isisd` (or `ospfd`), and possibly `staticd`. If daemons aren't running, check the FRR config delivery:

```bash
kubectl exec "$NODE" -n nodalarc -c frr-router -- \
  cat /etc/frr/frr.conf
```

### Give It Time

Routing convergence takes 10-60 seconds depending on constellation size. IS-IS hello interval is 1 second; hold time is 3 seconds. A new adjacency forms in 3-4 seconds after interfaces are UP. Full SPF convergence for the entire constellation takes longer.

## Removal Issues

### Uninstall Fails in Its Hook

**Symptom:** `helm uninstall` reports that the `nodalarc-uninstall` hook failed.

**Cause:** The hook stops the session and requires every server to report itself clean. It
fails when the session does not unwind within ten minutes, or when a server's cleaner reports a
device it could not remove; its log names the server and the device:

```bash
kubectl logs -n nodalarc job/nodalarc-uninstall
```

**Fix:** Address what the log names and run `helm uninstall` again; the platform stays installed
until the hook succeeds, so the hook can run again. Do not delete devices by hand: the cleaner is
the one implementation that deletes NodalArc's device group and verifies its absence.

### Namespace Stuck in Terminating

**Symptom:** `kubectl get namespace nodalarc` shows `Terminating` indefinitely.

**Cause:** The namespace was deleted while NodalArc was installed. The ConstellationSpec's
finalizer waits for the Operator to unwind the session, and the Operator is gone with the
namespace.

**Fix:** Remove the ConstellationSpec's finalizer so the namespace can go:

```bash
kubectl patch constellationspec current-session -n nodalarc --type=merge \
  -p '{"metadata":{"finalizers":[]}}'
```

The session's network devices stay on the servers until the next install's `helm uninstall`,
whose hook cleans every server.

### Kernel State Left Behind

**Symptom:** VXLAN tunnels or veth pairs from a previous session interfere with a new deployment.

Every host device NodalArc creates is in its device group, 0x4E415243 (1312903747): on a server,
`ip -o link show group 1312903747` lists them. Emulated LAN bridges (site LANs), their ports and
their VXLAN ports are in the `emulated_lan` network namespace instead:
`sudo ip netns exec emulated_lan ip -o link` lists them.

**Fix:** `helm uninstall` removes them on every server and reports each server clean. Do not
delete devices by hand.

## Performance Issues

### High OME CPU Usage

**Expected:** The OME uses significant CPU during window computation (computing visibility for all satellite pairs). This is a burst at startup and at each orbital window boundary. Between windows, the pacing thread uses minimal CPU.

**Concern:** If CPU stays high continuously, check for crash-restart loops:
```bash
kubectl get pod -l app=nodalarc-ome -n nodalarc
```

Look at RESTARTS count. If > 0, check logs for the crash reason.

### Scheduler Falling Behind

**Symptom:** Links in the visualization don't match the event log timing.

**Check:**
```bash
kubectl logs -l app=nodalarc-scheduler -n nodalarc | \
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
kubectl logs -l app=nodalarc-ome -n nodalarc -c ensure-nats-streams
```

Check the init container logs for stream creation errors. Common cause: NATS pod not ready when OME init ran.

```bash
# Restart OME to retry stream creation
kubectl rollout restart deployment/ome -n nodalarc
```

## Getting More Help

1. Check service logs (see commands above)
2. Run `helm test nodalarc -n nodalarc --logs`
3. Verify kernel modules are loaded: `lsmod | grep mpls`
4. Verify sysctls are set: `sysctl net.ipv4.ip_forward` (should be 1)
5. File an issue on the GitHub repository with:
   - The output of `helm test nodalarc -n nodalarc --logs`
   - Relevant service logs (last 100 lines)
   - Constellation size and session config being deployed
   - Number of nodes and placement policy
