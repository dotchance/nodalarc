#!/bin/bash
set -e

# Wait for the Operator to deliver FRR configs.
# The Operator execs into the pod to:
#   1. cp /etc/frr-config/* /etc/frr/
#   2. touch /etc/frr/.config-ready
READY_FILE="/etc/frr/.config-ready"
TIMEOUT=900
WAITED=0

echo "Waiting for config (sentinel: $READY_FILE)..."
while [ ! -f "$READY_FILE" ]; do
    sleep 1
    WAITED=$((WAITED + 1))
    if [ "$WAITED" -ge "$TIMEOUT" ]; then
        echo "ERROR: Config not delivered within ${TIMEOUT}s, exiting"
        exit 1
    fi
done
echo "Config ready after ${WAITED}s"

# Create vtysh.conf if it doesn't exist — suppresses the
# "Can't open configuration file /etc/frr/vtysh.conf" warning
# that appears on every vtysh invocation without it.
touch /etc/frr/vtysh.conf

# Ensure FRR owns config directory
chown -R frr:frr /etc/frr

# ---------------------------------------------------------------------------
# SSH terminal access setup (dropbear)
#
# Creates an 'operator' user with login shell = /usr/bin/vtysh.
# SSH sessions land directly in the FRR CLI — same experience as a real
# router. Key-only auth (no passwords), root login disabled.
#
# Public key is delivered via K8s Secret mount at /etc/ssh-keys/.
# Host keys are generated on first boot (tmpfs, regenerate on restart).
# ---------------------------------------------------------------------------

# Generate host keys if not present (tmpfs — regenerated each pod start)
if [ ! -f /etc/dropbear/dropbear_ed25519_host_key ]; then
    dropbearkey -t ed25519 -f /etc/dropbear/dropbear_ed25519_host_key >/dev/null 2>&1
    echo "SSH host key generated"
fi

# operator user created at image build time (Dockerfile) with:
#   login shell = /usr/bin/vtysh, group = frrvty (VTY socket access)
# Cannot create at runtime because /etc/passwd is on read-only root filesystem.
# Home dir is tmpfs (owned by root at mount) — must chown for dropbear auth.
chown operator:frrvty /home/operator
chmod 755 /home/operator

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

# Start dropbear SSH daemon in background.
#   -R: generate host keys if missing (belt and suspenders)
#   -s: disable password login (key-only authentication)
#   -g: disable root login
#   -p 22: listen on all interfaces, port 22
#   -K 60: send keepalive every 60 seconds
#   -I 600: disconnect idle sessions after 10 minutes
dropbear -R -s -g -p 22 -K 60 -I 600 2>/dev/null &
echo "SSH daemon started (key-only auth, root disabled, idle timeout 600s)"

# Rename eth0 → cni0 BEFORE FRR starts so zebra learns the correct name.
# cni0 is the K8s CNI infrastructure interface — not user-configurable.
# The name reserves mgmt0 for users to create their own management VRF.
if ip link show eth0 >/dev/null 2>&1; then
    ip link set eth0 name cni0
    echo "Renamed eth0 → cni0"
fi

# Hand off to FRR's stock docker-start (watchfrr reads /etc/frr/daemons)
exec /usr/lib/frr/docker-start
