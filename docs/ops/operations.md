# Operations

Day-to-day management of a running NodalArc deployment.

## Session Switching

### From the UI

Users can switch sessions from the browser's Sessions window. They select a
session and deploy it, or author one in the Session Builder. The platform handles the
transition automatically: it tears down the old session and brings up the new
one.

### Through the API

The Sessions window switches sessions through VS-API's REST API, and scripts can make the same
request; see the [API guide](../user/api.md). No manual teardown is needed: the platform stops
the old session before it starts the new one.

## Removing NodalArc

```bash
helm uninstall nodalarc -n nodalarc
```

Before Helm removes anything, the chart's uninstall hook:

1. Deletes the ConstellationSpec, so the Operator stops the running session and removes its pods
2. Waits until no session pod remains
3. Runs the Node Agent's cleaner on every server, which removes NodalArc's network devices and
   its emulated-LAN namespace and reports the server clean; a server that does not report clean
   fails the uninstall and is named

Helm then removes the platform: its Deployments, the Node Agent DaemonSet, services, ConfigMaps
and the NATS volume. It keeps VS-API's saved session data and the ConstellationSpec definition.
To remove those too:

```bash
kubectl delete namespace nodalarc
kubectl delete crd constellationspecs.nodalarc.io
```

Delete the namespace only after `helm uninstall`: deleting it while NodalArc runs leaves the
session's kernel state on the servers.

## Moving to Another Release

Remove the installed release, then install the other one:

```bash
helm uninstall nodalarc -n nodalarc
helm install nodalarc oci://ghcr.io/dotchance/nodalarc --version <release> \
  --namespace nodalarc --create-namespace --wait
```

VS-API's saved session data survives the move as long as the namespace stays.

## Health Monitoring

### Quick Status

```bash
helm status nodalarc -n nodalarc
helm test nodalarc -n nodalarc --logs
```

The test checks every platform Deployment, the Node Agent on every server, and that every
platform container runs the image digest the chart names.

### Pod Health

```bash
kubectl get pods -n nodalarc -o wide
```

All session pods should be `Running` with every container ready (`2/2` for a typical router pod). Platform pods should be `Running`. If pods are in `CrashLoopBackOff` or `ImagePullBackOff`, check logs:

```bash
kubectl logs <pod-name> -n nodalarc
```

### Session Phase

```bash
kubectl get constellationspec -n nodalarc \
  -o jsonpath='{.items[0].status.phase}'
```

Phases: `Creating` -> `Wiring` -> `Ready`. If stuck in `Creating` for more than 3 minutes, check Operator logs. If stuck in `Wiring`, check Node Agent logs.

### Service Logs

```bash
# Operator (session lifecycle, pod creation)
kubectl logs -l app=nodalarc-operator -n nodalarc --tail=50

# OME (orbital computation, event publishing)
kubectl logs -l app=nodalarc-ome -n nodalarc --tail=50

# Scheduler (link dispatch, reconciliation)
kubectl logs -l app=nodalarc-scheduler -n nodalarc --tail=50

# Node Agent (kernel operations, VXLAN)
kubectl logs -l app=nodalarc-node-agent -n nodalarc --tail=50

# VS-API (WebSocket, REST API)
kubectl logs -l app=nodalarc-vs-api -n nodalarc --tail=50
```

### Routing Verification

```bash
# Check IS-IS adjacencies on a satellite in the current session
SAT=$(kubectl get pods -n nodalarc \
  -o name | sed 's#pod/##' | grep -- '-sat-' | head -1)
kubectl exec "$SAT" -n nodalarc -c frr-router -- \
  vtysh -c "show isis neighbor"

# Check a ground node has adjacencies
GS=$(kubectl get pods -n nodalarc \
  -o name | sed 's#pod/##' | grep -E -- '-gw[0-9]+$' | head -1)
kubectl exec "$GS" -n nodalarc -c frr-router -- \
  vtysh -c "show isis neighbor"

# End-to-end ping through the current routing table
kubectl exec "$GS" -n nodalarc -c frr-router -- \
  ping -c 3 -W 5 <destination-loopback-or-prefix>
```

## SSH Terminal Key Management

SSH keys are generated per session by the Operator:

- Keypair stored in K8s Secret `nodalarc-terminal-keys`
- Public key mounted into all session pods
- Private key read by VS-API for browser terminal proxy
- Secret has ownerReference on the ConstellationSpec -> garbage collected on teardown

Keys are regenerated on every new session deployment. No manual key management needed.

### Extracting Keys (Direct SSH Access)

If users need to SSH directly (not through the browser):

```bash
kubectl get secret nodalarc-terminal-keys -n nodalarc \
  -o jsonpath='{.data.id_ed25519}' | base64 -d > nodalarc-ssh-key
chmod 600 nodalarc-ssh-key
```

Then SSH to any pod:

```bash
NODE=$(kubectl get pods -n nodalarc \
  -o name | sed 's#pod/##' | grep -- '-sat-' | head -1)
POD_IP=$(kubectl get pod "$NODE" -n nodalarc \
  -o jsonpath='{.status.podIP}')
ssh -i nodalarc-ssh-key -o StrictHostKeyChecking=no operator@$POD_IP
```

## Cleanup Levels

| Command | What It Removes |
|---------|----------------|
| `helm uninstall nodalarc -n nodalarc` | The session, the platform, and NodalArc's network devices on every server |
| `kubectl delete namespace nodalarc` | VS-API's saved session data, after the uninstall |
| `kubectl delete crd constellationspecs.nodalarc.io` | The ConstellationSpec definition, after the uninstall |

Developers who build NodalArc from source manage their install with `make`; the
[Developer Guide](../dev/getting-started.md) lists the targets.

## Backup and Recovery

User data lives in one place: the VS-API volume (PVC `nodalarc-session-data`), which holds the user catalog, builder-saved sessions and the history recorded for session runs. Back up that volume. Everything else is disposable:

- Session state lives in memory (OME, Scheduler) and is recomputed on restart
- NATS JetStream stores recent messages in a PVC, and recovery does not depend on it
- Workload configs are rendered by the profile's adapter from the resolved session at deploy time

`helm uninstall` keeps the VS-API volume; deleting the namespace deletes it, so copy it first if the user data matters.
