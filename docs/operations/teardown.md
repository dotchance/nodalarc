# NodalArc Teardown Procedure

Always follow this order. Deviating from it causes kopf
finalizer to block namespace deletion.

## Correct Order

```bash
# Step 1: Delete the ConstellationSpec CR (session)
kubectl delete constellationspec current-session \
    -n nodalarc --timeout=60s

# Step 2: Wait for session pods to terminate
kubectl wait --for=delete pod -l nodalarc.io/node-id \
    -n nodalarc --timeout=120s 2>/dev/null || true

# Step 3: Uninstall the Helm release
# This removes platform pods and the CRD (Helm owns the CRD)
helm uninstall nodalarc -n nodalarc

# Step 4: Delete the namespace
kubectl delete namespace nodalarc --timeout=60s
```

## What Not To Do

Do NOT manually delete the CRD:

```bash
kubectl delete crd constellationspecs.nodalarc.io  # WRONG
```

The CRD is owned by the Helm release. Deleting it before
helm uninstall causes kopf's finalizer to block because
the CR still exists but its CRD is gone. Helm uninstall
handles CRD deletion correctly as part of the release.

## Fresh Install After Teardown

```bash
helm install nodalarc deploy/helm/ \
    -n nodalarc --create-namespace \
    --set ome.enabled=true \
    --set nodalpath.enabled=true
```

No other steps required.

## Adding a Node (M9+)

When cross-node ISL tunnels are implemented (M9), expand
the cluster by labeling the new node and pushing images:

```bash
# 1. Label the new node for Node Agent scheduling
kubectl label node <new-node> nodalarc.io/node-agent=true

# 2. Push images to the new node
bash scripts/push-images-to-node.sh <new-node-ip>
```

The Node Agent DaemonSet will automatically start on any
node labeled `nodalarc.io/node-agent=true`.
