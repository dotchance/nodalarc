#!/bin/bash
set -e

# Wait for ConfigMap volume mount to be populated by kubelet.
# The Operator creates a per-node ConfigMap (frr-config-<node-id>) mounted
# at /etc/frr-config/. kubelet populates the volume when the pod is
# scheduled — no exec or sentinel file needed.
CONFIG_SRC="/etc/frr-config/frr.conf"
TIMEOUT=120
WAITED=0

echo "Waiting for ConfigMap mount ($CONFIG_SRC)..."
while [ ! -f "$CONFIG_SRC" ]; do
    sleep 1
    WAITED=$((WAITED + 1))
    if [ "$WAITED" -ge "$TIMEOUT" ]; then
        echo "ERROR: ConfigMap not mounted within ${TIMEOUT}s, exiting"
        exit 1
    fi
done
echo "ConfigMap mounted after ${WAITED}s"

# Copy ConfigMap contents to writable /etc/frr/ (tmpfs emptyDir).
# ConfigMap mounts are read-only; FRR needs to write to /etc/frr/.
cp /etc/frr-config/* /etc/frr/
# Write config version sentinel for readiness probe.
# _config_version is a file in the ConfigMap containing a hash of frr.conf.
# The readiness probe diffs /etc/frr/.config_version against the ConfigMap
# mount to verify this container has loaded the intended config version.
if [ -f /etc/frr-config/_config_version ]; then
    cp /etc/frr-config/_config_version /etc/frr/.config_version
fi
echo "Copied config from /etc/frr-config/ to /etc/frr/"

# Background config watcher — detects ConfigMap updates and reloads FRR.
# K8s kubelet syncs ConfigMap volume mounts on a polling interval (~60s).
# When frr.conf changes (new session config, routing parameter update),
# the watcher copies the new files and tells FRR to reload gracefully.
# This is the NOS-agnostic contract: the container owns its own config
# lifecycle. The Operator just mounts the ConfigMap.
_watch_config() {
    local last_hash
    last_hash=$(md5sum /etc/frr-config/frr.conf 2>/dev/null | awk '{print $1}')
    while true; do
        sleep 10
        local current_hash
        current_hash=$(md5sum /etc/frr-config/frr.conf 2>/dev/null | awk '{print $1}')
        if [ -n "$current_hash" ] && [ "$current_hash" != "$last_hash" ]; then
            echo "ConfigMap change detected, reloading FRR config"
            cp /etc/frr-config/* /etc/frr/
            chown -R frr:frr /etc/frr
            # Declarative reload — frr-reload.py diffs the running config
            # against the intended file and applies additions AND removals.
            # (vtysh -f only sources commands additively: config removed in
            # the new version would silently survive in the daemons.)
            if python3 /usr/lib/frr/frr-reload.py --reload /etc/frr/frr.conf; then
                # The sentinel is a claim that the running NOS has converged
                # to this config version. It moves only on reload success;
                # on failure the readiness probe must report the pod stale.
                if [ -f /etc/frr-config/_config_version ]; then
                    cp /etc/frr-config/_config_version /etc/frr/.config_version
                fi
                echo "FRR config reloaded"
                last_hash="$current_hash"
            else
                # last_hash intentionally not advanced: the watcher keeps
                # reconciling toward the intended config on every poll.
                echo "ERROR: FRR declarative reload failed; running config is stale and readiness will report it"
            fi
        fi
    done
}
_watch_config &

# Create vtysh.conf if it doesn't exist — suppresses the
# "Can't open configuration file /etc/frr/vtysh.conf" warning
# that appears on every vtysh invocation without it.
touch /etc/frr/vtysh.conf

# Ensure FRR owns config directory
chown -R frr:frr /etc/frr

# ---------------------------------------------------------------------------
# SSH terminal access (OpenSSH sshd)
#
# sshd_config written to tmpfs (/etc/ssh emptyDir mount).
# Host keys generated on first boot (tmpfs, regenerated each pod start).
# operator user created at image build time (Dockerfile) with:
#   login shell = /usr/bin/vtysh, group = frrvty (VTY socket access)
# Home dir is tmpfs (owned by root at mount) — must chown for sshd auth.
# ---------------------------------------------------------------------------

# Fix home directory ownership (tmpfs mount is root-owned at creation)
chown operator:frrvty /home/operator
chmod 755 /home/operator

# Generate host keys if not present (tmpfs — regenerated each pod start)
if [ ! -f /etc/ssh/ssh_host_ed25519_key ]; then
    ssh-keygen -t ed25519 -f /etc/ssh/ssh_host_ed25519_key -N "" -q
    echo "SSH host key generated"
fi

# Write sshd_config to tmpfs
cat > /etc/ssh/sshd_config << 'SSHD_CONFIG'
Port 22
HostKey /etc/ssh/ssh_host_ed25519_key
AuthorizedKeysFile .ssh/authorized_keys
PasswordAuthentication no
PermitRootLogin no
UseDNS no
ClientAliveInterval 60
ClientAliveCountMax 10
PrintMotd yes
AcceptEnv LANG LC_*
SSHD_CONFIG

# Install authorized keys from Secret mount (if present).
# The Operator generates a per-session SSH keypair and stores the public
# key in the nodalarc-terminal-keys Secret, mounted at /etc/ssh-keys/.
if [ -f /etc/ssh-keys/authorized_keys ]; then
    mkdir -p /home/operator/.ssh
    cp /etc/ssh-keys/authorized_keys /home/operator/.ssh/authorized_keys
    chown -R operator:frrvty /home/operator/.ssh
    chmod 700 /home/operator/.ssh
    chmod 600 /home/operator/.ssh/authorized_keys
    echo "SSH authorized key installed for operator"
else
    echo "WARNING: No SSH authorized keys found at /etc/ssh-keys/ — terminal access disabled"
fi

# Start sshd in background
/usr/sbin/sshd -e 2>/dev/null &
echo "SSH daemon started (OpenSSH, key-only auth, root disabled, UseDNS no)"

# Rename eth0 → cni0 BEFORE FRR starts so zebra learns the correct name.
# cni0 is the K8s CNI infrastructure interface — not user-configurable.
# The name reserves mgmt0 for users to create their own management VRF.
if ip link show eth0 >/dev/null 2>&1; then
    ip link set eth0 name cni0
    echo "Renamed eth0 → cni0"
fi

# Ensure BFD daemon is enabled. The Dockerfile COPYs daemons with bfdd=yes,
# but stale image caches may serve the base image's bfdd=no. This sed is
# idempotent and guarantees bfdd runs regardless of which image layer wins.
# The daemon is idle (zero overhead) when no protocol config requests BFD.
sed -i 's/^bfdd=no/bfdd=yes/' /etc/frr/daemons

# Hand off to FRR's stock docker-start (watchfrr reads /etc/frr/daemons)
exec /usr/lib/frr/docker-start
