# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""MPLS host preparation for Node Agent wiring."""

from __future__ import annotations

import logging
import os
import subprocess

from node_agent import ops_events

log = logging.getLogger(__name__)

MPLS_KERNEL_MODULES = ("mpls_router", "mpls_iptunnel")


def running_in_k8s() -> bool:
    return bool(os.environ.get("KUBERNETES_SERVICE_HOST") or os.environ.get("NODE_NAME"))


def load_mpls_kernel_modules() -> None:
    """Ask the host kernel to load MPLS support for an MPLS session.

    Module loading is best-effort because some kernels expose MPLS support
    without loadable modules. The wiring path still fails loudly if MPLS sysctls
    are unavailable when an MPLS session actually asks for MPLS on interfaces.
    """
    if not running_in_k8s():
        return

    for module in MPLS_KERNEL_MODULES:
        result = subprocess.run(
            ["modprobe", module],
            text=True,
            capture_output=True,
            check=False,
        )
        if result.returncode == 0:
            log.info("Kernel module loaded or already present: %s", module)
            continue
        message = (
            f"Kernel module {module!r} could not be loaded. MPLS sessions will fail "
            "until the host kernel exposes net.mpls sysctls."
        )
        ops_events.publish(
            level="warning",
            code="STARTUP_KERNEL_MODULE_UNAVAILABLE",
            message=message,
            session_id="",
            details={
                "module": module,
                "stderr": result.stderr.strip(),
                "returncode": result.returncode,
            },
        )
        log.warning("%s stderr=%s", message, result.stderr.strip())
