#!/bin/bash
# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
#
# Build one K3s server (or agent) for NodalArc. Run as root on each machine,
# the way K3s's own installer is run: the first machine alone, every further
# machine with the join command the first one prints.
#
#   build-k3s-node.sh [--link-mtu BYTES] [--agent] [--yes]
#
#   First machine:     build-k3s-node.sh
#   Further machines:  K3S_URL=https://<first>:6443 K3S_TOKEN=<token> build-k3s-node.sh
#
# What it changes, listed once and applied after one answer:
#   1. /etc/sysctl.d/60-nodalarc-inotify.conf, only for an inotify limit that
#      is below NodalArc's minimum; a larger value is left alone.
#   2. The MTU of the interface that carries the node's traffic, to the
#      emulated link MTU plus the VXLAN overhead, only when it is smaller;
#      persistent through netplan. Without netplan the requirement is printed
#      and the script stops.
#   3. /etc/rancher/k3s/config.yaml.d/60-nodalarc.yaml: the pods a server may
#      hold (max-pods=1000), no kubelet image pull limit (a session starts
#      hundreds of pods at once, and the kubelet's default of five pulls a
#      second answers "pull QPS exceeded" and backs off for up to forty
#      seconds), a /22 pod range per server, a readable kubeconfig, and
#      cluster-init on the first machine. Your own K3s settings stay in
#      /etc/rancher/k3s/config.yaml, which this script never touches; K3s
#      merges the two.
#   4. K3s's official installer (get.k3s.io) when K3s is absent; when K3s is
#      already installed and the drop-in changed, a restart of its unit.
#
# It checks nothing about the kernel: once NodalArc is installed, the Node
# Agent's readiness probe judges each server and names what it lacks.
# Idempotent: with nothing to change it exits 0 without asking.

set -euo pipefail
trap 'echo "ERROR: $(basename "$0") stopped at line $LINENO; nothing after that line was applied." >&2' ERR

# NodalArc's minimums, the values services/node_agent/qualification.py checks.
MIN_INOTIFY_INSTANCES=8192
MIN_INOTIFY_WATCHES=65536
MAX_PODS=1000
POD_RANGE_MASK_IPV4=22
K3S_INSTALLER_URL="https://get.k3s.io"

LINK_MTU=8800
ROLE=server
ASSUME_YES=0
while [ $# -gt 0 ]; do
    case "$1" in
        --link-mtu) LINK_MTU="$2"; shift 2 ;;
        --agent) ROLE=agent; shift ;;
        --yes) ASSUME_YES=1; shift ;;
        -h|--help) sed -n '5,32p' "$0"; exit 0 ;;
        *) echo "ERROR: unknown argument $1" >&2; exit 2 ;;
    esac
done
case "$LINK_MTU" in
    ''|*[!0-9]*) echo "ERROR: --link-mtu takes a number of bytes" >&2; exit 2 ;;
esac
if [ "$(id -u)" -ne 0 ]; then
    echo "ERROR: run as root" >&2
    exit 1
fi
if [ -n "${K3S_URL:-}" ] && [ -z "${K3S_TOKEN:-}" ]; then
    echo "ERROR: K3S_URL is set without K3S_TOKEN" >&2
    exit 2
fi
if [ "$ROLE" = agent ] && [ -z "${K3S_URL:-}" ]; then
    echo "ERROR: --agent needs K3S_URL and K3S_TOKEN of an existing server" >&2
    exit 2
fi

echo "=== NodalArc K3s node builder ==="

# ---------------------------------------------------------------------------
# What the machine has
# ---------------------------------------------------------------------------

changes=()

# inotify: raise only.
sysctl_lines=()
current_instances="$(sysctl -n fs.inotify.max_user_instances)"
current_watches="$(sysctl -n fs.inotify.max_user_watches)"
if [ "$current_instances" -lt "$MIN_INOTIFY_INSTANCES" ]; then
    sysctl_lines+=("fs.inotify.max_user_instances = $MIN_INOTIFY_INSTANCES")
    changes+=("raise fs.inotify.max_user_instances from $current_instances to $MIN_INOTIFY_INSTANCES (/etc/sysctl.d/60-nodalarc-inotify.conf)")
fi
if [ "$current_watches" -lt "$MIN_INOTIFY_WATCHES" ]; then
    sysctl_lines+=("fs.inotify.max_user_watches = $MIN_INOTIFY_WATCHES")
    changes+=("raise fs.inotify.max_user_watches from $current_watches to $MIN_INOTIFY_WATCHES (/etc/sysctl.d/60-nodalarc-inotify.conf)")
fi

# MTU: the interface of the default route carries the node's traffic. The
# VXLAN overhead is 50 bytes over IPv4 node addresses and 70 over IPv6.
cluster_interface="$(ip -o route get 1.1.1.1 2>/dev/null | sed -n 's/.* dev \([^ ]*\) .*/\1/p')"
overhead=50
if [ -z "$cluster_interface" ]; then
    cluster_interface="$(ip -o -6 route get 2606:4700:4700::1111 2>/dev/null | sed -n 's/.* dev \([^ ]*\) .*/\1/p')"
    overhead=70
fi
if [ -z "$cluster_interface" ]; then
    echo "ERROR: no default route; cannot identify the interface that carries the node's traffic." >&2
    exit 1
fi
needed_mtu=$((LINK_MTU + overhead))
current_mtu="$(cat "/sys/class/net/$cluster_interface/mtu")"
mtu_change=0
if [ "$current_mtu" -lt "$needed_mtu" ]; then
    max_mtu="$(ip -d link show dev "$cluster_interface" | sed -n 's/.* maxmtu \([0-9]*\).*/\1/p')"
    if [ -n "$max_mtu" ] && [ "$max_mtu" -lt "$needed_mtu" ]; then
        echo "ERROR: $cluster_interface supports an MTU of at most $max_mtu; NodalArc's emulated link MTU of $LINK_MTU needs $needed_mtu." >&2
        echo "       Choose a smaller emulated link MTU at install (chart value network.linkMtu) and run this script with --link-mtu." >&2
        exit 1
    fi
    if ! command -v netplan >/dev/null 2>&1 || [ ! -d /etc/netplan ]; then
        echo "ERROR: $cluster_interface has MTU $current_mtu and NodalArc needs $needed_mtu ($LINK_MTU plus $overhead bytes of VXLAN overhead)." >&2
        echo "       This machine does not use netplan, so set a persistent MTU of $needed_mtu on $cluster_interface with its own network configuration, then run this script again." >&2
        exit 1
    fi
    mtu_change=1
    changes+=("raise the MTU of $cluster_interface from $current_mtu to $needed_mtu (/etc/netplan/60-nodalarc-mtu.yaml)")
fi

# The first server initializes the cluster; a joining one (K3S_URL now, or
# K3S_URL in the unit environment K3s's installer wrote on an earlier run)
# does not.
first_server=1
if [ -n "${K3S_URL:-}" ] || grep -qs '^K3S_URL=' /etc/systemd/system/k3s.service.env /etc/systemd/system/k3s-agent.service.env; then
    first_server=0
fi

# K3s drop-in.
dropin_dir=/etc/rancher/k3s/config.yaml.d
dropin="$dropin_dir/60-nodalarc.yaml"
desired_dropin="# Written by NodalArc's build-k3s-node.sh. Your own settings belong in
# /etc/rancher/k3s/config.yaml; K3s merges this file with it.
write-kubeconfig-mode: \"0644\"
kubelet-arg+:
  - \"max-pods=$MAX_PODS\"
  - \"registry-qps=0\"
kube-controller-manager-arg+:
  - \"node-cidr-mask-size-ipv4=$POD_RANGE_MASK_IPV4\""
init_note=""
if [ "$first_server" -eq 1 ]; then
    desired_dropin="$desired_dropin
cluster-init: true"
    init_note=", cluster-init"
fi
dropin_change=0
if [ ! -f "$dropin" ] || [ "$(cat "$dropin")" != "$desired_dropin" ]; then
    dropin_change=1
    changes+=("write $dropin (max-pods=$MAX_PODS, no kubelet image pull limit, a /$POD_RANGE_MASK_IPV4 pod range per server, readable kubeconfig$init_note)")
fi

# K3s itself.
k3s_present=0
k3s_unit=""
if command -v k3s >/dev/null 2>&1; then
    k3s_present=1
    for candidate in k3s k3s-agent; do
        if systemctl list-unit-files "$candidate.service" --no-legend 2>/dev/null | grep -q "^$candidate.service"; then
            k3s_unit="$candidate"
            break
        fi
    done
    if [ "$dropin_change" -eq 1 ]; then
        changes+=("restart $k3s_unit so the drop-in takes effect (an API outage on a single-server cluster)")
    fi
else
    if [ -n "${K3S_URL:-}" ]; then
        changes+=("install K3s from $K3S_INSTALLER_URL as a $ROLE joining $K3S_URL")
    else
        changes+=("install K3s from $K3S_INSTALLER_URL as the first server")
    fi
fi

# ---------------------------------------------------------------------------
# One question
# ---------------------------------------------------------------------------

if [ "${#changes[@]}" -eq 0 ]; then
    echo "Nothing to change: this machine already carries every setting NodalArc needs."
else
    echo "This script will make these changes:"
    for change in "${changes[@]}"; do
        echo "  - $change"
    done
    if [ "$ASSUME_YES" -ne 1 ]; then
        printf 'Apply them? [y/N] '
        read -r answer
        case "$answer" in
            y|Y|yes|YES) ;;
            *) echo "Nothing changed."; exit 1 ;;
        esac
    fi
fi

# ---------------------------------------------------------------------------
# Apply
# ---------------------------------------------------------------------------

if [ "${#sysctl_lines[@]}" -gt 0 ]; then
    {
        echo "# Written by NodalArc's build-k3s-node.sh: inotify minimums for a"
        echo "# server that runs hundreds of session pods. Only limits that were"
        echo "# below the minimum are listed."
        printf '%s\n' "${sysctl_lines[@]}"
    } > /etc/sysctl.d/60-nodalarc-inotify.conf
    sysctl -q -p /etc/sysctl.d/60-nodalarc-inotify.conf
    echo "  inotify: instances $(sysctl -n fs.inotify.max_user_instances), watches $(sysctl -n fs.inotify.max_user_watches)"
fi

if [ "$mtu_change" -eq 1 ]; then
    cat > /etc/netplan/60-nodalarc-mtu.yaml <<NETPLAN
network:
  version: 2
  ethernets:
    ${cluster_interface}:
      mtu: ${needed_mtu}
NETPLAN
    chmod 600 /etc/netplan/60-nodalarc-mtu.yaml
    netplan generate
    netplan apply
    for _ in $(seq 1 30); do
        [ "$(cat "/sys/class/net/$cluster_interface/mtu")" = "$needed_mtu" ] \
            && [ "$(cat "/sys/class/net/$cluster_interface/operstate")" = "up" ] && break
        sleep 1
    done
    if [ "$(cat "/sys/class/net/$cluster_interface/mtu")" != "$needed_mtu" ] \
        || [ "$(cat "/sys/class/net/$cluster_interface/operstate")" != "up" ]; then
        echo "ERROR: $cluster_interface is not up at an MTU of $needed_mtu after applying the configuration." >&2
        exit 1
    fi
    echo "  $cluster_interface: MTU $needed_mtu"
fi

if [ "$dropin_change" -eq 1 ]; then
    mkdir -p "$dropin_dir"
    printf '%s\n' "$desired_dropin" > "$dropin.tmp"
    chmod 600 "$dropin.tmp"
    mv "$dropin.tmp" "$dropin"
    echo "  wrote $dropin"
fi

if [ "$k3s_present" -eq 0 ]; then
    echo "  installing K3s..."
    if [ -n "${K3S_URL:-}" ]; then
        INSTALL_K3S_EXEC="$ROLE" K3S_URL="$K3S_URL" K3S_TOKEN="$K3S_TOKEN" \
            sh -c "$(curl -sfL "$K3S_INSTALLER_URL")"
    else
        sh -c "$(curl -sfL "$K3S_INSTALLER_URL")"
    fi
elif [ "$dropin_change" -eq 1 ]; then
    echo "  restarting $k3s_unit..."
    systemctl restart "$k3s_unit"
fi

# ---------------------------------------------------------------------------
# What comes next
# ---------------------------------------------------------------------------

if [ "$ROLE" = server ]; then
    node_name="$(hostname)"
    if [ "${#changes[@]}" -gt 0 ]; then
        echo "  waiting for this server to be Ready..."
    fi
    for _ in $(seq 1 90); do
        if k3s kubectl get node "$node_name" --no-headers 2>/dev/null | grep -q " Ready"; then
            break
        fi
        sleep 2
    done
    if ! k3s kubectl get node "$node_name" --no-headers 2>/dev/null | grep -q " Ready"; then
        echo "ERROR: $node_name did not become Ready; see: journalctl -u k3s" >&2
        exit 1
    fi
    echo "  $node_name is Ready; pods allowed: $(k3s kubectl get node "$node_name" -o jsonpath='{.status.allocatable.pods}')"
    echo ""
    echo "=== Done ==="
    if [ "$first_server" -eq 1 ]; then
        node_ip="$(ip -o route get 1.1.1.1 2>/dev/null | sed -n 's/.* src \([^ ]*\) .*/\1/p')"
        token="$(cat /var/lib/rancher/k3s/server/node-token)"
        echo "To add another server, run on it as root:"
        echo "  K3S_URL=https://${node_ip}:6443 K3S_TOKEN=${token} $(basename "$0")"
        echo "To add a worker instead, add --agent to that command."
        echo ""
        echo "The kubeconfig is /etc/rancher/k3s/k3s.yaml. Next: install NodalArc with helm; see the getting-started page."
    fi
else
    echo ""
    echo "=== Done: this machine joined $K3S_URL as an agent ==="
fi
