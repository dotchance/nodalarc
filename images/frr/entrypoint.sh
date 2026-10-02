#!/bin/bash
set -e

# Wait for ConfigMap volume mount to be populated by kubelet.
# The Operator mounts the node's workload artifact ConfigMap at
# /etc/frr-config/: the FRR adapter's frr.conf and daemons.
# kubelet populates the volume when the pod is scheduled — no exec or
# sentinel file needed.
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

# The adapter's daemons file is the only daemon selection. /etc/frr is an
# emptyDir, so without it watchfrr would start no daemons.
if [ ! -f /etc/frr-config/daemons ]; then
    echo "ERROR: /etc/frr-config/daemons is missing; the FRR adapter selects the daemons, exiting"
    exit 1
fi

# Copy ConfigMap contents to writable /etc/frr/ (tmpfs emptyDir).
# ConfigMap mounts are read-only; FRR needs to write to /etc/frr/.
# Everything but the integrated configuration is in place before FRR starts;
# frr.conf follows once mgmtd serves every daemon that takes its configuration
# (see apply_boot_configuration below).
for source in /etc/frr-config/*; do
    [ "$(basename "$source")" = "frr.conf" ] || cp "$source" /etc/frr/
done
echo "Copied config from /etc/frr-config/ to /etc/frr/ (frr.conf at boot)"

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

# Write sshd_config to tmpfs. sshd listens in two places, over IPv4 and IPv6:
# - the default VRF, for SSH to the router's addresses over the emulated
#   network, as on a real router;
# - the management VRF (rdomain), for terminal connections, which arrive on
#   cni0: the Node Agent moved cni0 into that VRF before this container started.
# The pod keeps tcp_l3mdev_accept at 0, so each listener accepts only
# connections arriving in its own VRF, and nothing in the default VRF accepts
# connections that arrive through the VRFs the user creates on the router.
cat > /etc/ssh/sshd_config << 'SSHD_CONFIG'
Port 22
ListenAddress 0.0.0.0
ListenAddress ::
ListenAddress 0.0.0.0 rdomain nodalarc-mgmt
ListenAddress :: rdomain nodalarc-mgmt
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

# Start sshd in background. -D keeps it from daemonizing, which would point
# its stderr at /dev/null before it listens; its log (including a failure to
# listen in the management VRF) goes to the container log.
/usr/sbin/sshd -D -e &
echo "SSH daemon started in the default and management VRFs (OpenSSH, key-only auth, root disabled, UseDNS no)"

# The pod mounts an empty volume over /var/log, which hides the image's
# /var/log/frr. The rendered configuration logs to /var/log/frr/frr.log; without
# the directory every daemon's configuration load reports a failed line.
mkdir -p /var/log/frr
chown frr:frr /var/log/frr

# The boot configuration is applied once every selected daemon is up and mgmtd
# serves every selected daemon that takes its configuration through it. FRR's
# own start applies frr.conf as soon as the daemons are launched; applied
# before zebra and staticd had connected to mgmtd, the interface addresses
# were never installed on 2 to 14 routers per session start, and a later
# apply installed them. A boot configuration that cannot be applied ends the
# container: the session sees a router that is not running, and the kubelet
# restarts it. The readiness probe accepts the container only after the marker
# below exists. The marker lives on a volume that outlives a container
# restart, so a restarted container removes it first.
BOOT_WAIT_S=120
# The daemons whose configuration FRR 10.7.1's mgmtd takes and passes on.
MGMTD_BACKENDS="zebra ripd ripngd staticd"
rm -f /var/run/frr/nodalarc-boot-config-applied

# Prints what the boot configuration still waits for; prints nothing once
# every daemon the daemons file ($1) selects is up and every selected mgmtd
# backend is connected.
boot_configuration_waits_for() {
    local selected="" daemon state watchfrr backends
    while IFS='=' read -r daemon state; do
        if [ "$state" = "yes" ]; then
            selected="$selected $daemon"
        fi
    done < "$1"
    watchfrr="$(vtysh -c 'show watchfrr' 2>/dev/null || true)"
    backends="$(vtysh -c 'show mgmt backend-adapter all' 2>/dev/null || true)"
    for daemon in $selected; do
        printf '%s\n' "$watchfrr" | grep -Eq "^[[:space:]]+$daemon[[:space:]]+Up\$" \
            || printf ' %s(down)' "$daemon"
        case " $MGMTD_BACKENDS " in
            *" $daemon "*)
                printf '%s\n' "$backends" | grep -Eq "Client:[[:space:]]+$daemon\$" \
                    || printf ' %s(no mgmtd backend)' "$daemon"
                ;;
        esac
    done
}

# Runs in the background; $$ is the entrypoint's process, which becomes FRR's
# docker-start. Ending it ends the container.
apply_boot_configuration() {
    local deadline waiting
    deadline=$(( $(date +%s) + BOOT_WAIT_S ))
    while waiting="$(boot_configuration_waits_for /etc/frr/daemons)"; [ -n "$waiting" ]; do
        if [ "$(date +%s)" -ge "$deadline" ]; then
            echo "ERROR: frr.conf not applied; after ${BOOT_WAIT_S}s still waiting for:$waiting; ending the container" >&2
            kill -TERM "$$"
            return 1
        fi
        sleep 0.2
    done
    if ! cp /etc/frr-config/frr.conf /etc/frr/frr.conf \
        || ! chown frr:frr /etc/frr/frr.conf \
        || ! vtysh -b \
        || ! touch /var/run/frr/nodalarc-boot-config-applied; then
        echo "ERROR: frr.conf did not apply cleanly; ending the container" >&2
        kill -TERM "$$"
        return 1
    fi
    echo "frr.conf applied with every selected daemon up and connected to mgmtd"
}
apply_boot_configuration &

# Hand off to FRR's stock docker-start (watchfrr reads /etc/frr/daemons)
exec /usr/lib/frr/docker-start
