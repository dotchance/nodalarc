# Copyright 2024-2026 .chance (dotchance)
# Licensed under the NodalArc Source Available License 1.0. See LICENSE file.
"""Read/write namespace runner for Node Agent kernel operations.

This is the public wrapper around the setns model used by namespace_ops.py.
Verification code uses this instead of shelling out to nsenter.
"""

from __future__ import annotations

import ctypes
import os
from collections.abc import Callable
from typing import TypeVar

from pyroute2 import IPRoute

from node_agent.namespace_ops import _CLONE_NEWNET, _get_host_ns_fd, _in_namespace, _libc, _ns_lock

_T = TypeVar("_T")


def run_in_pod_namespace(pid: int, fn: Callable[[IPRoute], _T]) -> _T:
    """Run a short pyroute2 operation inside a pod network namespace."""
    return _in_namespace(pid, fn)


def run_in_host_namespace(fn: Callable[[IPRoute], _T]) -> _T:
    """Run a short pyroute2 operation inside the host network namespace."""
    with _ns_lock:
        ret = _libc.setns(_get_host_ns_fd(), _CLONE_NEWNET)
        if ret != 0:
            errno = ctypes.get_errno()
            raise OSError(errno, f"setns to host failed: {os.strerror(errno)}")
        ipr = IPRoute()
        try:
            return fn(ipr)
        finally:
            ipr.close()
