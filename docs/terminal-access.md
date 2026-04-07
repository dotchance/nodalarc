# Terminal Access to Constellation Nodes

## Overview

Every satellite and ground station pod runs an SSH daemon (dropbear) with
key-only authentication. The login shell is `/usr/bin/vtysh` — you land
directly in the FRR CLI, same as SSHing to a real Cisco/Juniper/Arista router.

There are two ways to access a node's CLI:

1. **Browser** — open the CLI drawer, select Terminal mode, pick a node
2. **Direct SSH** — use any SSH client (PuTTY, iTerm, etc.)

## Browser Access

1. Open the Visualization Frontend (http://localhost:3000)
2. Click the CLI drawer toggle (bottom of screen)
3. Select **Terminal** mode (left of the toolbar)
4. Select a node from the dropdown
5. An interactive vtysh session opens automatically

You can run any vtysh command: `show ip route`, `configure terminal`,
`write memory`, etc. Tab completion works.

## Direct SSH Access

### From the K8s host

```bash
# Get the SSH private key from the K8s Secret
kubectl get secret nodalarc-terminal-keys -n nodalarc \
  -o jsonpath='{.data.id_ed25519}' | base64 -d > ~/.ssh/nodalarc
chmod 600 ~/.ssh/nodalarc

# Get the pod IP
POD_IP=$(kubectl get pod sat-P00S00 -n nodalarc -o jsonpath='{.status.podIP}')

# SSH in
ssh -i ~/.ssh/nodalarc -o StrictHostKeyChecking=no operator@$POD_IP
```

### Via kubectl port-forward

```bash
# Forward local port 2222 to the pod's SSH port 22
kubectl port-forward pod/sat-P00S00 2222:22 -n nodalarc &

# SSH via the forwarded port
ssh -i ~/.ssh/nodalarc -p 2222 -o StrictHostKeyChecking=no operator@localhost
```

### Via NodePort (if configured)

Expose specific nodes' SSH ports via K8s NodePort services for remote access.
Not configured by default — requires manual Service creation.

## Config Export

Download a node's running FRR configuration:

```bash
# Via REST API
curl -H "Authorization: Bearer $TOKEN" \
  http://localhost:8080/api/v1/nodes/sat-P00S00/config > sat-P00S00.conf
```

Or click the **⬇ Config** button in the CLI drawer toolbar.

## Security Model

| Control | Status |
|---------|--------|
| Authentication | SSH key-only (no passwords) |
| Root login | Disabled (dropbear -g) |
| Login shell | `/usr/bin/vtysh` (not bash) |
| `terminal shell` | "Unknown command" (disabled in FRR 10.3.1) |
| Root filesystem | Read-only (writable tmpfs for /etc/frr, /var/run/frr) |
| Idle timeout | 10 minutes (dropbear -I 600) |
| Capabilities | NET_ADMIN, NET_RAW, SYS_ADMIN (FRR requirement) |

### Known limitation

vtysh can see and configure the eth0 management interface. A user could
add static routes through eth0 to reach the K8s management network. This
is the same as on real network equipment where the management interface is
visible from the CLI. The proper fix (management VRF) is planned for a
future release.

## Key Lifecycle

- SSH keypair is generated per session by the Operator
- Public key stored in K8s Secret `nodalarc-terminal-keys`
- Secret has owner reference to ConstellationSpec — deleted on teardown
- Each new session deployment generates fresh keys
- The VS-API reads the private key lazily on first terminal connection
