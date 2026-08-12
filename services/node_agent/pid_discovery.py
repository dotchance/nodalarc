# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""Validated pod network-namespace discovery for pods on the local K3s node.

The DaemonSet variant filters by spec.nodeName so each agent only discovers
pods on its own node. Discovery resolves the CRI pod sandbox, never a
workload container: the sandbox holds the pod network namespace from the
moment the pod is provisioned, so wiring can begin before any authored
container has started and never depends on container ordering or state.

Discovery is fenced to the active deployment run: only pods carrying the
manifest's session-run and owner-uid labels count, so a stale pod from a
previous deployment can never satisfy discovery. Every returned handle is
validated end to end — sandbox identity matches the pod UID, the sandbox is
ready, its PID is alive, and the network-namespace inode was read from that
PID. Anything ambiguous or unverifiable is omitted; callers treat missing
entries as pending and retry. Discovery output never defines expectation:
the expected-local set always comes from the wiring manifest.

IMPORTANT — node ID contract:
  The node_id keying the result comes from the K8s label
  "nodalarc.io/node-id", which carries the runtime node ID from the resolved
  session manifest. All Node Agent protobuf messages must use this exact
  value because ground bridge naming helpers derive host veth names from the
  node ID, and Linux interface names are case-sensitive.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
from dataclasses import dataclass

from nodalarc.substrate.manifest_contract import (
    POD_OWNER_UID_LABEL,
    POD_SESSION_RUN_LABEL,
)

log = logging.getLogger(__name__)

_SANDBOX_READY = "SANDBOX_READY"


@dataclass(frozen=True, slots=True)
class NamespaceHandle:
    """One validated pod network-namespace handle.

    ``pid`` is the sandbox PID whose /proc/<pid>/ns/net is the pod network
    namespace; ``netns_id`` is that namespace's nsfs inode, read at
    discovery time. Consumers must re-verify the handle (``verify_handle``)
    before kernel mutations: a sandbox recreation invalidates the handle
    even though the pod UID is unchanged.
    """

    node_id: str
    pod_uid: str
    sandbox_id: str
    sandbox_attempt: int
    pid: int
    netns_id: str


def netns_identity(pid: int) -> str | None:
    """Return the nsfs inode of a PID's network namespace, or None if gone."""
    try:
        return str(os.stat(f"/proc/{pid}/ns/net").st_ino)
    except OSError:
        return None


def verify_handle(handle: NamespaceHandle) -> bool:
    """Whether the handle still names the exact namespace it was created for."""
    return netns_identity(handle.pid) == handle.netns_id


def _crictl_command() -> list[str]:
    """Base crictl invocation honoring the K3s runtime endpoint."""
    command = ["crictl"]
    runtime_ep = os.environ.get("CONTAINER_RUNTIME_ENDPOINT")
    if runtime_ep:
        command.extend(["--runtime-endpoint", runtime_ep])
    return command


def _ready_sandboxes_by_pod_uid() -> dict[str, tuple[str, int]] | None:
    """Map pod UID -> (sandbox ID, attempt) for ready sandboxes on this node.

    Among several ready sandboxes for one pod UID the highest attempt wins
    deterministically; equal ready attempts are ambiguous and reject the
    pod UID outright. Returns None when the listing itself failed, so the
    caller can distinguish "CRI unavailable" from "no sandboxes".
    """
    try:
        proc = subprocess.run(
            [*_crictl_command(), "pods", "-o", "json"],
            capture_output=True,
            text=True,
            check=True,
        )
        listing = json.loads(proc.stdout)
    except (subprocess.CalledProcessError, json.JSONDecodeError) as exc:
        log.error("Failed to list CRI pod sandboxes: %s", exc)
        return None
    by_uid: dict[str, tuple[str, int]] = {}
    ambiguous: set[str] = set()
    for item in listing.get("items") or []:
        if item.get("state") != _SANDBOX_READY:
            continue
        sandbox_id = item.get("id")
        metadata = item.get("metadata") or {}
        pod_uid = metadata.get("uid")
        attempt = metadata.get("attempt", 0)
        if not sandbox_id or not pod_uid or not isinstance(attempt, int):
            continue
        current = by_uid.get(pod_uid)
        if current is None or attempt > current[1]:
            by_uid[pod_uid] = (sandbox_id, attempt)
            ambiguous.discard(pod_uid)
        elif attempt == current[1] and sandbox_id != current[0]:
            ambiguous.add(pod_uid)
    for pod_uid in ambiguous:
        log.error("Pod UID %s has two ready sandboxes at the same attempt — rejected", pod_uid)
        del by_uid[pod_uid]
    return by_uid


def _validated_sandbox_handle(
    node_id: str, pod_uid: str, sandbox_id: str, attempt: int, pod_ip: str | None
) -> NamespaceHandle | None:
    """Inspect one sandbox and return a fully validated handle, or None.

    Every identity fact inspectp offers is checked before the PID is
    trusted for privileged namespace mutation: the echoed sandbox ID and
    attempt, the pod UID, readiness, the sandbox IP against the Kubernetes
    pod IP, and — non-negotiable — that the discovered namespace is not the
    host network namespace. A bad PID must never let the Node Agent rename
    the host's interfaces, delete its default route, or alter its firewall.
    """
    try:
        proc = subprocess.run(
            [*_crictl_command(), "inspectp", sandbox_id],
            capture_output=True,
            text=True,
            check=True,
        )
        info = json.loads(proc.stdout)
    except (subprocess.CalledProcessError, json.JSONDecodeError) as exc:
        log.warning("Failed to inspect sandbox %s for %s: %s", sandbox_id, node_id, exc)
        return None
    status = info.get("status") or {}
    observed_id = status.get("id")
    if observed_id != sandbox_id:
        log.warning(
            "Sandbox inspection for %s echoed a different sandbox: asked %s, got %r",
            node_id,
            sandbox_id,
            observed_id,
        )
        return None
    metadata = status.get("metadata") or {}
    if metadata.get("uid") != pod_uid:
        log.warning(
            "Sandbox %s identity mismatch for %s: expected pod UID %s, observed %r",
            sandbox_id,
            node_id,
            pod_uid,
            metadata.get("uid"),
        )
        return None
    if metadata.get("attempt") != attempt:
        log.warning(
            "Sandbox %s attempt changed for %s: expected %d, observed %r",
            sandbox_id,
            node_id,
            attempt,
            metadata.get("attempt"),
        )
        return None
    if status.get("state") != _SANDBOX_READY:
        log.warning("Sandbox %s for %s is no longer ready", sandbox_id, node_id)
        return None
    sandbox_ip = ((status.get("network") or {}).get("ip")) or None
    if pod_ip and sandbox_ip and sandbox_ip != pod_ip:
        log.warning(
            "Sandbox %s IP %s does not match pod IP %s for %s",
            sandbox_id,
            sandbox_ip,
            pod_ip,
            node_id,
        )
        return None
    pid = (info.get("info") or {}).get("pid")
    if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
        log.warning("Sandbox %s for %s reports no live PID: %r", sandbox_id, node_id, pid)
        return None
    netns = netns_identity(pid)
    if netns is None:
        log.warning("Sandbox %s PID %d for %s has no readable netns", sandbox_id, pid, node_id)
        return None
    host_netns = netns_identity(1)
    if host_netns is not None and netns == host_netns:
        log.error(
            "Sandbox %s PID %d for %s resolves to the HOST network namespace — rejected",
            sandbox_id,
            pid,
            node_id,
        )
        return None
    return NamespaceHandle(
        node_id=node_id,
        pod_uid=pod_uid,
        sandbox_id=sandbox_id,
        sandbox_attempt=attempt,
        pid=pid,
        netns_id=netns,
    )


def discover_local_pod_handles(
    namespace: str | None = None,
    node_name: str | None = None,
    *,
    session_run_id: str,
    owner_uid: str,
) -> dict[str, NamespaceHandle]:
    """Discover validated namespace handles for current-run pods on this node.

    Returns {node_id: NamespaceHandle} containing only pods that carry the
    active run identity and passed every validation step. Missing entries
    mean pending; callers retry against the manifest's expected-local set
    and never conclude from this map alone.
    """
    import kubernetes
    import kubernetes.client
    import kubernetes.config

    if not session_run_id or not owner_uid:
        raise ValueError("discovery requires the active session_run_id and owner_uid")

    if namespace is None:
        from nodalarc.platform_config import get_platform_config

        namespace = get_platform_config().kubernetes_namespace

    if node_name is None:
        node_name = os.environ.get("NODE_NAME", "")

    try:
        kubernetes.config.load_incluster_config()
    except kubernetes.config.config_exception.ConfigException:
        kubernetes.config.load_kube_config()

    v1 = kubernetes.client.CoreV1Api()

    label_selector = (
        f"nodalarc.io/role,{POD_SESSION_RUN_LABEL}={session_run_id},"
        f"{POD_OWNER_UID_LABEL}={owner_uid}"
    )
    field_selector = f"spec.nodeName={node_name}" if node_name else ""
    pods = v1.list_namespaced_pod(
        namespace,
        label_selector=label_selector,
        field_selector=field_selector,
    )

    sandboxes = _ready_sandboxes_by_pod_uid()
    if sandboxes is None:
        return {}

    candidates: dict[str, tuple[str, str | None]] = {}
    duplicates: set[str] = set()
    for pod in pods.items:
        node_id = pod.metadata.labels.get("nodalarc.io/node-id")
        if not node_id:
            continue
        if node_id in candidates:
            duplicates.add(node_id)
            continue
        pod_ip = pod.status.pod_ip if pod.status else None
        candidates[node_id] = (pod.metadata.uid, pod_ip)
    for node_id in duplicates:
        log.error(
            "Node ID %s is carried by more than one current-run pod on this node — rejected",
            node_id,
        )
        del candidates[node_id]

    result: dict[str, NamespaceHandle] = {}
    for node_id, (pod_uid, pod_ip) in candidates.items():
        sandbox = sandboxes.get(pod_uid)
        if sandbox is None:
            log.info("No ready sandbox yet for %s (pod UID %s)", node_id, pod_uid)
            continue
        handle = _validated_sandbox_handle(node_id, pod_uid, *sandbox, pod_ip)
        if handle is None:
            continue
        result[node_id] = handle
        log.info(
            "Discovered %s -> sandbox %s attempt %d PID %d netns %s",
            node_id,
            handle.sandbox_id[:13],
            handle.sandbox_attempt,
            handle.pid,
            handle.netns_id,
        )

    log.info("Discovered %d validated handles on node %s", len(result), node_name or "(all)")
    return result
