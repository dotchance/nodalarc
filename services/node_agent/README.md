# Node Agent - DaemonSet

Runs on each K3s node with `hostPID: true` and privileged access.
Receives BatchLinkUp/Down commands from the Scheduler via NATS request/reply
and executes kernel operations via pyroute2.

There is no Node Agent gRPC control plane. The protobuf file defines NATS
payload messages only.

## Operations

- Create host-mediated veth pairs with tc mirred redirect (carrier-gated model: pod-side always admin UP, host-side admin state controls carrier)
- Shape each pod interface from its own terminal: HTB transmit rate plus netem
  delay on the pod interface, HTB receive rate on the host-side veth that feeds it
- Apply and verify cross-node ground shaping on the local terminal endpoint
- Manage ground station bridge and tc mirred redirect attachments
- Enable MPLS forwarding on interfaces
- Publish generation-scoped substrate latency measurements and OpsEvents

## Key Rules

- **Never use `NetNS()`** - use `_in_namespace(pid, fn)` from `namespace_ops.py`
- **Command fence** - every request must carry operation, session, and wiring generation
- **Wiring gate** - NATS server does not subscribe until typed wiring status is ready
- **No false green** - success requires per-entry kernel proof; dirty kernel stops dispatch
- **Stateless** - diffs desired (ConfigMap) vs actual (kernel) on every startup
