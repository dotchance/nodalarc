# Node Agent - DaemonSet

**Location:** `services/node_agent/`
**Deployment:** DaemonSet (one per labeled K8s node)
**Entry point:** `services/node_agent/__main__.py`
**Privileges:** `hostPID: true`, `hostNetwork: true`, `privileged: true`

## Responsibility

The Node Agent is the only component that touches the Linux kernel's network stack. It receives fenced commands from the Scheduler over NATS request/reply and executes them using pyroute2. It must not report success unless the requested kernel state has been verified.

## Startup Sequence

1. Read topology wiring manifest from ConfigMap
2. Discover all session pod PIDs on this node
3. Wire base infrastructure for every pod:
   - Create host-mediated veth pairs for ISL interfaces
   - Create generated terminal interfaces such as `islX`, `gndX`, and `terr0`
   - Set sysctls (forwarding, rp_filter, MPLS input)
   - Remove default K8s route
4. Write typed `nodalarc-wiring-status` with session ID, wiring generation,
   per-phase results, and dirty-kernel state
5. Subscribe to NATS for Scheduler requests

**Critical:** Step 5 must happen AFTER the current manifest is validated and the matching wiring status is ready. The Scheduler must not dispatch to a Node Agent that has not completed wiring for the same `session_id` and `wiring_generation`.

## Command Contract

Node Agent protobufs are NATS payloads only. There is no Node Agent gRPC service, hostPort, or `--port` control path.

Every command includes a `CommandEnvelope`:

- `operation_id`
- `session_id`
- `wiring_generation`
- `operation_kind`

The envelope and all required fields are validated before any kernel mutation. Stale sessions, stale generations, malformed frames, missing PIDs, missing peer identity, missing `HOST_IP`, and unspecified enum zero values fail closed. There is no compatibility shim for old unfenced commands.

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

**Never use `pyroute2.NetNS()`** - it forks a child process that inherits signal handlers and socket fds. Causes orphaned processes and unreliable restart behavior.

## Link Operations

### BatchLinkUp (LOCAL)

1. Bring host-side veth admin UP → carrier arrives on pod-side interface
2. Apply tc netem (latency) and tc tbf (bandwidth) on pod-side interface
3. For ground links: attach to ground bridge via tc mirred redirect

### BatchLinkUp (CROSS_NODE)

ISL and ground links use different CROSS_NODE paths:

- **ISL:** create a VXLAN interface, create a host/pod veth pair, redirect VXLAN
  to the veth host end with tc mirred, move the pod end into the target
  namespace, bring it UP, then apply shaping.
- **Ground:** attach a VXLAN interface to the already-wired local host-side
  ground interface with tc mirred, apply tc tbf/netem on the local terminal
  endpoint, and verify VXLAN, mirred, and qdisc state before ACK.

### BatchLinkDown

1. Bring host-side veth admin DOWN → carrier drops on pod-side
2. For VXLAN links: remove VXLAN interface and host-side veth
3. For ground links: remove tc mirred redirect rules and bring the satellite
   pod-side `gndX` DOWN; the ground-node pod interface remains
   carrier-driven by the host-side veth

### SetLatency

Update tc netem delay value on an existing interface:
```python
tc qdisc change dev isl0 root netem delay {latency_ms}ms
```

The response contains one `LatencyResult` per requested entry. Aggregate
success is true only when every entry succeeded and was verified.

## Proof And Dirty Kernel

Batch operations are not advertised as atomic Linux transactions. The honest contract is:

1. validate the whole request before mutation
2. execute entries in deterministic order
3. return one result per requested entry
4. set aggregate success only when every entry succeeded
5. set `dirty_kernel=true` when cleanup, rollback, or proof fails

MVP proof verifies the postcondition that would make an ACK false if absent:
pod interface existence, host state, VXLAN identity, mirred redirects, tbf/netem
qdisc delay/rate, and cleanup after LinkDown. `SetLatency` runs through the
operation plan executor and fails if qdisc proof does not match the requested
delay.

## Ground Station Bridge

Ground stations use a host-side bridge with tc mirred redirect. During a handoff:

1. Remove mirred rules from old satellite's host-side veth
2. Bring old satellite's pod-side `gndX` and host-side veth DOWN (carrier
   drops on the GS terminal interface)
3. Bring new satellite's host-side veth and pod-side `gndX` UP (carrier
   arrives on the GS terminal interface)
4. Add mirred rules for new satellite

FRR detects carrier loss immediately (no hold timer), tears adjacency, forms new one.

## Substrate Monitoring

The `substrate_monitor` measures physical latency for the manifest-required
Kubernetes-node pairs owned by this Node Agent:

- Required pairs come from `required_substrate_pairs` in the typed wiring
  manifest.
- `HOST_IP` must match the manifest source IP before measurement starts.
- Median RTT is measured with ICMP samples on the K8s management network.
- Results are written to `nodalarc-substrate-status-<source-node>` ConfigMaps.
- The status document is scoped by `session_id` and `wiring_generation` and
  includes sample counts, min/median/max RTT, status, and `stale_after`.
- Failed measurements are written as evidence and then fail startup or refresh.

Exact VXLAN peer refs remain as lifecycle diagnostics only. They do not trigger
measurement, publish dispatch inputs, or act as substrate truth.

## Ops Evidence

The Node Agent publishes structured OpsEvents for startup dependency failures,
manifest validation failures, command rejections, command failures, proof
failures, and dirty-kernel state. Failures that happen before NATS is available
are written to `/var/lib/nodalarc/node-agent/ops-events.jsonl` and drained after
the first NATS connection.

## Statelessness

The Node Agent is stateless across restarts. On startup, it diffs desired state (from wiring manifest) against actual kernel state:

- No kernel state → wire from scratch (Case A)
- Kernel matches desired → no-op (Case B)
- Kernel mismatched → cleanup then wire from scratch (Case C)

## Key Files

| File | Content |
|------|---------|
| `__main__.py` | Entry point, NATS subscription, startup gate |
| `handlers.py` | BatchLinkUp/Down/SetLatency/KernelInventory request handlers |
| `command_contract.py` | Envelope, enum, identity, and required-field validation |
| `manifest_contract.py` / `wiring_status.py` | Typed wiring manifest and readiness status |
| `operation_plan.py` / `operation_executor.py` | Deterministic planned execution and rollback evidence |
| `kernel_verifier.py` | MVP kernel proof helpers, including read-only KernelInventory proof |
| `namespace_ops.py` | `setns()`-based namespace entry, `_HOST_NS_FD` |
| `vxlan.py` | VXLAN tunnel creation/destruction |
| `wiring.py` | Initial pod interface wiring from manifest |
| `substrate_monitor.py` | Manifest-required inter-node substrate measurement |
