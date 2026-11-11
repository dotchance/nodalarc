#!/bin/bash
# Copyright 2024-2026 .chance (dotchance)
# SPDX-License-Identifier: AGPL-3.0-only
# Additional terms under AGPL-3.0 Section 7 are in the NOTICE file.
#
# Build a K3s cluster for NodalArc on machines this build machine reaches over
# ssh, and connect this build machine to it. Run it in the NodalArc checkout
# as yourself, not as root:
#
#   scripts/build-k3s-cluster.sh [--registry-port PORT] [--yes] SERVER [SERVER ...]
#
# Each SERVER is an ssh destination ([user@]host) whose user may run sudo
# there. The first SERVER starts the cluster and runs the image registry that
# the build machine pushes to and every server pulls from; the others join
# it. On every server the script copies scripts/build-k3s-node.sh there and
# runs it with sudo, and that script lists its changes and asks before
# applying them (--yes answers for it and for this script).
#
# It runs unattended when this machine logs in to every server with an ssh key
# and sudo there asks for no password. Otherwise it stops at each password
# prompt, ssh logins first and the sudo prompts as each server is built, and
# shows an INPUT NEEDED notice above every one: ssh gives up on a login after
# the server's LoginGraceTime (2 minutes by default) and sudo after its
# passwd_timeout (5 minutes by default).
#
# On this machine it then:
#   1. writes the cluster's kubeconfig to ~/.kube/nodalarc.yaml, naming the
#      first server by its address;
#   2. sets KUBECONFIG and REGISTRY_HOST in config.mk (copied from
#      config.mk.example when it is absent);
#   3. adds the registry to "insecure-registries" in /etc/docker/daemon.json
#      (with sudo) and reloads Docker, which keeps running containers;
#   4. checks that every server is Ready, that the registry answers and that
#      Docker pushes to it over plain HTTP.
#
# Then run make all. To add servers later, run it again with every server,
# the first one first; servers already in the cluster keep their settings.

set -euo pipefail

REGISTRY_PORT=5000
ASSUME_YES=0
servers=()
while [ $# -gt 0 ]; do
    case "$1" in
        --registry-port) REGISTRY_PORT="$2"; shift 2 ;;
        --yes) ASSUME_YES=1; shift ;;
        -h|--help) sed -n '/^# Build a K3s cluster/,/^# the first one first/p' "$0"; exit 0 ;;
        -*) echo "ERROR: unknown argument $1" >&2; exit 2 ;;
        *) servers+=("$1"); shift ;;
    esac
done
case "$REGISTRY_PORT" in
    ''|*[!0-9]*) echo "ERROR: --registry-port takes a port number" >&2; exit 2 ;;
esac
if [ "${#servers[@]}" -eq 0 ]; then
    echo "ERROR: name at least one server: $(basename "$0") [--registry-port PORT] [--yes] SERVER [SERVER ...]" >&2
    exit 2
fi
if [ "$(id -u)" -eq 0 ]; then
    echo "ERROR: run this as yourself, not as root; it uses sudo where it needs to." >&2
    exit 1
fi

checkout="$(cd "$(dirname "$0")/.." && pwd)"
builder="$checkout/scripts/build-k3s-node.sh"
config_mk="$checkout/config.mk"
kubeconfig="$HOME/.kube/nodalarc.yaml"
daemon_json=/etc/docker/daemon.json
for f in "$builder" "$checkout/config.mk.example"; do
    if [ ! -f "$f" ]; then
        echo "ERROR: $f is missing; run this script from a NodalArc checkout." >&2
        exit 1
    fi
done

# make reads KUBECONFIG and REGISTRY_HOST from the environment before config.mk,
# so a value exported in this shell would point make somewhere else.
for var in KUBECONFIG REGISTRY_HOST; do
    if [ -n "${!var:-}" ]; then
        echo "ERROR: $var is set in your environment (${!var}); make would use it instead of the" >&2
        echo "       value this script writes to config.mk. Unset it and run this script again." >&2
        exit 1
    fi
done

for tool in ssh scp jq curl kubectl docker; do
    if ! command -v "$tool" >/dev/null 2>&1; then
        echo "ERROR: $tool is not installed; run sudo scripts/bootstrap-dev-machine.sh first." >&2
        exit 1
    fi
done
if ! docker info >/dev/null 2>&1; then
    echo "ERROR: Docker does not answer for your account. Check that its daemon runs and that your" >&2
    echo "       account is in the docker group (after joining it, log out and back in)." >&2
    exit 1
fi

# One ssh connection per server carries every command and copy, so a password
# is asked once per server.
work="$(mktemp -d)"
if [ -z "$work" ] || [ ! -d "$work" ]; then
    echo "ERROR: could not create a working directory" >&2
    exit 1
fi
# The connections stay open while the script runs (the EXIT trap closes them):
# a shorter lifetime closed the first server's connection while the others were
# built, and its password was asked again. The 4-hour bound only matters when the
# script is killed before its trap runs.
ssh_opts=(-o ControlMaster=auto -o "ControlPath=$work/%C" -o ControlPersist=4h)
cleanup() {
    for server in "${servers[@]}"; do
        ssh "${ssh_opts[@]}" -O exit "$server" >/dev/null 2>&1 || true
    done
    rm -rf -- "$work"
}
trap cleanup EXIT

tty_flag=()
if [ -t 0 ]; then
    tty_flag=(-t)
fi

# A notice above every prompt that waits for the user's password.
if [ -t 1 ]; then
    frame_color=$'\033[1;36m'; title_color=$'\033[1;33m'; color_off=$'\033[0m'
else
    frame_color=""; title_color=""; color_off=""
fi
input_notice() {
    local title="$1" width=66 line rule
    shift
    for line in "$title" "$@"; do
        if [ $(( ${#line} + 4 )) -gt "$width" ]; then
            width=$(( ${#line} + 4 ))
        fi
    done
    rule="$(printf '%*s' $(( width - 2 )) '' | sed 's/ /━/g')"
    printf '\n%s┏%s┓%s\n' "$frame_color" "$rule" "$color_off"
    printf '%s┃%s %s%-*s%s %s┃%s\n' "$frame_color" "$color_off" "$title_color" $(( width - 4 )) "$title" "$color_off" "$frame_color" "$color_off"
    printf '%s┣%s┫%s\n' "$frame_color" "$rule" "$color_off"
    for line in "$@"; do
        printf '%s┃%s %-*s %s┃%s\n' "$frame_color" "$color_off" $(( width - 4 )) "$line" "$frame_color" "$color_off"
    done
    printf '%s┗%s┛%s\n' "$frame_color" "$rule" "$color_off"
}

# Open the one ssh connection to every server now, so the logins that need a
# password are all asked at the start, before the long part of the build.
for server in "${servers[@]}"; do
    if ssh "${ssh_opts[@]}" -o BatchMode=yes -o ConnectTimeout=15 "$server" true 2>/dev/null; then
        continue
    fi
    if [ "${#tty_flag[@]}" -eq 0 ]; then
        echo "ERROR: $server needs a password or a host key confirmation, and this script has no terminal" >&2
        echo "       to ask on. Run it from a terminal, or give this machine key-based login to $server." >&2
        exit 1
    fi
    input_notice "INPUT NEEDED  |  ssh login to $server" \
        "This machine has no key-based login to $server, so ssh asks you" \
        "to confirm the server's host key (the first time only) and to type" \
        "the password of your account there." \
        "Answer at the prompt below: the server closes an unanswered login" \
        "after its LoginGraceTime, 2 minutes by default."
    ssh "${ssh_opts[@]}" -o ConnectTimeout=15 "$server" true
done

# sudo on a server asks for a password unless its sudoers say otherwise.
notice_if_sudo_asks() {
    local server="$1"
    if ssh "${ssh_opts[@]}" "$server" 'sudo -n true' 2>/dev/null; then
        return
    fi
    if [ "${#tty_flag[@]}" -eq 0 ]; then
        echo "ERROR: sudo on $server asks for a password, and this script has no terminal to ask on." >&2
        exit 1
    fi
    input_notice "INPUT NEEDED  |  sudo password on $server" \
        "Building $server runs scripts/build-k3s-node.sh with sudo, and sudo" \
        "there asks for the password of your account on $server." \
        "Type it at the [sudo] prompt below: sudo gives up after its" \
        "passwd_timeout, 5 minutes by default, and the build stops."
}

# The address a server gives its own cluster traffic: the source address of
# its default route, the address build-k3s-node.sh registers the server with.
server_address() {
    local address
    address="$(ssh "${ssh_opts[@]}" "$1" 'ip -o route get 1.1.1.1 2>/dev/null || ip -o -6 route get 2606:4700:4700::1111' \
        | sed -n 's/.* src \([^ ]*\) .*/\1/p' | head -n 1)"
    if [ -z "$address" ]; then
        echo "ERROR: $1 has no default route; cannot find the address of its cluster traffic." >&2
        exit 1
    fi
    case "$address" in
        *:*) printf '[%s]\n' "$address" ;;
        *) printf '%s\n' "$address" ;;
    esac
}

# Copy build-k3s-node.sh to a server and run it there with sudo. With a token
# file, the first server's join token is copied into it in the same session,
# so sudo asks for a password once.
run_builder() {
    local server="$1" env_args="$2" builder_args="$3" token_file="${4:-}" remote rc
    remote="$(ssh "${ssh_opts[@]}" "$server" 'mktemp /tmp/nodalarc-build-k3s-node.XXXXXX')"
    case "$remote" in
        /tmp/nodalarc-build-k3s-node.*) ;;
        *) echo "ERROR: could not create a file on $server" >&2; exit 1 ;;
    esac
    scp -q "${ssh_opts[@]}" "$builder" "$server:$remote"
    local command="sudo env $env_args bash $remote $builder_args; rc=\$?"
    if [ -n "$token_file" ]; then
        command="$command; if [ \$rc -eq 0 ]; then sudo cat /var/lib/rancher/k3s/server/node-token > $token_file || rc=\$?; fi"
        command="$command; if [ \$rc -ne 0 ]; then rm -f -- $token_file; fi"
    fi
    command="$command; rm -f -- $remote; exit \$rc"
    echo ""
    echo "=== $server ==="
    notice_if_sudo_asks "$server"
    rc=0
    ssh "${ssh_opts[@]}" "${tty_flag[@]}" "$server" "$command" || rc=$?
    if [ "$rc" -ne 0 ]; then
        echo "ERROR: building $server stopped (exit $rc); see its output above." >&2
        exit 1
    fi
}

# ---------------------------------------------------------------------------
# What will happen
# ---------------------------------------------------------------------------

first="${servers[0]}"
first_address="$(server_address "$first")"
registry="$first_address:$REGISTRY_PORT"

daemon_change=0
daemon_current='{}'
if [ -f "$daemon_json" ]; then
    daemon_current="$(cat "$daemon_json")"
    if ! jq -e 'type == "object"' <<<"$daemon_current" >/dev/null 2>&1; then
        echo "ERROR: $daemon_json is not a JSON object; fix it before adding the registry to it." >&2
        exit 1
    fi
fi
if ! jq -e --arg r "$registry" '(.["insecure-registries"] // []) | index($r)' <<<"$daemon_current" >/dev/null; then
    daemon_change=1
fi

echo "=== NodalArc cluster builder ==="
echo "Servers, each built with scripts/build-k3s-node.sh, which lists its own changes:"
echo "  - $first: starts the cluster at https://$first_address:6443 and runs the image registry $registry"
for server in "${servers[@]:1}"; do
    echo "  - $server: joins the cluster"
done
echo "On this machine:"
echo "  - write $kubeconfig"
if [ -f "$config_mk" ]; then
    echo "  - set KUBECONFIG and REGISTRY_HOST in $config_mk"
else
    echo "  - create $config_mk from config.mk.example with KUBECONFIG and REGISTRY_HOST set"
fi
if [ "$daemon_change" -eq 1 ]; then
    echo "  - add $registry to insecure-registries in $daemon_json and reload Docker"
fi
if [ "$ASSUME_YES" -ne 1 ]; then
    printf 'Go ahead? [y/N] '
    read -r answer
    case "$answer" in
        y|Y|yes|YES) ;;
        *) echo "Nothing changed."; exit 1 ;;
    esac
fi
builder_yes=""
if [ "$ASSUME_YES" -eq 1 ]; then
    builder_yes="--yes"
fi

# ---------------------------------------------------------------------------
# The servers
# ---------------------------------------------------------------------------

remote_token_file="$(ssh "${ssh_opts[@]}" "$first" 'mktemp /tmp/nodalarc-token.XXXXXX')"
case "$remote_token_file" in
    /tmp/nodalarc-token.*) ;;
    *) echo "ERROR: could not create a file on $first" >&2; exit 1 ;;
esac
run_builder "$first" "" "--registry $registry --serve-registry $builder_yes" "$remote_token_file"
token="$(ssh "${ssh_opts[@]}" "$first" "cat $remote_token_file; rm -f -- $remote_token_file")"
case "$token" in
    ''|*[[:space:]]*) echo "ERROR: could not read the join token from $first" >&2; exit 1 ;;
esac

for server in "${servers[@]:1}"; do
    run_builder "$server" "K3S_URL=https://$first_address:6443 K3S_TOKEN=$token" "--registry $registry $builder_yes"
done

# ---------------------------------------------------------------------------
# This machine
# ---------------------------------------------------------------------------

echo ""
echo "=== this machine ==="

mkdir -p "$(dirname "$kubeconfig")"
ssh "${ssh_opts[@]}" "$first" 'cat /etc/rancher/k3s/k3s.yaml' \
    | sed "s#^\([[:space:]]*server:[[:space:]]*\).*#\1https://$first_address:6443#" > "$work/kubeconfig"
if ! grep -q "server: https://$first_address:6443" "$work/kubeconfig"; then
    echo "ERROR: the kubeconfig from $first names no server" >&2
    exit 1
fi
install -m 600 "$work/kubeconfig" "$kubeconfig"
echo "  wrote $kubeconfig"

if [ ! -f "$config_mk" ]; then
    cp "$checkout/config.mk.example" "$config_mk"
fi
set_setting() {
    awk -v key="$1" -v value="$2" '
        $0 ~ "^" key "[[:space:]]*[:?]?=" { if (!done) print key " ?= " value; done = 1; next }
        { print }
        END { if (!done) print key " ?= " value }
    ' "$config_mk" > "$work/config.mk"
    cp "$work/config.mk" "$config_mk"
}
set_setting KUBECONFIG "$kubeconfig"
set_setting REGISTRY_HOST "$registry"
echo "  set KUBECONFIG and REGISTRY_HOST in $config_mk"

if [ "$daemon_change" -eq 1 ]; then
    jq --arg r "$registry" '.["insecure-registries"] = ((.["insecure-registries"] // []) + [$r])' \
        <<<"$daemon_current" > "$work/daemon.json"
    if ! sudo -n true 2>/dev/null; then
        input_notice "INPUT NEEDED  |  sudo password on this machine" \
            "Adding the registry to Docker's settings ($daemon_json)" \
            "runs sudo here, and sudo asks for your password." \
            "Type it at the [sudo] prompt below: sudo gives up after its" \
            "passwd_timeout, 5 minutes by default, and the script stops."
    fi
    sudo install -m 644 "$work/daemon.json" "$daemon_json"
    sudo systemctl reload docker
    echo "  added $registry to insecure-registries in $daemon_json"
fi

# ---------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------

for server in "${servers[@]}"; do
    name="$(ssh "${ssh_opts[@]}" "$server" hostname)"
    if ! kubectl --kubeconfig "$kubeconfig" get node "$name" --no-headers 2>/dev/null | grep -q " Ready"; then
        echo "ERROR: $server ($name) is not a Ready node of the cluster at https://$first_address:6443" >&2
        exit 1
    fi
done
if ! curl -sf -o /dev/null "http://$registry/v2/"; then
    echo "ERROR: the registry at $registry does not answer from this machine" >&2
    exit 1
fi
for _ in $(seq 1 10); do
    if [ "$(docker info --format "{{with index .RegistryConfig.IndexConfigs \"$registry\"}}{{.Secure}}{{end}}" 2>/dev/null)" = "false" ]; then
        break
    fi
    sleep 1
done
if [ "$(docker info --format "{{with index .RegistryConfig.IndexConfigs \"$registry\"}}{{.Secure}}{{end}}" 2>/dev/null)" != "false" ]; then
    echo "ERROR: Docker does not list $registry as an insecure registry after the reload. A Docker" >&2
    echo "       that does not read $daemon_json (a snap or rootless install) needs the registry in" >&2
    echo "       its own daemon configuration: \"insecure-registries\": [\"$registry\"]." >&2
    exit 1
fi

echo ""
echo "=== Done ==="
echo "${#servers[@]} server(s) Ready; registry $registry. Next, in $checkout:"
echo "  make all"
