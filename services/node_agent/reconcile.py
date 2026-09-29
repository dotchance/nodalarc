# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""Kernel-vs-manifest reconciliation for Node Agent wiring.

The Node Agent is stateless across restarts. On every startup and manifest
change, it diffs desired (the wiring manifest) vs actual (kernel) and acts:
  Case A — No kernel state, no current wiring proof: wire from scratch
  Case B — Every local pod carries current wiring proof: no-op
  Case C — Kernel state exists but proof is absent or stale: clean, re-wire
"""

from __future__ import annotations

import errno
import logging
import socket
import sys
from collections.abc import Sequence

from nodalarc.runtime_naming import MANAGED_HOST_DEVICE_GROUP
from nodalarc.substrate.manifest_contract import (
    POD_OWNER_UID_LABEL,
    POD_SESSION_RUN_LABEL,
    WiringManifest,
)
from nodalarc.substrate.wiring_status import WIRING_STATUS_ANNOTATION, pod_wiring_statuses
from nodalarc.workload_target import NODE_ID_LABEL
from pydantic import BaseModel, ConfigDict
from pyroute2 import IPRoute
from pyroute2.netlink.exceptions import NetlinkError

from node_agent.emulated_lan import (
    EMULATED_LAN_NAMESPACE,
    emulated_lan_namespace_present,
    remove_emulated_lan_namespace,
)
from node_agent.proof_delivery import delivered_proof, kubelet_pods_dir

log = logging.getLogger(__name__)


def _managed_devices(ipr: IPRoute) -> dict[str, int]:
    """Host devices in NodalArc's device group, by name, with their indexes.

    The group is the only mark of ownership: every host device NodalArc
    creates joins it, and a device outside it is never NodalArc's, whatever
    its name.
    """
    return {
        link.get_attr("IFLA_IFNAME", ""): int(link["index"])
        for link in ipr.get_links()
        if link.get_attr("IFLA_GROUP") == MANAGED_HOST_DEVICE_GROUP
    }


def get_actual_nodalarc_interfaces() -> set[str]:
    """The names of the host devices in NodalArc's device group."""
    with IPRoute() as ipr:
        return set(_managed_devices(ipr))


class HostCleanupReport(BaseModel):
    """What one host cleanup removed, could not remove, and found remaining.

    The report is the cleanup's only result: the Node Agent decides from it
    whether wiring may start, and the teardown judges each host by it. A
    device already gone when its delete runs is absence, never a failure.
    Every other error is recorded with its text, and the final enumeration
    lists every device still in NodalArc's device group.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    host: str
    removed: tuple[str, ...]
    failed: tuple[tuple[str, str], ...]
    remaining: tuple[str, ...]
    verification_completed: bool
    enumeration_error: str | None = None
    verification_error: str | None = None

    @property
    def clean(self) -> bool:
        """No failed delete, nothing remaining, both enumerations completed."""
        return (
            not self.failed
            and not self.remaining
            and self.verification_completed
            and self.enumeration_error is None
        )


def _error_text(exc: BaseException) -> str:
    return f"{type(exc).__name__}: {exc}"


def clean_and_verify_host_state() -> HostCleanupReport:
    """Delete every device in NodalArc's device group and the site-LAN namespace, then verify.

    Deletion failures do not stop the pass: every member is attempted, each
    failure is retained with its error, and the report carries whatever the
    final enumeration still finds. The report is returned, never raised, so a
    caller always sees the whole picture. Devices outside the group are never
    touched. Removing the emulated_lan namespace destroys every site-LAN
    bridge and port in it.
    """
    host = socket.gethostname()
    removed: list[str] = []
    failed: list[tuple[str, str]] = []
    try:
        with IPRoute() as ipr:
            before = _managed_devices(ipr)
            # One request deletes the whole group, and the kernel unregisters
            # its members as one batch. The request is sent only when the
            # group has members, so a host with nothing to remove needs no
            # privilege to prove itself clean.
            if before:
                try:
                    ipr.link("del", group=MANAGED_HOST_DEVICE_GROUP)
                except NetlinkError as exc:
                    if exc.code != errno.ENODEV:  # ENODEV: the group emptied meanwhile
                        failed.append((f"group {MANAGED_HOST_DEVICE_GROUP:#x}", _error_text(exc)))
            # A member the group request left behind is deleted by itself.
            left = _managed_devices(ipr) if before else {}
            removed.extend(sorted(set(before) - set(left)))
            for name, index in left.items():
                try:
                    ipr.link("del", index=index)
                    removed.append(name)
                except NetlinkError as exc:
                    if exc.code == errno.ENODEV:
                        continue  # already gone: absence, not a failure
                    failed.append((name, _error_text(exc)))
                except Exception as exc:
                    failed.append((name, _error_text(exc)))
        lan = f"netns {EMULATED_LAN_NAMESPACE}"
        try:
            if remove_emulated_lan_namespace():
                removed.append(lan)
        except Exception as exc:
            failed.append((lan, _error_text(exc)))
    except Exception as exc:
        return HostCleanupReport(
            host=host,
            removed=tuple(removed),
            failed=tuple(failed),
            remaining=(),
            verification_completed=False,
            enumeration_error=_error_text(exc),
        )
    try:
        remaining = tuple(sorted(get_actual_nodalarc_interfaces()))
        if emulated_lan_namespace_present():
            remaining += (f"netns {EMULATED_LAN_NAMESPACE}",)
    except Exception as exc:
        return HostCleanupReport(
            host=host,
            removed=tuple(removed),
            failed=tuple(failed),
            remaining=(),
            verification_completed=False,
            verification_error=_error_text(exc),
        )
    return HostCleanupReport(
        host=host,
        removed=tuple(removed),
        failed=tuple(failed),
        remaining=remaining,
        verification_completed=True,
    )


USAGE = "usage: python -m node_agent.reconcile --clean"


def main(argv: Sequence[str] | None = None) -> int:
    """The cleanup entry point: one JSON report line; exit 0 only when clean."""
    args = list(sys.argv[1:] if argv is None else argv)
    if args != ["--clean"]:
        sys.stderr.write(USAGE + "\n")
        return 2
    report = clean_and_verify_host_state()
    sys.stdout.write(report.model_dump_json() + "\n")
    sys.stdout.flush()
    return 0 if report.clean else 1


def wiring_status_is_current(
    v1,
    namespace: str,
    manifest: WiringManifest,
    local_handles,
) -> bool:
    """Check whether this host's pods carry current wiring proof (Case B).

    True only when every pod in ``local_handles`` ({node_id: NamespaceHandle})
    carries a proof that is ready for the manifest and names the exact live
    pod incarnation, and its wiring-status volume holds the same proof. A
    proof written for a replaced pod or a recreated sandbox fails the binding
    and forces a rewire, as does a proof its pod's gate never received. The
    host owns only its own pods' kernel state, so only those proofs decide.
    """
    try:
        pods_dir = kubelet_pods_dir()
        pods = v1.list_namespaced_pod(
            namespace,
            label_selector=(
                f"{POD_SESSION_RUN_LABEL}={manifest.session_run_id},"
                f"{POD_OWNER_UID_LABEL}={manifest.owner_uid}"
            ),
        ).items
        by_uid = {pod.metadata.uid: pod for pod in pods}
        local_pods = [
            by_uid[handle.pod_uid] for handle in local_handles.values() if handle.pod_uid in by_uid
        ]
        if len(local_pods) != len(local_handles):
            return False
        statuses = pod_wiring_statuses(local_pods, node_id_label=NODE_ID_LABEL)
        for node_id, handle in local_handles.items():
            row = statuses.get(node_id)
            if row is None:
                return False
            if (
                row.pod_uid != handle.pod_uid
                or row.sandbox_id != handle.sandbox_id
                or row.netns_id != handle.netns_id
            ):
                log.warning(
                    "Wiring proof for %s names another pod incarnation "
                    "(proof pod=%s sandbox=%s netns=%s, live pod=%s sandbox=%s netns=%s) "
                    "— rewire required",
                    node_id,
                    row.pod_uid,
                    row.sandbox_id,
                    row.netns_id,
                    handle.pod_uid,
                    handle.sandbox_id,
                    handle.netns_id,
                )
                return False
            if not row.ready_for(manifest):
                return False
            annotation = by_uid[handle.pod_uid].metadata.annotations[WIRING_STATUS_ANNOTATION]
            if delivered_proof(pods_dir, handle.pod_uid) != annotation:
                log.warning(
                    "Wiring proof for %s was not delivered to pod %s — rewire required",
                    node_id,
                    handle.pod_uid,
                )
                return False
        return True
    except Exception as exc:
        log.warning("wiring proof validation failed: %s", exc)
        return False


if __name__ == "__main__":
    sys.exit(main())
