#!/bin/bash
# Copyright 2024-2026 .chance (dotchance)
# K3s node configuration NodalArc requires on every node that runs session pods.
#
# Writes a K3s configuration drop-in and restarts K3s when the drop-in changed,
# then proves the running kubelet carries the settings. Run as root on each
# node; scripts/bootstrap-host.sh calls it after installing K3s.
#
# Idempotent: an unchanged drop-in neither rewrites the file nor restarts K3s.

set -euo pipefail

DROPIN_DIR=/etc/rancher/k3s/config.yaml.d
DROPIN="$DROPIN_DIR/60-nodalarc.yaml"

if [ "$(id -u)" -ne 0 ]; then
    echo "ERROR: configure-k3s-node.sh must run as root" >&2
    exit 1
fi
if ! command -v k3s >/dev/null 2>&1; then
    echo "ERROR: k3s is not installed on this node" >&2
    exit 1
fi

unit=""
for candidate in k3s k3s-agent; do
    if systemctl list-unit-files "$candidate.service" --no-legend 2>/dev/null | grep -q "^$candidate.service"; then
        unit="$candidate"
        break
    fi
done
if [ -z "$unit" ]; then
    echo "ERROR: no k3s or k3s-agent systemd unit on this node" >&2
    exit 1
fi

# The kubelet limits image pulls to 5 per second with a burst of 10 by default.
# A session creates every pod at once and each pod starts several containers,
# so a session of a few hundred pods issues hundreds of pull requests within a
# second. Requests over the limit fail with "pull QPS exceeded" and the
# container then waits out image-pull back-off (10, 20 and 40 s). A value of 0
# removes the kubelet's client-side limit; the registry still serves every pull.
#
# A server's API server deletes a collection with one worker by default, one
# object after another. Deleting a namespace deletes each of its resource
# collections, and a namespace that hosted sessions for an hour holds several
# thousand Events: 6,000 took 40 s to delete, 16 workers share the work.
desired="$(cat <<'YAML'
# Written by scripts/configure-k3s-node.sh. Do not edit by hand.
kubelet-arg+:
  - "registry-qps=0"
YAML
)"
if [ "$unit" = "k3s" ]; then
    desired="$desired
kube-apiserver-arg+:
  - \"delete-collection-workers=16\""
fi

changed=0
if [ ! -f "$DROPIN" ] || [ "$(cat "$DROPIN")" != "$desired" ]; then
    mkdir -p "$DROPIN_DIR"
    printf '%s\n' "$desired" > "$DROPIN.tmp"
    chmod 600 "$DROPIN.tmp"
    mv "$DROPIN.tmp" "$DROPIN"
    changed=1
    echo "  wrote $DROPIN"
else
    echo "  $DROPIN is current"
fi

# inotify instances are counted per user, and every container runtime shim
# (root) takes three. The kernel default of 128 instances per user runs out
# at about forty pods on one host; past it the kubelet cannot follow container
# logs and K3s stops watching its own certificate files ("too many open
# files"). A host that runs session pods needs room for hundreds of shims.
SYSCTL_FILE=/etc/sysctl.d/60-nodalarc-inotify.conf
sysctl_desired="$(cat <<'CONF'
# Written by scripts/configure-k3s-node.sh. Do not edit by hand.
fs.inotify.max_user_instances = 8192
fs.inotify.max_user_watches = 1048576
CONF
)"
if [ ! -f "$SYSCTL_FILE" ] || [ "$(cat "$SYSCTL_FILE")" != "$sysctl_desired" ]; then
    printf '%s\n' "$sysctl_desired" > "$SYSCTL_FILE"
    echo "  wrote $SYSCTL_FILE"
fi
sysctl -q -p "$SYSCTL_FILE"
if [ "$(sysctl -n fs.inotify.max_user_instances)" != "8192" ] \
    || [ "$(sysctl -n fs.inotify.max_user_watches)" != "1048576" ]; then
    echo "ERROR: the inotify limits did not take effect" >&2
    exit 1
fi
echo "  inotify: max_user_instances=8192 max_user_watches=1048576"


if [ "$changed" -eq 1 ]; then
    echo "  restarting $unit to apply the kubelet settings"
    systemctl restart "$unit"
fi

# Prove the running kubelet carries the setting. The kubelet serves its
# effective configuration at /configz through the API server's node proxy.
node_name="$(hostname)"
for _ in $(seq 1 90); do
    if qps="$(k3s kubectl get --raw "/api/v1/nodes/$node_name/proxy/configz" 2>/dev/null \
        | sed -n 's/.*"registryPullQPS":\([0-9]*\).*/\1/p')" && [ -n "$qps" ]; then
        break
    fi
    sleep 2
done
if [ -z "${qps:-}" ]; then
    echo "ERROR: could not read the kubelet configuration of node $node_name" >&2
    exit 1
fi
if [ "$qps" != "0" ]; then
    echo "ERROR: kubelet on $node_name runs registryPullQPS=$qps; expected 0" >&2
    exit 1
fi
echo "  kubelet on $node_name: registryPullQPS=0"

# Prove the running API server carries its setting: K3s logs the API server's
# full command line each time it starts it.
if [ "$unit" = "k3s" ]; then
    # The main process start: K3s logs the command line before it reports ready.
    started="$(systemctl show -p ExecMainStartTimestamp --value "$unit")"
    if ! journalctl -u "$unit" --since "$started" --no-pager 2>/dev/null \
        | grep 'Running kube-apiserver' | tail -1 | grep -q -- '--delete-collection-workers=16'; then
        echo "ERROR: the API server on $node_name does not run with --delete-collection-workers=16" >&2
        exit 1
    fi
    echo "  kube-apiserver on $node_name: delete-collection-workers=16"
fi
