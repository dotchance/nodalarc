# Operations

Day-to-day management of a running NodalArc deployment.

## Session Switching

### From the UI

Users can switch sessions from the browser wizard. They select or build a
segment session, choose routing options, and deploy. The platform handles the
transition automatically: it tears down the old session and brings up the new
one.

### From the Command Line

```bash
make session DEFAULT_SESSION=catalog/nodalarc/sessions/earth-leo-walker.yaml
```

No manual teardown needed. The system switches sessions automatically.

## Teardown

When you need to fully stop the platform:

```bash
make teardown
```

This runs `scripts/na-teardown.sh` - a 9-step sequence that handles every failure mode:

1. Strip finalizers from session resources, delete ConstellationSpec CRs
2. Wait for session pods to terminate (force-deletes after 60s timeout)
3. Clean kernel state on all nodes via Node Agent (VXLAN tunnels, veth pairs, bridges)
4. Helm uninstall (removes all platform pods, services, ConfigMaps)
5. Wait for Node Agent pods to terminate
6. Delete namespace (forces through stuck finalizers if needed)
7. Delete cluster-scoped resources (CRD, ClusterRoles, ClusterRoleBindings)
8. Final local kernel state cleanup
9. Verify clean state

The script handles stuck pods, stuck finalizers, stuck namespaces, partially deployed sessions, and crashed operators. If it prints "Teardown complete" the system is clean.

**Never use `kubectl delete namespace nodalarc` directly.** It will hang on finalizers and leave kernel state behind.

### After Teardown

To bring the platform back up:

```bash
make install && make session
```

This assumes the required runtime images are already loaded into the selected image destination. If images were removed, run `make build && make load && make install && make session` instead.

## Upgrades

### Image Update (No Config Change)

After rebuilding images from new source code:

```bash
make build && make load && make upgrade
```

This does a Helm upgrade with the new image tags. Platform services restart with new images. The running session is preserved if the upgrade is backward-compatible.

### Full Redeploy

If Helm chart templates or configuration changed:

```bash
make build && make load && make reinstall && make session
```

`make reinstall` uses the official teardown path before installing. Do not replace it with `kubectl delete namespace`.

For a full square-one validation, including dependency/image/artifact cleanup while leaving K3s installed:

```bash
make nuke && make all
```

### Single Service Update

To rebuild and restart one service without affecting others:

```bash
make deploy-scheduler    # or deploy-ome, deploy-vs-api, etc.
```

## Health Monitoring

### Quick Status

```bash
make status
```

Shows pod counts, session phase, and active links.

### Pod Health

```bash
sudo KUBECONFIG=/etc/rancher/k3s/k3s.yaml kubectl get pods -n nodalarc -o wide
```

All session pods should be `Running 1/1`. Platform pods should be `Running`. If pods are in `CrashLoopBackOff` or `ImagePullBackOff`, check logs:

```bash
sudo KUBECONFIG=/etc/rancher/k3s/k3s.yaml kubectl logs <pod-name> -n nodalarc
```

### Session Phase

```bash
sudo KUBECONFIG=/etc/rancher/k3s/k3s.yaml kubectl get constellationspec -n nodalarc \
  -o jsonpath='{.items[0].status.phase}'
```

Phases: `Creating` → `Wiring` → `Ready`. If stuck in `Creating` for more than 3 minutes, check Operator logs. If stuck in `Wiring`, check Node Agent logs.

### Service Logs

```bash
# Operator (session lifecycle, pod creation)
sudo KUBECONFIG=/etc/rancher/k3s/k3s.yaml kubectl logs -l app=nodalarc-operator -n nodalarc --tail=50

# OME (orbital computation, event publishing)
sudo KUBECONFIG=/etc/rancher/k3s/k3s.yaml kubectl logs -l app=nodalarc-ome -n nodalarc --tail=50

# Scheduler (link dispatch, reconciliation)
sudo KUBECONFIG=/etc/rancher/k3s/k3s.yaml kubectl logs -l app=nodalarc-scheduler -n nodalarc --tail=50

# Node Agent (kernel operations, VXLAN)
sudo KUBECONFIG=/etc/rancher/k3s/k3s.yaml kubectl logs -l app=nodalarc-node-agent -n nodalarc --tail=50

# VS-API (WebSocket, REST API)
sudo KUBECONFIG=/etc/rancher/k3s/k3s.yaml kubectl logs -l app=nodalarc-vs-api -n nodalarc --tail=50
```

### Routing Verification

```bash
# Check IS-IS adjacencies on a satellite in the current session
SAT=$(sudo KUBECONFIG=/etc/rancher/k3s/k3s.yaml kubectl get pods -n nodalarc \
  -o name | sed 's#pod/##' | grep -- '-sat-' | head -1)
sudo KUBECONFIG=/etc/rancher/k3s/k3s.yaml kubectl exec "$SAT" -n nodalarc -c frr -- \
  vtysh -c "show isis neighbor"

# Check a ground node has adjacencies
GS=$(sudo KUBECONFIG=/etc/rancher/k3s/k3s.yaml kubectl get pods -n nodalarc \
  -o name | sed 's#pod/##' | grep -- '-gs-' | head -1)
sudo KUBECONFIG=/etc/rancher/k3s/k3s.yaml kubectl exec "$GS" -n nodalarc -c frr -- \
  vtysh -c "show isis neighbor"

# End-to-end ping through the current routing table
sudo KUBECONFIG=/etc/rancher/k3s/k3s.yaml kubectl exec "$GS" -n nodalarc -c frr -- \
  ping -c 3 -W 5 <destination-loopback-or-prefix>
```

## SSH Terminal Key Management

SSH keys are generated per session by the Operator:

- Keypair stored in K8s Secret `nodalarc-terminal-keys`
- Public key mounted into all session pods
- Private key read by VS-API for browser terminal proxy
- Secret has ownerReference on the ConstellationSpec → garbage collected on teardown

Keys are regenerated on every new session deployment. No manual key management needed.

### Extracting Keys (Direct SSH Access)

If users need to SSH directly (not through the browser):

```bash
sudo KUBECONFIG=/etc/rancher/k3s/k3s.yaml kubectl get secret nodalarc-terminal-keys -n nodalarc \
  -o jsonpath='{.data.id_ed25519}' | base64 -d > nodalarc-ssh-key
chmod 600 nodalarc-ssh-key
```

Then SSH to any pod:

```bash
NODE=$(sudo KUBECONFIG=/etc/rancher/k3s/k3s.yaml kubectl get pods -n nodalarc \
  -o name | sed 's#pod/##' | grep -- '-sat-' | head -1)
POD_IP=$(sudo KUBECONFIG=/etc/rancher/k3s/k3s.yaml kubectl get pod "$NODE" -n nodalarc \
  -o jsonpath='{.status.podIP}')
ssh -i nodalarc-ssh-key -o StrictHostKeyChecking=no operator@$POD_IP
```

## Cleanup Levels

| Command | What It Removes | When to Use |
|---------|----------------|-------------|
| `make teardown` | Session + platform (namespace, pods, CRD) | Normal shutdown |
| `make clean` | Frontend dist, Python caches | Force frontend rebuild |
| `make clean-images` | All nodalarc Docker images + build cache | Force full image rebuild |
| `make clean-deps` | Python .venv, node_modules | Force dependency reinstall |
| `make clean-registry` | Images from registry | Purge stale registry images |
| `make purge-containerd` | NodalArc images from K3s containerd cache | Purge node image caches |
| `make nuke` | All of the above | Square-one reset: K3s remains |

## Lifecycle State Transitions

| Current state | Correct transition |
|---------------|--------------------|
| Clean K3s or freshly nuked state | `make all` |
| Running platform, same session | `make build && make load && make upgrade` |
| Running platform, destructive refresh | `make build && make load && make reinstall && make session` |
| Running platform, switch session only | `make session DEFAULT_SESSION=catalog/nodalarc/sessions/<name>.yaml` |
| Unknown or polluted NodalArc state | `make nuke && make all` |
| Tooling broken and namespace must be removed | `make force-teardown`, then `make nuke` before redeploying |

## Backup and Recovery

NodalArc stores no persistent user data. All state is ephemeral:

- Session state lives in memory (OME, Scheduler) and is recomputed on restart
- NATS JetStream stores recent messages in a PVC but recovery doesn't depend on it
- VS-API SQLite snapshots are disposable (rebuilt from NATS streams)
- FRR configs are generated from templates + session YAML at deploy time

To "back up" a NodalArc installation, you only need the source code and your `config.mk` file. Everything else is generated.
