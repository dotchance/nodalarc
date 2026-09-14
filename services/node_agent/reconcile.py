# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""Kernel-vs-ConfigMap reconciliation for Node Agent wiring.

The Node Agent is stateless across restarts. On every startup and ConfigMap
change, it diffs desired (ConfigMap) vs actual (kernel) and acts accordingly:
  Case A — No kernel state, no current wiring-status: wire from scratch
  Case B — Wiring-status present and current: no-op
  Case C — Kernel state exists but wiring-status absent/stale: clean, re-wire
"""

from __future__ import annotations

import errno
import logging
import socket
import sys
from collections.abc import Sequence

from nodalarc.runtime_naming import is_managed_host_ifname
from nodalarc.substrate.manifest_contract import WiringManifest
from nodalarc.substrate.wiring_status import WIRING_STATUS_CONFIGMAP, parse_status_configmap
from pydantic import BaseModel, ConfigDict
from pyroute2 import IPRoute
from pyroute2.netlink.exceptions import NetlinkError

log = logging.getLogger(__name__)


def get_actual_nodalarc_interfaces() -> set[str]:
    """Enumerate nodalarc host-side interfaces from kernel via pyroute2."""
    with IPRoute() as ipr:
        return {
            link.get_attr("IFLA_IFNAME", "")
            for link in ipr.get_links()
            if is_managed_host_ifname(link.get_attr("IFLA_IFNAME", ""))
        }


class HostCleanupReport(BaseModel):
    """What one host cleanup removed, could not remove, and found remaining.

    The report is the cleanup's only result: the Node Agent decides from it
    whether wiring may start, and the teardown judges each host by it. A
    device already gone when its delete runs is absence, never a failure.
    Every other error is recorded with its text, and the final enumeration
    lists every recognized name still present.
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
    """Delete every recognized host-side device, then verify none remains.

    Deletion failures do not stop the pass: every recognized device is
    attempted, each failure is retained with its error, and the report
    carries whatever the final enumeration still finds. The report is
    returned, never raised, so a caller always sees the whole picture.
    """
    host = socket.gethostname()
    removed: list[str] = []
    failed: list[tuple[str, str]] = []
    try:
        with IPRoute() as ipr:
            targets = [
                (link.get_attr("IFLA_IFNAME", ""), int(link["index"]))
                for link in ipr.get_links()
                if is_managed_host_ifname(link.get_attr("IFLA_IFNAME", ""))
            ]
            for name, index in targets:
                try:
                    ipr.link("del", index=index)
                    removed.append(name)
                except NetlinkError as exc:
                    if exc.code == errno.ENODEV:
                        continue  # already gone: absence, not a failure
                    failed.append((name, _error_text(exc)))
                except Exception as exc:
                    failed.append((name, _error_text(exc)))
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
    """Check if nodalarc-wiring-status reflects the current manifest.

    Returns True (Case B) if wiring-status exists, matches session and
    generation, every manifest node has all required wiring steps ready, and
    every local row names the exact live pod incarnation in
    ``local_handles`` ({node_id: NamespaceHandle}). A row written for a
    replaced pod or a recreated sandbox fails the binding and forces a
    rewire.
    """
    try:
        cm = v1.read_namespaced_config_map(WIRING_STATUS_CONFIGMAP, namespace)
        if not cm.data:
            return False
        session_id, generation, statuses = parse_status_configmap(cm.data)
        if session_id != manifest.session_id:
            return False
        if generation != manifest.wiring_generation:
            return False
        expected_nodes = set(manifest.nodes.keys())
        if not expected_nodes.issubset(statuses.keys()):
            return False
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
                    "Wiring row for %s names another pod incarnation "
                    "(row pod=%s sandbox=%s netns=%s, live pod=%s sandbox=%s netns=%s) "
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
        return all(statuses[node_id].ready_for(manifest) for node_id in expected_nodes)
    except Exception as exc:
        log.warning("wiring-status validation failed: %s", exc)
        return False


if __name__ == "__main__":
    sys.exit(main())
