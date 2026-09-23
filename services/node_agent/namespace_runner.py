# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""Read/write namespace runner for Node Agent kernel operations.

This is the public wrapper around the setns model used by namespace_ops.py.
Verification code uses this instead of shelling out to nsenter.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TypeVar

from pyroute2 import IPRoute

from node_agent.namespace_ops import _in_namespace, in_host_namespace

_T = TypeVar("_T")


def run_in_pod_namespace(pid: int, fn: Callable[[IPRoute], _T]) -> _T:
    """Run a short pyroute2 operation inside a pod network namespace."""
    return _in_namespace(pid, fn)


def run_in_host_namespace(fn: Callable[[IPRoute], _T]) -> _T:
    """Run a short pyroute2 operation inside the host network namespace."""
    return in_host_namespace(fn)
