# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""Wiring proof delivered into a session pod's wiring-status volume.

The Node Agent writes each pod's wiring proof onto the pod as an annotation,
which the Operator and the Scheduler read. The pod's release gate reads the
same bytes from a file in the pod's wiring-status emptyDir, which the Node
Agent writes from the host side through the kubelet's pods directory
(``KUBELET_PODS_DIR``). The kubelet takes no part in delivering it.
"""

from __future__ import annotations

import os

from nodalarc.substrate.wiring_status import WIRING_STATUS_FILE, wiring_status_host_path


def kubelet_pods_dir() -> str:
    """The host's kubelet pods directory as mounted into the Node Agent.

    Named by ``KUBELET_PODS_DIR``; there is no default. Raises when it is
    unset or not a directory.
    """
    value = os.environ.get("KUBELET_PODS_DIR", "").strip()
    if not value:
        raise RuntimeError("KUBELET_PODS_DIR env var is required to deliver wiring proof")
    if not os.path.isdir(value):
        raise RuntimeError(f"KUBELET_PODS_DIR {value!r} is not a directory")
    return value


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
