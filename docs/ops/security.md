# Security

NodalArc session pods are hardened to prevent escape from the emulated network environment. Users interacting via the terminal get a router CLI experience, not a general-purpose Linux shell.

## Pod Hardening

Every session pod (satellite and ground station) runs with the following security controls:

| Control | Implementation | Purpose |
|---------|---------------|---------|
| Read-only root filesystem | Pod securityContext | Prevents filesystem modification |
| tmpfs for writable paths | `/etc/frr`, `/var/run/frr`, `/tmp`, `/var/log` | FRR needs to write state files |
| No service account token | `automountServiceAccountToken: false` | Cannot access K8s API from inside pod |
| SSH key-only auth | sshd_config: `PasswordAuthentication no` | No password brute-force |
| Root login disabled | sshd_config: `PermitRootLogin no` | Users land as `operator` |
| vtysh login shell | `/usr/bin/vtysh` as operator's shell | No bash/ash access via SSH |
| Idle timeout | sshd_config: `ClientAliveInterval 600` | Stale sessions terminated |
| CNI egress blocked | iptables OUTPUT DROP on cni0 | Cannot reach K8s API or other services |
| cni0 renamed from eth0 | FRR entrypoint script | User namespace reserved for user interfaces |
| `terminal shell` disabled | FRR official 10.3.1 image | Cannot escape vtysh to underlying OS |

### What Users Can Do

- Run any vtysh command (`show`, `configure terminal`, `debug`, etc.)
- Ping and traceroute through the emulated network
- View and modify FRR routing configuration
- Inspect interface state, routing tables, MPLS labels
- SSH jump between nodes through the emulated ISL/ground network

### What Users Cannot Do

- Access the underlying Linux shell (vtysh is the login shell, `terminal shell` disabled)
- Reach the Kubernetes API or other platform services (iptables blocks cni0 egress)
- Modify the filesystem outside of FRR's tmpfs mounts
- Access other pods' network namespaces
- Use the service account token (not mounted)

### SYS_ADMIN Capability

Session pods retain `CAP_SYS_ADMIN`. This is required by FRR's `ospfd` and `mgmtd` daemons for network namespace operations. It does not compromise the security boundary because:
- The root filesystem is read-only
- cni0 egress is blocked by iptables
- No service account token is available
- vtysh cannot execute shell commands

## Network Isolation

### cni0 (Infrastructure Interface)

Every pod has a `cni0` interface (renamed from eth0 at boot by the FRR entrypoint). This is the Kubernetes CNI interface that connects to the cluster network. It is visible in `show interface brief` but:

- iptables `OUTPUT DROP` on cni0 blocks all egress
- Exception: `ESTABLISHED,RELATED` allows return traffic for SSH sessions initiated from outside
- Users cannot use cni0 to reach the K8s API, NATS, or any platform service
- The name `cni0` signals "infrastructure, not yours" — `mgmt0` is reserved for user-created management interfaces

### Data Plane Interfaces

Only `isl0-3`, `gnd0`, `terr0`, and `lo` carry user-plane traffic. These are wired by the Node Agent and carry real routed traffic between pods. Traffic on these interfaces is genuine emulated satellite networking.

## SSH Key Lifecycle

1. **Generation** — Operator generates an ED25519 keypair when creating a session
2. **Storage** — keypair stored in K8s Secret `nodalarc-terminal-keys` with ConstellationSpec ownerReference
3. **Distribution** — public key mounted into session pods via volume mount, copied to operator's `~/.ssh/authorized_keys`
4. **Usage** — VS-API reads private key from the Secret for its SSH proxy (browser terminal). Direct SSH clients use the same key.
5. **Rotation** — new session = new keys. Each session deployment generates a fresh keypair.
6. **Cleanup** — ownerReference on the Secret causes automatic garbage collection when the ConstellationSpec CR is deleted (teardown)

No persistent SSH keys exist between sessions. No shared keys across deployments.

## rp_filter

Reverse path filtering is disabled on all session pods (`net.ipv4.conf.all.rp_filter=0`). This is required for IS-IS and OSPF multicast hellos to pass — without it, routing protocol hellos arriving on ISL interfaces fail the kernel's reverse-path check and are silently dropped.

This is set as a pod-level sysctl by the Operator in `session_deployer.py`. The Node Agent does not need to manage it.

## Recommendations for Production Deployments

- **Network policy:** Add Kubernetes NetworkPolicy to restrict pod-to-pod traffic to only the data plane interfaces. Block direct pod-to-pod communication via the CNI network.
- **RBAC:** Limit who can `kubectl exec` into session pods. The browser terminal goes through VS-API (which handles authentication); direct kubectl access should be restricted to operators.
- **Image scanning:** Run vulnerability scans on the FRR base image. The official FRRouting image is Alpine-based and minimal.
- **HTTPS:** Deploy an ingress controller with TLS termination for the VF and VS-API. Required for SharedArrayBuffer (Web Worker) support and recommended for any non-localhost deployment.
