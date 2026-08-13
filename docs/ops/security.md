# Security

NodalArc session pods are hardened to prevent escape from the emulated network environment. On a router node the terminal is a router CLI, not a general-purpose Linux shell. On a host node the terminal is deliberately that workload's own shell, and the isolation comes from the substrate controls, not from hiding the shell.

## Pod Hardening

Every session pod, whatever workload it runs, gets the platform controls:

| Control | Implementation | Purpose |
|---------|---------------|---------|
| Read-only root filesystem | Profile `root_filesystem` (default `read_only`) | Prevents filesystem modification |
| No service account token | `automountServiceAccountToken: false` | Cannot access K8s API from inside pod |
| CNI egress blocked | iptables OUTPUT DROP on cni0 | Cannot reach K8s API or other services |
| cni0 renamed from eth0 | Node Agent wiring, before the workload starts | User namespace reserved for user interfaces |
| Capabilities are profile-declared | Workload profile grammar, closed vocabulary | A host profile can and usually does run with none |

The shipped FRR router profile adds the router-terminal posture on top:

| Control | Implementation | Purpose |
|---------|---------------|---------|
| tmpfs for writable paths | `/etc/frr`, `/var/run/frr`, `/tmp`, `/var/log` | FRR needs to write state files |
| SSH key-only auth | sshd_config: `PasswordAuthentication no` | No password brute-force |
| Root login disabled | sshd_config: `PermitRootLogin no` | Users land as `operator` |
| vtysh login shell | `/usr/bin/vtysh` as operator's shell | No bash/ash access via SSH |
| Idle timeout | sshd_config: `ClientAliveInterval 600` | Stale sessions terminated |
| `terminal shell` disabled | FRR official 10.3.1 image | Cannot escape vtysh to underlying OS |

### What Users Can Do

- On a router: run any vtysh command (`show`, `configure terminal`, `debug`, etc.), view and modify FRR routing configuration, inspect interface state, routing tables, MPLS labels
- On a host: use that workload's own shell and tools inside its container
- Ping through the emulated network
- SSH jump between router nodes through the emulated ISL/ground network

### What Users Cannot Do

- Escape a router terminal to the underlying OS (vtysh is the login shell, `terminal shell` disabled)
- Reach the Kubernetes API or other platform services from any node (iptables blocks cni0 egress)
- Modify the filesystem outside the profile's declared writable mounts
- Access other pods' network namespaces
- Use the service account token (not mounted)

### SYS_ADMIN Capability

The FRR router profile retains `CAP_SYS_ADMIN`. This is required by FRR's `ospfd` and `mgmtd` daemons for network namespace operations. Host profiles declare no capabilities at all. It does not compromise the security boundary because:
- The root filesystem is read-only
- cni0 egress is blocked by iptables
- No service account token is available
- vtysh cannot execute shell commands

## Network Isolation

### cni0 (Infrastructure Interface)

Every session pod has a `cni0` interface (renamed from eth0 by the Node Agent during wiring, before the workload starts). This is the Kubernetes CNI interface that connects to the cluster network. It is visible in `show interface brief` but:

- iptables `OUTPUT DROP` on cni0 blocks all egress
- Exception: `ESTABLISHED,RELATED` allows return traffic for SSH sessions initiated from outside
- Users cannot use cni0 to reach the K8s API, NATS, or any platform service
- The name `cni0` signals "infrastructure, not yours" - `mgmt0` is reserved for user-created management interfaces

### Data Plane Interfaces

Only the emulated interfaces carry user-plane traffic: WAN interfaces such
as `islX` and `gndX`, Ethernet segment ports such as `terr0` or `bus0`, and
`lo`. These are wired by the Node Agent and carry real routed traffic
between pods. Traffic on these interfaces is genuine emulated
satellite networking.

## SSH Key Lifecycle

1. **Generation** - Operator generates an ED25519 keypair when creating a session
2. **Storage** - keypair stored in K8s Secret `nodalarc-terminal-keys` with ConstellationSpec ownerReference
3. **Distribution** - public key mounted into session pods via volume mount, copied to operator's `~/.ssh/authorized_keys`
4. **Usage** - VS-API reads private key from the Secret for its SSH proxy (browser terminal). Direct SSH clients use the same key.
5. **Rotation** - new session = new keys. Each session deployment generates a fresh keypair.
6. **Cleanup** - ownerReference on the Secret causes automatic garbage collection when the ConstellationSpec CR is deleted (teardown)

No persistent SSH keys exist between sessions. No shared keys across deployments.

## rp_filter

Reverse path filtering is disabled on all session pods (`net.ipv4.conf.all.rp_filter=0`). This is required for IS-IS and OSPF multicast hellos to pass - without it, routing protocol hellos arriving on ISL interfaces fail the kernel's reverse-path check and are silently dropped.

This is set as a pod-level sysctl by the Operator in `session_deployer.py`. The Node Agent does not need to manage it.

## Recommendations for Production Deployments

- **Network policy:** Add Kubernetes NetworkPolicy to restrict pod-to-pod traffic to only the data plane interfaces. Block direct pod-to-pod communication via the CNI network.
- **RBAC:** Limit who can `kubectl exec` into session pods. The browser terminal goes through VS-API (which handles authentication); direct kubectl access should be restricted to operators.
- **Image scanning:** Run vulnerability scans on the images your sessions pin. The shipped profiles use the official FRRouting image (Alpine-based and minimal) and upstream vendor images referenced by digest.
- **HTTPS:** Deploy an ingress controller with TLS termination for the VF and VS-API. Required for SharedArrayBuffer (Web Worker) support and recommended for any non-localhost deployment.
