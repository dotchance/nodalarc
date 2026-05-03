# Node Agent — DaemonSet

**Location:** `services/node_agent/`
**Deployment:** DaemonSet (one per labeled K8s node)
**Entry point:** `services/node_agent/main.py`
**Privileges:** `hostPID: true`, `hostNetwork: true`, `privileged: true`

## Responsibility

The Node Agent is the only component that touches the Linux kernel's network stack. It receives commands from the Scheduler and executes them using pyroute2 (pure-Python netlink).

## Startup Sequence

1. Read topology wiring manifest from ConfigMap
2. Discover all session pod PIDs on this node
3. Wire base infrastructure for every pod:
   - Create host-mediated veth pairs for ISL interfaces
   - Create `gnd0` interface
   - Set sysctls (forwarding, rp_filter, MPLS input)
   - Remove default K8s route
4. Signal wiring complete (write `nodalarc-wiring-status` ConfigMap)
5. Subscribe to NATS for Scheduler requests

**Critical:** Step 5 must happen AFTER step 4. The Scheduler must not dispatch to a Node Agent that hasn't finished wiring.

## Namespace Operations

All kernel operations enter pod network namespaces via `setns()` syscall:

```python
# namespace_ops.py
def _in_namespace(pid: int, fn: Callable):
    """Enter namespace, execute fn, return to host."""
    ns_fd = open(f"/proc/{pid}/ns/net", "r")
    _HOST_NS_FD  # captured once at module load
    setns(ns_fd.fileno(), CLONE_NEWNET)
    try:
        result = fn()
    finally:
        setns(_HOST_NS_FD, CLONE_NEWNET)
    return result
```

**Never use `pyroute2.NetNS()`** — it forks a child process that inherits signal handlers and socket fds. Causes orphaned processes, port conflicts on restart.

## Link Operations

### BatchLinkUp (LOCAL)

1. Bring host-side veth admin UP → carrier arrives on pod-side interface
2. Apply tc netem (latency) and tc tbf (bandwidth) on pod-side interface
3. For ground links: attach to ground bridge via tc mirred redirect

### BatchLinkUp (CROSS_NODE)

1. Create VXLAN interface in host namespace (deterministic VNI from endpoint IDs)
2. Create veth pair (host-side + pod-side)
3. Add tc mirred redirect between VXLAN and veth host-end
4. Move veth pod-end into target pod namespace, rename to interface name
5. Bring interfaces UP, apply shaping

### BatchLinkDown

1. Bring host-side veth admin DOWN → carrier drops on pod-side
2. For VXLAN links: remove VXLAN interface and host-side veth
3. For ground links: remove tc mirred redirect rules

### SetLatency

Update tc netem delay value on an existing interface:
```python
tc qdisc change dev isl0 root netem delay {latency_ms}ms
```

## Ground Station Bridge

Ground stations use a host-side bridge with tc mirred redirect. During a handoff:

1. Remove mirred rules from old satellite's host-side veth
2. Bring old satellite's host-side veth DOWN (carrier drops on gnd0)
3. Bring new satellite's host-side veth UP (carrier arrives on gnd0)
4. Add mirred rules for new satellite

FRR detects carrier loss immediately (no hold timer), tears adjacency, forms new one.

## Substrate Monitoring

The `substrate_monitor` measures physical latency between this node and each VXLAN peer:
- First measurement on first VXLAN tunnel to each new peer
- Refreshed every 60 seconds
- Median of 10 ICMP samples on the K8s management network
- Published to `SUBJECT_SUBSTRATE_LATENCY` on NATS

## Statelessness

The Node Agent is stateless across restarts. On startup, it diffs desired state (from wiring manifest) against actual kernel state:

- No kernel state → wire from scratch (Case A)
- Kernel matches desired → no-op (Case B)
- Kernel mismatched → cleanup then wire from scratch (Case C)

## Key Files

| File | Content |
|------|---------|
| `main.py` | Entry point, NATS subscription, startup gate |
| `handlers.py` | BatchLinkUp/Down/SetLatency request handlers |
| `namespace_ops.py` | `setns()`-based namespace entry, `_HOST_NS_FD` |
| `vxlan.py` | VXLAN tunnel creation/destruction |
| `wiring.py` | Initial pod interface wiring from manifest |
| `substrate_monitor.py` | Inter-node latency measurement |
