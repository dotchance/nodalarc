# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""Validated pod network-namespace discovery for pods on the local node.

The DaemonSet variant filters by spec.nodeName so each agent only discovers
pods on its own node. Discovery resolves the pod sandbox process, never a
workload container: the sandbox holds the pod network namespace from the
moment the pod is provisioned, so wiring can begin before any authored
container has started and never depends on container ordering or state.

Discovery reads the host's process table directly. Every process of a pod
carries the pod UID in its cgroup path, and the last component of that path
names its container. The pod's status names the ID of every container it
runs; the one container of the pod that the status does not name is its
sandbox. No container runtime client is involved, so discovery works with
any runtime that runs a sandbox process (containerd, and CRI-O with an infra
container).

Discovery is fenced to the active deployment run: only pods carrying the
manifest's session-run and owner-uid labels count, so a stale pod from a
previous deployment can never satisfy discovery. Every returned handle is
validated end to end: the sandbox process carries the pod UID, it is the
only container the pod status does not name, its PID is alive, and the
network-namespace inode was read from that PID. Anything ambiguous or
unverifiable is omitted; callers treat missing entries as pending and retry. Discovery output never defines expectation:
the expected-local set always comes from the wiring manifest.

IMPORTANT — node ID contract:
  The node_id keying the result comes from the K8s label
  "nodalarc.io/node-id", which carries the runtime node ID from the resolved
  session manifest. All Node Agent protobuf messages must use this exact
  value because ground bridge naming helpers derive host veth names from the
  node ID, and Linux interface names are case-sensitive.
"""

from __future__ import annotations

import logging
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass

from nodalarc.substrate.manifest_contract import (
    POD_OWNER_UID_LABEL,
    POD_SESSION_RUN_LABEL,
)
from nodalarc.workload_target import NODE_ID_LABEL

log = logging.getLogger(__name__)

# A pod's cgroup directory carries its UID, with underscores for dashes under
# the systemd cgroup driver ("kubepods-besteffort-pod<uid>.slice") and as is
# under cgroupfs ("pod<uid>").
_POD_UID_RE = re.compile(
    r"pod([0-9a-f]{8}[-_][0-9a-f]{4}[-_][0-9a-f]{4}[-_][0-9a-f]{4}[-_][0-9a-f]{12})"
)
# A container's cgroup directory: the 64-hex container ID, with the runtime's
# prefix and ".scope" under the systemd driver, bare under cgroupfs.
_CONTAINER_DIR_RE = re.compile(r"(?:cri-containerd-|crio-|docker-)?([0-9a-f]{64})(?:\.scope)?")


@dataclass(frozen=True, slots=True)
class NamespaceHandle:
    """One validated pod network-namespace handle.

    ``pid`` is the sandbox PID whose /proc/<pid>/ns/net is the pod network
    namespace; ``netns_id`` is that namespace's nsfs inode, read at
    discovery time. Consumers must re-verify the handle (``verify_handle``)
    before kernel mutations: a sandbox recreation invalidates the handle
    even though the pod UID is unchanged.

    ``mpls_enable`` is the wiring manifest's requirement for this node,
    bound when the handle is built so every consumer of a published handle
    reads the manifest's value and never a default.
    """

    node_id: str
    pod_name: str
    pod_uid: str
    sandbox_id: str
    pid: int
    netns_id: str
    mpls_enable: bool


def netns_identity(pid: int) -> str | None:
    """Return the nsfs inode of a PID's network namespace, or None if gone."""
    try:
        return str(os.stat(f"/proc/{pid}/ns/net").st_ino)
    except OSError:
        return None


def verify_handle(handle: NamespaceHandle) -> bool:
    """Whether the handle still names the exact namespace it was created for."""
    return netns_identity(handle.pid) == handle.netns_id


@dataclass(frozen=True, slots=True)
class PodProcesses:
    """The processes of one pod on this host, by container ID, read from /proc."""

    by_container: dict[str, list[int]]
    unrecognized: list[str]


def pod_processes(pod_uids: set[str], *, proc_root: str = "/proc") -> dict[str, PodProcesses]:
    """Read every process's cgroup and group the processes of ``pod_uids`` by container.

    A process of a wanted pod whose cgroup path names no container ID in a
    known form is recorded with its path, so the caller can refuse the pod
    with the path named. Processes that exit while the table is read are
    skipped.
    """
    found = {uid: PodProcesses(by_container={}, unrecognized=[]) for uid in pod_uids}
    for entry in os.scandir(proc_root):
        if not entry.name.isdigit():
            continue
        try:
            with open(f"{proc_root}/{entry.name}/cgroup", encoding="utf-8") as handle:
                lines = handle.read().splitlines()
        except OSError:
            continue
        for line in lines:
            path = line.split(":", 2)[-1]
            match = _POD_UID_RE.search(path)
            if match is None:
                continue
            uid = match.group(1).replace("_", "-")
            if uid not in found:
                break
            container = _CONTAINER_DIR_RE.fullmatch(path.rsplit("/", 1)[-1])
            if container is None:
                found[uid].unrecognized.append(path)
            else:
                found[uid].by_container.setdefault(container.group(1), []).append(int(entry.name))
            break
    return found


def _status_container_ids(pod) -> set[str]:
    """The IDs of every container the pod's status names, without the runtime scheme."""
    status = pod.status
    ids = set()
    for statuses in (
        status.init_container_statuses,
        status.container_statuses,
        status.ephemeral_container_statuses,
    ):
        for container in statuses or ():
            if container.container_id:
                ids.add(container.container_id.split("://", 1)[-1])
    return ids


def _validated_sandbox_handle(
    node_id: str,
    pod,
    processes: PodProcesses,
    *,
    mpls_enable: bool,
) -> NamespaceHandle | None:
    """Find the pod's sandbox process and return a fully validated handle, or None.

    The sandbox is the one container of the pod that its status does not
    name. More than one such container means the status has not yet caught
    up with a container that started; none means this runtime runs the pod
    without a sandbox process, which NodalArc cannot wire. Non-negotiable:
    the discovered namespace is not the host network namespace. A bad PID
    must never let the Node Agent rename the host's interfaces, delete its
    default route, or alter its firewall.
    """
    pod_name, pod_uid = pod.metadata.name, pod.metadata.uid
    if processes.unrecognized:
        log.error(
            "Pod %s (%s) has processes in cgroups whose container is not recognized: %s",
            pod_name,
            node_id,
            processes.unrecognized[:3],
        )
        return None
    unnamed = sorted(set(processes.by_container) - _status_container_ids(pod))
    if not unnamed:
        if processes.by_container:
            log.error(
                "Pod %s (%s) has no sandbox process: every container is named in its status "
                "(a runtime that runs pods without an infra container cannot be wired)",
                pod_name,
                node_id,
            )
        else:
            log.info("No processes yet for %s (pod UID %s)", node_id, pod_uid)
        return None
    if len(unnamed) > 1:
        log.info(
            "Pod %s (%s) has %d containers its status does not name yet; waiting",
            pod_name,
            node_id,
            len(unnamed),
        )
        return None
    sandbox_id = unnamed[0]
    pids = processes.by_container[sandbox_id]
    if len(pids) != 1:
        log.warning(
            "Sandbox %s of %s has %d processes; its one process is expected",
            sandbox_id[:13],
            node_id,
            len(pids),
        )
        return None
    pid = pids[0]
    netns = netns_identity(pid)
    if netns is None:
        log.warning("Sandbox %s PID %d for %s has no readable netns", sandbox_id[:13], pid, node_id)
        return None
    host_netns = netns_identity(1)
    if host_netns is not None and netns == host_netns:
        log.error(
            "Sandbox %s PID %d for %s resolves to the HOST network namespace — rejected",
            sandbox_id[:13],
            pid,
            node_id,
        )
        return None
    return NamespaceHandle(
        node_id=node_id,
        pod_name=pod_name,
        pod_uid=pod_uid,
        sandbox_id=sandbox_id,
        pid=pid,
        netns_id=netns,
        mpls_enable=mpls_enable,
    )


def discover_local_pod_handles(
    namespace: str | None = None,
    node_name: str | None = None,
    *,
    session_run_id: str,
    owner_uid: str,
    requirements: Mapping[str, bool],
) -> dict[str, NamespaceHandle]:
    """Discover validated namespace handles for current-run pods on this node.

    Returns {node_id: NamespaceHandle} containing only pods that carry the
    active run identity and passed every validation step. Missing entries
    mean pending; callers retry against the manifest's expected-local set
    and never conclude from this map alone.

    ``requirements`` maps every node the manifest places on this host to its
    MPLS requirement. It is the only source of a handle's ``mpls_enable``;
    a current-run pod whose node is not in it is not expected here and gets
    no handle.
    """
    import kubernetes
    import kubernetes.client
    import kubernetes.config

    if not session_run_id or not owner_uid:
        raise ValueError("discovery requires the active session_run_id and owner_uid")
    if not requirements:
        raise ValueError("discovery requires the manifest's expected-local node requirements")

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

    candidates: dict[str, object] = {}
    duplicates: set[str] = set()
    for pod in pods.items:
        node_id = pod.metadata.labels.get(NODE_ID_LABEL)
        if not node_id:
            continue
        if node_id not in requirements:
            log.info("Current-run pod for %s is not expected on this host by the manifest", node_id)
            continue
        if node_id in candidates:
            duplicates.add(node_id)
            continue
        candidates[node_id] = pod
    for node_id in duplicates:
        log.error(
            "Node ID %s is carried by more than one current-run pod on this node — rejected",
            node_id,
        )
        del candidates[node_id]

    processes = pod_processes({pod.metadata.uid for pod in candidates.values()})
    result: dict[str, NamespaceHandle] = {}
    for node_id, pod in candidates.items():
        handle = _validated_sandbox_handle(
            node_id, pod, processes[pod.metadata.uid], mpls_enable=requirements[node_id]
        )
        if handle is None:
            continue
        result[node_id] = handle
        log.info(
            "Discovered %s -> sandbox %s PID %d netns %s",
            node_id,
            handle.sandbox_id[:13],
            handle.pid,
            handle.netns_id,
        )

    log.info("Discovered %d validated handles on node %s", len(result), node_name or "(all)")
    return result
