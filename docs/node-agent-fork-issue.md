# Node Agent Fork Issue - Root Cause Analysis

> **STATUS: RESOLVED.** The fix implemented was Option C (`setns()` syscall).
> The codebase now uses `_in_namespace()` with the `setns()` syscall in
> `services/node_agent/namespace_ops.py`. All `NetNS()` fork calls have been
> removed from the Node Agent. The host namespace file descriptor is captured
> once at module load as `_HOST_NS_FD`. No fork, no signal inheritance, no
> fd leakage. The remainder of this document is preserved as historical root
> cause analysis.

**Date:** 2026-03-31
**Symptom:** Session deploys from the wizard show green ISL links in the VF but zero ISIS neighbors. FRR has no ISL interfaces inside the pod. The Node Agent never wired them.

---

## Resolution

Option C was implemented: every `NetNS()` call was replaced with `_in_namespace(pid, fn)` in `services/node_agent/namespace_ops.py`. This function uses the `setns()` syscall to enter the target network namespace in the current thread, execute the operation, and return to the host namespace. No child process is forked, so there is no signal handler inheritance, no orphaned children holding ports, and no file descriptor leakage.

Key implementation details:
- `_HOST_NS_FD` is opened once at module load from `/proc/1/ns/net` (the host network namespace under `hostPID: true`)
- `setns()` is called via `ctypes.CDLL('libc.so.6')`, a single syscall with no process creation overhead
- All callers in `namespace_ops.py`, `wiring.py`, `ground_bridge.py`, and `handlers.py` were migrated
- The `NetNS()` constructor is no longer used anywhere in the Node Agent

---

## Root Cause (Historical)

pyroute2's `NetNS()` constructor forks a child process via `os.fork()` to create a netlink socket inside the target network namespace. The child inherits the parent process's signal handlers. When the parent Node Agent process receives SIGTERM (from K8s pod lifecycle or the Operator restarting platform pods), the forked child also catches SIGTERM via the inherited handler. This creates two problems:

1. **The child process holds port 50100.** The parent's ZMQ ROUTER socket is bound to port 50100 before the fork. After fork, both parent and child have a copy of the socket file descriptor. When the parent exits, the child keeps the fd open. The next Node Agent restart cannot bind port 50100 because the orphaned child still holds it.

2. **Repeated SIGTERMs prevent wiring.** The Node Agent's SIGTERM handler sets `_running = False` and stops the ZMQ server. The wiring watcher thread (daemon) never gets to execute because the main thread exits before the watcher completes its first ConfigMap check cycle. On restart, the new process starts PID discovery, but before it completes, another SIGTERM arrives (from the orphaned child or concurrent pod lifecycle operations), killing it again.

### Evidence

```
# Two Node Agent python processes with parent-child relationship:
PID     PPID    STARTED  CMD
2010172 2009724 23:46:35 python -m node_agent ...
2394920 2010172 03:22:51 python -m node_agent ...

# 15 "Shutting down" events with 0 pod restarts - process keeps catching
# SIGTERMs without actually exiting cleanly:
2026-03-31 03:48:22,307 Shutting down (signal 15)...
2026-03-31 03:48:25,110 Shutting down (signal 15)...
2026-03-31 03:48:39,402 Shutting down (signal 15)...
... (12 more over 2 minutes)

# Node Agent PID namespace is shared with host (hostPID: true):
# PID 1 inside the container is systemd (host init), not the Node Agent.
```

### Code Locations

**Fork trigger** - every `NetNS()` call in the Node Agent:
- `node_agent/namespace_ops.py:69,108,143,176,195,227,269` - interface operations
- `node_agent/wiring.py:199` - default route removal
- `node_agent/ground_bridge.py:153,187` - GS bridge attach/detach
- `node_agent/handlers.py:496` - topology query

**pyroute2 fork mechanism:**
- `pyroute2/netns/__init__.py:396` - `ChildProcess()` context manager forks to create socket in namespace
- `pyroute2/process.py:202` - `os.fork()` call
- `pyroute2/config/__init__.py:81` - `child_process_mode = 'fork'` (default)
- `pyroute2/process.py:44-46` - child only resets signals when `disable_mp_signal=True` (default is False)

**Signal handler:**
- `node_agent/__main__.py:164-169` - SIGTERM handler calls `server.stop()`
- `node_agent/server.py:116-117` - `stop()` sets `_running = False`

---

## Impact on System Operation

1. **Complete data plane failure.** No ISL or ground link interfaces are created in any pod. FRR has no interfaces to form adjacencies over. Routing never converges. The VF shows green links (from the Scheduler tracking OME events) but these are phantom. No actual forwarding plane exists.

2. **Silent failure.** The session appears to deploy (pods Running, CR shows "Wiring"), but wiring never completes. The Node Agent logs show latency update warnings (`ns(0)/isl2: Interface not found`) that look like transient errors, not a fundamental wiring failure.

3. **Recovery requires manual intervention.** A simple pod restart doesn't fix it because the orphaned child still holds port 50100. Requires identifying and killing the orphaned python process on the host, then restarting the Node Agent pod.

---

## Recommendations

### Option A: Set `child_process_mode = 'mp'` (multiprocessing instead of fork)

pyroute2 supports `multiprocessing.Process` as an alternative to `os.fork()`. The `mp` mode uses `multiprocessing.Process()` which creates a proper child process that does NOT inherit signal handlers from the parent.

**Change:**
```python
# At Node Agent startup, before any pyroute2 calls:
import pyroute2.config
pyroute2.config.child_process_mode = 'mp'
```

**Pros:**
- One line change. No refactoring.
- Child processes are proper subprocesses, not forks. Signal handlers are not inherited.
- pyroute2 already supports this mode. It's a documented configuration option.

**Cons:**
- `multiprocessing.Process` is heavier than `fork()`. Each namespace operation spawns a full subprocess. With 78 pods × 4 ISL interfaces × multiple operations, this could be noticeably slower during wiring.
- Need to verify that `multiprocessing` works correctly in the DaemonSet's `hostPID: true` environment.

### Option B: Reset signals in forked children via `disable_mp_signal = True`

pyroute2 has a config flag `disable_mp_signal` that, when True, resets SIGTERM to `SIG_DFL` in the forked child (line 46 of `process.py`). This means the child ignores the parent's custom handler and uses the default behavior (terminate immediately).

**Change:**
```python
# At Node Agent startup:
import pyroute2.config
pyroute2.config.disable_mp_signal = True
```

**Pros:**
- One line change. Keeps the fast `fork()` mode.
- Children still fork (fast) but don't inherit the parent's SIGTERM handler.
- The child exits immediately on SIGTERM instead of running the parent's shutdown logic.

**Cons:**
- The flag name is misleading (`disable_mp_signal` affects fork mode too, not just mp mode).
- pyroute2's documentation for this flag is minimal. The behavior may change between versions.
- Still uses `fork()` in a multi-threaded process, which Python's own deprecation warning flags as unsafe.

### Option C: Replace `NetNS()` with `setns()` syscall (no fork at all)

Instead of pyroute2's `NetNS()` (which forks to enter the namespace), use the `setns()` syscall directly to enter the target namespace in the current thread, perform the operation, then return to the host namespace. This eliminates the fork entirely.

**Change:** Refactor `namespace_ops.py` to use `ctypes.CDLL('libc.so.6').setns()` with the namespace fd, perform operations with `IPRoute()` (which does NOT fork), then `setns()` back to the host namespace.

```python
import ctypes
import os
from pyroute2 import IPRoute

_libc = ctypes.CDLL('libc.so.6', use_errno=True)

def _in_namespace(pid: int, fn):
    """Execute fn inside a network namespace, then return to host."""
    host_ns_fd = os.open('/proc/1/ns/net', os.O_RDONLY)
    target_ns_fd = os.open(f'/proc/{pid}/ns/net', os.O_RDONLY)
    try:
        _libc.setns(target_ns_fd, 0)
        return fn()
    finally:
        _libc.setns(host_ns_fd, 0)
        os.close(target_ns_fd)
        os.close(host_ns_fd)
```

**Pros:**
- Eliminates fork entirely. No child processes, no signal inheritance, no port conflicts.
- Fastest option. `setns()` is a single syscall, no process creation overhead.
- No dependency on pyroute2's process management. Uses `IPRoute()` directly.
- Eliminates the Python deprecation warning about fork in multi-threaded processes.

**Cons:**
- Largest code change. Every `NetNS()` call in `namespace_ops.py`, `wiring.py`, `ground_bridge.py`, and `handlers.py` must be refactored.
- `setns()` changes the calling thread's namespace. In a multi-threaded process, other threads see the host namespace while the current thread is in the target namespace. This is correct for our use case (only the wiring thread or handler thread enters namespaces) but requires care to ensure no concurrent namespace operations on the same thread.
- Uses `ctypes` to call `setns()`. Less portable than pyroute2's abstraction, though Linux-only is acceptable.
- Must handle the host namespace fd correctly. With `hostPID: true`, `/proc/1/ns/net` is the host network namespace (PID 1 is host systemd). If `hostPID` changes, this path changes.

### Option D: Isolate NetNS operations in a dedicated subprocess

Run all pyroute2 `NetNS()` operations in a long-lived subprocess that communicates with the Node Agent main process over a pipe or Unix socket. The subprocess forks freely but is isolated. Its signal handlers, port bindings, and crashes don't affect the main process.

**Change:** Create `node_agent/namespace_worker.py` that runs as a subprocess, accepts namespace operation commands over a pipe, and returns results. The main process never calls `NetNS()` directly.

**Pros:**
- Complete isolation. The main process never forks. Signal handling, ZMQ server, and namespace operations are in separate processes.
- The subprocess can crash or hang without affecting the ZMQ server.
- Conceptually clean. Matches the Scheduler/Node Agent split where the ZMQ server dispatches to a worker that does the heavy lifting.

**Cons:**
- Significant refactoring of the operation dispatch model.
- Adds IPC complexity (serialization over pipe, error propagation, subprocess lifecycle management).
- Slower than in-process operations due to IPC overhead on every netlink call.
- The subprocess still uses pyroute2's `NetNS()` and fork. It just isolates the consequences. Doesn't eliminate the fundamental issue, only contains it.

---

## Recommendation

**Start with Option B (`disable_mp_signal = True`) for immediate fix, then migrate to Option C (`setns()`) for the structural solution.**

Option B is a one-line change that unblocks deploys immediately. Option C is the correct long-term architecture. It eliminates fork entirely and removes an entire class of problems (signal inheritance, fd leakage, multi-threaded fork unsafety). Option C should be done as a focused refactor of `namespace_ops.py` with the `setns()` pattern, then the callers updated one at a time.

Option A (`mp` mode) is not recommended because `multiprocessing.Process` in a `hostPID` container has its own complications and is slower than either fork or setns.

Option D is over-engineered for this problem. The fork isn't inherently dangerous, just its signal handler inheritance.
