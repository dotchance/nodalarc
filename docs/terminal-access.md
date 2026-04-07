# Terminal Access to Constellation Nodes

## Overview

Every satellite and ground station pod runs an SSH daemon (dropbear) with
key-only authentication. The login shell is `/usr/bin/vtysh` — you land
directly in the FRR CLI, same as SSHing to a real Cisco/Juniper/Arista router.

There are two ways to access a node's CLI:

1. **Browser** — open the CLI drawer, select Terminal mode, pick a node
2. **Direct SSH** — use any SSH client (PuTTY, iTerm, SecureCRT, etc.)

## Browser Access

1. Open the Visualization Frontend (http://localhost:3000)
2. Click the CLI drawer toggle (bottom of screen)
3. Select **Terminal** mode (left of the toolbar)
4. Select a node from the dropdown
5. An interactive vtysh session opens as a tab

**Multi-session tabs:** Each node you select opens a new persistent tab.
Sessions stay alive when you switch between tabs — output accumulates in
the background. Switch back and scroll through everything that happened
while you were away. This matches the standard network engineering workflow
of having multiple SSH sessions open simultaneously.

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
kubectl port-forward pod/sat-P00S00 2222:22 -n nodalarc &
ssh -i ~/.ssh/nodalarc -p 2222 -o StrictHostKeyChecking=no operator@localhost
```

### SSH jump between nodes

```bash
# SSH to one satellite, then jump to another through the constellation
ssh -i ~/.ssh/nodalarc -J operator@$SAT_A_IP operator@$SAT_B_IP
```

Port forwarding and SSH tunneling through ISL/gnd interfaces is supported
and intended — this is how network engineers work with real hardware.

## Config Export

Download a node's running FRR configuration:

```bash
curl -H "Authorization: Bearer $TOKEN" \
  http://localhost:8080/api/v1/nodes/sat-P00S00/config > sat-P00S00.conf
```

Or click the **⬇ Config** button in the CLI drawer toolbar.

## Interface Naming

Session pods have the following interfaces:

| Interface | Purpose | User-configurable |
|-----------|---------|-------------------|
| `isl0`-`isl3` | Inter-satellite links | Yes (FRR routing) |
| `gnd0` | Ground station link | Yes (FRR routing) |
| `lo` | Loopback | Yes (FRR routing) |
| `terr0` | Terrestrial prefix stub | Yes (FRR routing) |
| `cni0` | K8s CNI infrastructure | **No** — egress blocked by iptables |

`cni0` is the renamed K8s CNI interface (formerly eth0). It is visible in
`show interface brief` but cannot be used for data plane traffic — iptables
blocks all egress except return traffic for SSH sessions. The name `cni0`
reserves `mgmt0` for users to create their own management VRF and interface,
matching real router conventions.

## Security Model

| Control | Implementation | Verified |
|---------|---------------|----------|
| Authentication | SSH key-only (dropbear -s, no passwords) | Audit passed |
| Root login | Disabled (dropbear -g) | Audit passed |
| Login shell | `/usr/bin/vtysh` (not bash) | Audit passed |
| `terminal shell` | "Unknown command" (disabled in FRR 10.3.1) | Audit passed |
| Root filesystem | Read-only (tmpfs for writable paths) | Audit passed |
| CNI egress | iptables OUTPUT DROP on cni0 | Audit passed |
| K8s SA token | Not mounted (automountServiceAccountToken: false) | Audit passed |
| Idle timeout | 10 minutes (dropbear -I 600) | Configured |
| Capabilities | NET_ADMIN, NET_RAW, SYS_ADMIN (FRR requirement) | Documented |
| SCP/SFTP | Blocked (vtysh login shell, no scp subsystem) | Audit passed |
| Command injection | Blocked (vtysh parser rejects ;|`$ etc.) | Audit passed |
| SSH tunneling | Allowed (ISL/gnd use case), blocked on cni0 by iptables | Audit passed |

### Security audit results

A full penetration test was conducted covering:
- vtysh shell escape attempts (all blocked)
- SSH exec arbitrary commands (all passed to vtysh, rejected)
- SSH port forwarding/SOCKS proxy (tunnel establishes, cni0 egress blocked)
- Network access to K8s API, NATS, gateway (all blocked by iptables)
- File system write attempts (read-only root blocks all)
- Privilege escalation (su not suid, sudo not installed, operator has zero capabilities)
- K8s service account token (not mounted)
- nsenter (present as busybox applet, unreachable via vtysh)
- FRR command injection via special characters (vtysh parser blocks all)

### Known limitations

- `cni0` is visible in `show interface brief` with K8s CNI routes in the
  routing table. Routes are non-functional (iptables blocks egress) but
  visible. Management VRF isolation planned for future release.
- FRR requires SYS_ADMIN capability (ospfd/mgmtd call privs_init).
  Cannot be removed without breaking FRR. Mitigated by other hardening.
- bash exists in the container (FRR's docker-start requires it). Not
  accessible via SSH because login shell is vtysh with no shell escape.

## Key Lifecycle

- SSH keypair generated per session by the Operator at session creation
- Stored in K8s Secret `nodalarc-terminal-keys` with ConstellationSpec owner ref
- Public key mounted into session pods, copied to operator's authorized_keys
- Private key read by VS-API on first terminal connection (lazy, cached)
- Secret garbage-collected on session teardown (owner reference)
- Each new session deployment generates fresh keys
