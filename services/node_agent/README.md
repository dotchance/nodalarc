# Node Agent — DaemonSet

Runs on each K3s node with `hostPID: true` and privileged access.
Receives BatchLinkUp/Down commands from the Scheduler via NATS request/reply
and executes kernel operations via pyroute2.

## Operations

- Create/destroy veth pairs between pod network namespaces
- Apply tc netem (latency) and tc tbf (bandwidth) shaping
- Manage ground station bridge and tc mirred redirect attachments
- Enable MPLS forwarding on interfaces

## Key Rules

- **Never use `NetNS()`** — use `_in_namespace(pid, fn)` from `namespace_ops.py`
- **Wiring gate** — NATS server does not subscribe until pid_map is populated
- **Stateless** — diffs desired (ConfigMap) vs actual (kernel) on every startup
