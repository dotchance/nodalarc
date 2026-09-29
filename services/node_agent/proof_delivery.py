# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""Wiring proof delivered into a session pod's wiring-status volume.

The Node Agent writes each pod's wiring proof onto the pod as an annotation,
which the Operator and the Scheduler read. The pod's release gate reads the
same bytes from a file in the pod's wiring-status emptyDir, which the Node
Agent writes from the host side. It reaches the kubelet's pods directory
through the host's root as its process table shows it (``/proc/1/root``,
the Node Agent runs in the host PID namespace), at the kubelet root directory
the installation names (``kubelet_root_dir``). No host directory is mounted
into the Node Agent, and the kubelet takes no part in delivering the proof.
"""

from __future__ import annotations

import os

from nodalarc.platform_config import get_platform_config
from nodalarc.substrate.wiring_status import WIRING_STATUS_FILE, wiring_status_host_path

HOST_ROOT = "/proc/1/root"


def kubelet_pods_dir() -> str:
    """The host's kubelet pods directory, reached through the host's root.

    Raises when it is not a directory: the installation named a kubelet root
    directory this host does not have.
    """
    root_dir = get_platform_config().kubelet_root_dir
    path = f"{HOST_ROOT}{root_dir.rstrip('/')}/pods"
    if not os.path.isdir(path):
        raise RuntimeError(
            f"the kubelet pods directory {root_dir.rstrip('/')}/pods is not on this host "
            f"(checked {path}); set nodeAgent.kubeletRootDir to the kubelet's --root-dir"
        )
    return path


def deliver_proof_file(pods_dir: str, pod_uid: str, encoded: str) -> None:
    """Replace the proof file in the pod's wiring-status volume.

    The file is staged beside the target and renamed over it, so the gate
    reads either the previous proof or this one, never a partial file. A
    missing volume directory raises: the kubelet creates it before the pod
    sandbox, so a pod the Node Agent can see always has it.
    """
    directory = wiring_status_host_path(pods_dir, pod_uid)
    staging = os.path.join(directory, f".{WIRING_STATUS_FILE}.staging")
    with open(staging, "w", encoding="utf-8") as handle:
        handle.write(encoded)
    os.chmod(staging, 0o644)
    os.replace(staging, os.path.join(directory, WIRING_STATUS_FILE))


def delivered_proof(pods_dir: str, pod_uid: str) -> str | None:
    """The proof file's content, or None when nothing was delivered."""
    path = os.path.join(wiring_status_host_path(pods_dir, pod_uid), WIRING_STATUS_FILE)
    try:
        with open(path, encoding="utf-8") as handle:
            return handle.read()
    except FileNotFoundError:
        return None
