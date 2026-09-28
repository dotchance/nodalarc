# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""Scheduler entry point.

Loads session config, builds the interface map and terminal rates, discovers pod
locations, initializes agent pool, and runs the async dispatch loop.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import logging
import os
import signal
import socket
import sys
import time as _time
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from nodal.logging import configure as _configure_logging
from nodalarc.cr_runtime_config import (
    CR_GROUP,
    CR_NAME,
    CR_PLURAL,
    CR_VERSION,
    ConstellationSpecStatus,
)
from nodalarc.models.resolved_session import ResolvedSession
from nodalarc.runtime_service_config import (
    DEFAULT_INSTALLED_SHIPPED_CATALOG_ROOT,
    RuntimeConfigHealth,
    load_mounted_runtime_config,
    start_runtime_health_server,
)
from nodalarc.session_identity import (
    require_resolved_session_run_id,
)
from nodalarc.substrate.manifest_contract import (
    POD_OWNER_UID_LABEL,
    POD_SESSION_RUN_LABEL,
    WIRING_MANIFEST_CONFIGMAP,
    WiringManifest,
    decode_wiring_manifest,
)
from nodalarc.substrate.wiring_status import failed_status_summary, pod_wiring_statuses
from nodalarc.workload_target import NODE_ID_LABEL

from scheduler.agent_pool import AgentPool
from scheduler.dispatcher import Dispatcher, DispatcherSuperseded
from scheduler.pod_locator import PodLocationMap
from scheduler.substrate_latency import (
    load_substrate_status_documents,
    validate_required_substrate_measurements,
)
from scheduler.writer_lease import (
    LEASE_NAME,
    RENEW_DEADLINE_S,
    RENEW_INTERVAL_S,
    STANDBY_RETRY_S,
    WriterLease,
    WriterLeaseConflict,
    WriterLeaseLost,
)

log = logging.getLogger(__name__)


def _routing_protocol_label(resolved: ResolvedSession) -> str:
    """Human log label for one or more routing domains."""
    protocols = tuple(sorted({domain.protocol for domain in resolved.routing_domains}))
    if not protocols:
        return "none"
    if len(protocols) == 1:
        return protocols[0]
    return "multi-domain[" + ",".join(protocols) + "]"


def _dispatch_timing(resolved: ResolvedSession) -> tuple[float, float]:
    """Derive Scheduler timing from catalog-resolved session truth."""
    if resolved.dispatch is None:
        raise RuntimeError("resolved session is missing dispatch configuration")
    if resolved.time is None:
        raise RuntimeError("resolved session is missing time configuration")
    return (
        resolved.dispatch.max_latency_age_ticks * resolved.time.step_seconds,
        resolved.time.compression,
    )


def _scheduler_capacity_maps(
    resolved: ResolvedSession,
) -> tuple[dict[str, int], dict[str, str], dict[str, int]]:
    """Build Scheduler capacity/policy maps from catalog terminal mounts."""
    ground_candidates = resolved.ground_candidate_satellites_by_gs()
    required_ground_ids = set(ground_candidates)
    required_satellite_ids = {sat_id for sats in ground_candidates.values() for sat_id in sats}
    gs_terminal_capacities: dict[str, int] = {}
    gs_handover_modes: dict[str, str] = {}
    sat_ground_terminal_capacities: dict[str, int] = {}
    selected_access = resolved.selected_access_terminals_by_node()

    for node in resolved.nodes:
        access_capacity = sum(
            len(selection.interface_indices) for selection in selected_access.get(node.node_id, ())
        )
        if node.kind == "ground_station":
            if node.node_id not in required_ground_ids:
                continue
            if access_capacity <= 0:
                raise RuntimeError(
                    f"Resolved ground station {node.node_id} has access candidates but no access capacity"
                )
            if node.ground_scheduling is None or node.ground_scheduling.handover_mode is None:
                raise RuntimeError(
                    f"Resolved ground station {node.node_id} is missing handover scheduling"
                )
            gs_terminal_capacities[node.node_id] = access_capacity
            gs_handover_modes[node.node_id] = node.ground_scheduling.handover_mode
        elif node.kind == "satellite":
            sat_capacity = access_capacity
            if node.node_id in required_satellite_ids and sat_capacity <= 0:
                raise RuntimeError(
                    f"Resolved satellite {node.node_id} has access candidates but no access terminal capacity"
                )
            if sat_capacity > 0:
                sat_ground_terminal_capacities[node.node_id] = sat_capacity

    missing_gs = sorted(required_ground_ids - set(gs_terminal_capacities))
    if missing_gs:
        raise RuntimeError(
            f"Ground candidate map references unresolved ground station(s): {missing_gs}"
        )
    missing_sats = sorted(required_satellite_ids - set(sat_ground_terminal_capacities))
    if missing_sats:
        raise RuntimeError(
            f"Ground candidate map references unresolved satellite(s): {missing_sats}"
        )
    return gs_terminal_capacities, gs_handover_modes, sat_ground_terminal_capacities


def _session_wiring_proofs(k8s_v1: Any, namespace: str, manifest: WiringManifest) -> dict:
    """The wiring proof each of this run's session pods carries, by node id."""
    pods = k8s_v1.list_namespaced_pod(
        namespace,
        label_selector=(
            f"{POD_SESSION_RUN_LABEL}={manifest.session_run_id},"
            f"{POD_OWNER_UID_LABEL}={manifest.owner_uid}"
        ),
    )
    return pod_wiring_statuses(pods.items, node_id_label=NODE_ID_LABEL)


def wait_for_wiring_gate(
    *,
    k8s_v1: Any,
    namespace: str,
    manifest: WiringManifest,
    expected_nodes: set[str],
    timeout_s: float = 120.0,
    poll_s: float = 2.0,
    monotonic: Callable[[], float] = _time.monotonic,
    sleep: Callable[[float], None] = _time.sleep,
) -> None:
    """Block Scheduler startup until Node Agent wiring is complete.

    Dispatching before every namespace has its veth/bridge wiring creates a
    topology that can never match OME's authoritative link state. Timeout is a
    hard failure so Kubernetes restarts the Scheduler instead of letting it
    apply links to a partially wired substrate.
    """
    expected_count = len(expected_nodes)
    deadline = monotonic() + timeout_s
    while monotonic() < deadline:
        try:
            statuses = _session_wiring_proofs(k8s_v1, namespace, manifest)
            ready = {node_id for node_id, status in statuses.items() if status.ready_for(manifest)}
            if expected_nodes.issubset(ready):
                log.info("Wiring gate passed: %d/%d nodes ready", len(ready), expected_count)
                return
            current_statuses = {
                node_id: status
                for node_id, status in statuses.items()
                if status.session_id == manifest.session_id
                and status.wiring_generation == manifest.wiring_generation
            }
            failure = failed_status_summary(current_statuses, node_ids=expected_nodes)
            if failure:
                log.error("Wiring gate failed: %s", failure)
                raise RuntimeError(f"Wiring gate failed: {failure}")
            if int(monotonic()) % 10 < 2:
                log.debug("Wiring in progress: %d/%d", len(ready), expected_count)
        except RuntimeError:
            raise
        except Exception as exc:
            status = getattr(exc, "status", None)
            if status != 404:
                log.warning("Wiring status check error: %s", exc)
        sleep(poll_s)

    try:
        statuses = _session_wiring_proofs(k8s_v1, namespace, manifest)
        wired = {node_id for node_id, status in statuses.items() if status.ready_for(manifest)}
    except Exception as exc:
        log.warning("Failed to read wiring status after timeout: %s", exc)
        wired = set()
    missing = sorted(expected_nodes - wired)
    log.error(
        "Wiring gate TIMEOUT after %.0fs: %d/%d wired, %d missing: %s",
        timeout_s,
        len(wired),
        expected_count,
        len(missing),
        ", ".join(missing[:20])
        + (f" ... and {len(missing) - 20} more" if len(missing) > 20 else ""),
    )
    raise RuntimeError(
        f"Wiring gate timeout: {len(wired)}/{expected_count} nodes wired; missing={missing[:20]}"
    )


def wait_for_substrate_gate(
    *,
    k8s_v1: Any,
    namespace: str,
    manifest: WiringManifest,
    timeout_s: float = 120.0,
    poll_s: float = 2.0,
    monotonic: Callable[[], float] = _time.monotonic,
    sleep: Callable[[float], None] = _time.sleep,
):
    """Block Scheduler startup until all required substrate RTTs are proven."""
    if not manifest.required_substrate_pairs:
        log.info("Substrate gate passed: no cross-node substrate pairs required")
        return {}

    deadline = monotonic() + timeout_s
    last_error = ""
    while monotonic() < deadline:
        try:
            documents = load_substrate_status_documents(k8s_v1=k8s_v1, namespace=namespace)
            measurements = validate_required_substrate_measurements(
                required_pairs=manifest.required_substrate_pairs,
                documents_by_source=documents,
                session_id=manifest.session_id,
                wiring_generation=manifest.wiring_generation,
                now=datetime.now(UTC),
            )
            log.info(
                "Substrate gate passed: %d/%d directional measurements ready",
                len(measurements),
                len(manifest.required_substrate_pairs),
            )
            return measurements
        except Exception as exc:
            last_error = str(exc)
            log.debug("Substrate gate waiting: %s", last_error)
        sleep(poll_s)

    raise RuntimeError(
        "Substrate gate timeout: "
        f"{len(manifest.required_substrate_pairs)} directional measurements required; "
        f"last_error={last_error}"
    )


def _make_lifecycle_identity_reader(
    k8s_v1: Any, namespace: str
) -> Callable[[], tuple[str | None, tuple[str, str] | None]]:
    """Authoritative lifecycle reader for the dispatcher's halt classifier.

    Returns (cr_session_run_id, manifest_identity):
    - cr_session_run_id: the Operator-stamped status.sessionRunId on the
      ConstellationSpec. This flips the moment the Operator reconciles a
      new spec — BEFORE old session pods are deleted — so it is the
      earliest authoritative supersession signal. None when the CR is
      absent or carries no stamped identity yet.
    - manifest_identity: the wiring manifest's (session_id,
      wiring_generation); None when the manifest ConfigMap is absent
      (teardown in progress).

    Any other read failure propagates — the dispatcher treats an
    unreadable authority as unproven supersession and keeps the halt
    fatal.
    """
    import kubernetes.client

    custom = kubernetes.client.CustomObjectsApi(k8s_v1.api_client)

    def _read() -> tuple[str | None, tuple[str, str] | None]:
        try:
            cr = custom.get_namespaced_custom_object(
                CR_GROUP, CR_VERSION, namespace, CR_PLURAL, CR_NAME
            )
            cr_run_id = ConstellationSpecStatus.from_cr(cr.get("status")).session_run_id or None
        except kubernetes.client.rest.ApiException as exc:
            if exc.status != 404:
                raise
            cr_run_id = None
        try:
            manifest = read_wiring_manifest_identity(k8s_v1, namespace)
            manifest_identity = (manifest.session_id, manifest.wiring_generation)
        except kubernetes.client.rest.ApiException as exc:
            if exc.status != 404:
                raise
            manifest_identity = None
        return cr_run_id, manifest_identity

    return _read


def read_wiring_manifest_identity(k8s_v1: Any, namespace: str) -> WiringManifest:
    cm = k8s_v1.read_namespaced_config_map(WIRING_MANIFEST_CONFIGMAP, namespace)
    return decode_wiring_manifest(cm.data)


def wait_for_wiring_manifest_identity(
    *,
    k8s_v1: Any,
    namespace: str,
    timeout_s: float = 120.0,
    poll_s: float = 2.0,
    monotonic: Callable[[], float] = _time.monotonic,
    sleep: Callable[[float], None] = _time.sleep,
) -> WiringManifest:
    """Block Scheduler startup until the Operator publishes the wiring manifest.

    Session ConfigMap creation and topology-wiring ConfigMap creation are
    separate Kubernetes writes. A missing ConfigMap is only tolerated during
    that bounded creation window. Malformed manifest content remains an
    immediate fatal error because the Scheduler cannot safely infer substrate
    identity without the exact session/generation contract.
    """
    deadline = monotonic() + timeout_s
    while monotonic() < deadline:
        try:
            return read_wiring_manifest_identity(k8s_v1, namespace)
        except Exception as exc:
            if getattr(exc, "status", None) != 404:
                raise
        sleep(poll_s)
    raise RuntimeError(f"{WIRING_MANIFEST_CONFIGMAP} ConfigMap not found after {timeout_s:.0f}s")


def _build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Nodal Arc Scheduler")
    parser.add_argument("--session", required=True, help="Path to session YAML")
    parser.add_argument(
        "--session-config-dir",
        type=Path,
        help="Directory-mounted runtime session ConfigMap",
    )
    parser.add_argument(
        "--installed-shipped-root",
        type=Path,
        default=DEFAULT_INSTALLED_SHIPPED_CATALOG_ROOT,
        help="Installed read-only nodalarc catalog root",
    )
    parser.add_argument(
        "--platform-config",
        default="configs/platform.yaml",
        help="Path to platform configuration YAML",
    )
    return parser


class _Terminated(BaseException):
    """SIGTERM arrived while no event loop owned signal handling.

    A BaseException, so the startup gate loops (which retry on Exception)
    cannot absorb it.
    """


def _raise_terminated(signum: int, _frame: object) -> None:
    raise _Terminated(signum)


async def _acquire_writer_lease(lease: WriterLease) -> int:
    """Wait until this Scheduler holds the session writer lease; return its epoch."""
    loop = asyncio.get_running_loop()
    standby_logged = False
    while True:
        epoch = await loop.run_in_executor(None, lease.try_acquire)
        if epoch is not None:
            log.info(
                "Holding session writer lease %s as %s: writer epoch %d",
                LEASE_NAME,
                lease.holder,
                epoch,
            )
            return epoch
        if not standby_logged:
            log.info(
                "Session writer lease %s is held by another Scheduler of this session; "
                "waiting as a standby",
                LEASE_NAME,
            )
            standby_logged = True
        await asyncio.sleep(STANDBY_RETRY_S)


async def _keep_writer_lease(lease: WriterLease, dispatcher: Dispatcher) -> str | None:
    """Renew the lease while the dispatcher runs; stop it once the hold is gone.

    Returns why the hold became unproven when the API server stopped
    answering renewals: past the renew deadline another Scheduler may take
    the lease, so this one stops commanding first. Returns None after
    superseding the dispatcher because the lease names another holder.
    """
    import kubernetes.client
    import urllib3

    loop = asyncio.get_running_loop()
    renewed_at = _time.monotonic()
    while True:
        await asyncio.sleep(RENEW_INTERVAL_S)
        try:
            held = await loop.run_in_executor(None, lease.renew)
        except (
            kubernetes.client.rest.ApiException,
            urllib3.exceptions.HTTPError,
            OSError,
            WriterLeaseConflict,
        ) as exc:
            if _time.monotonic() - renewed_at < RENEW_DEADLINE_S:
                log.warning("Writer lease renewal failed: %s", exc)
                continue
            dispatcher.stop()
            return (
                f"Writer lease {LEASE_NAME} not renewed for {RENEW_DEADLINE_S:.0f} s ({exc}); "
                f"this instance ({lease.holder}, writer epoch {lease.epoch}) stopped commanding "
                "and exits for a restart."
            )
        if not held:
            dispatcher.supersede(
                f"Writer lease {LEASE_NAME} now names another Scheduler; this instance "
                f"({lease.holder}, writer epoch {lease.epoch}) no longer commands the session. "
                "Shutting down cleanly."
            )
            return None
        renewed_at = _time.monotonic()


def _release_writer_lease(lease: WriterLease) -> None:
    """Hand the lease on at once. A failed release costs a successor one lease duration."""
    import kubernetes.client
    import urllib3

    try:
        lease.release()
    except (
        kubernetes.client.rest.ApiException,
        urllib3.exceptions.HTTPError,
        OSError,
        WriterLeaseConflict,
    ) as exc:
        log.warning("Writer lease release failed (%s); it expires on its own", exc)


async def _serve(
    lease: WriterLease,
    build_dispatcher: Callable[[int], Dispatcher],
    *,
    commanding: Callable[[], None],
) -> None:
    """Take the writer lease, then run the dispatcher with SIGTERM routed to its orderly stop.

    ``commanding`` is called once the lease is held, before the first command.
    Raises WriterLeaseLost when the hold became unproven while the dispatcher ran.
    """
    loop = asyncio.get_running_loop()
    epoch = await _acquire_writer_lease(lease)
    lost: str | None = None
    try:
        dispatcher = build_dispatcher(epoch)
        commanding()
        loop.add_signal_handler(signal.SIGTERM, dispatcher.stop)
        keeper = asyncio.create_task(_keep_writer_lease(lease, dispatcher))
        try:
            await dispatcher.run()
        finally:
            if not keeper.done():
                keeper.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                lost = await keeper
    finally:
        await loop.run_in_executor(None, _release_writer_lease, lease)
    if lost is not None:
        raise WriterLeaseLost(lost)


def main() -> None:
    _configure_logging("nodal.arc.scheduler", nats_level=logging.INFO)
    # Kubernetes stops a pod with SIGTERM, and the kernel does not deliver a
    # signal to a PID namespace's init process unless it installed a handler.
    # Without this the Scheduler ignored SIGTERM and its pod lived out the full
    # termination grace period.
    signal.signal(signal.SIGTERM, _raise_terminated)
    try:
        _run_scheduler()
    except _Terminated:
        log.info("Scheduler stopped by SIGTERM")


def _run_scheduler() -> None:
    args = _build_argument_parser().parse_args()

    from nodalarc.platform_config import get_platform_config, init_platform_config

    init_platform_config(Path(args.platform_config))

    session_file = Path(args.session)
    session_config_dir = args.session_config_dir or session_file.parent
    pod_uid = os.environ["POD_UID"]
    release = os.environ["NODALARC_RELEASE"]
    build = os.environ["NODAL_BUILD"]
    runtime_health = RuntimeConfigHealth(session_config_dir, pod_uid=pod_uid)
    start_runtime_health_server(runtime_health)
    runtime_config = load_mounted_runtime_config(
        config_directory=session_config_dir,
        installed_shipped_root=args.installed_shipped_root,
        origin="scheduler",
        namespace=os.environ.get("POD_NAMESPACE"),
        pod_uid=pod_uid,
        release=release,
        build=build,
        log=log,
    )
    resolved = runtime_config.config.resolution.resolved
    # A loaded Scheduler is not ready until it holds the writer lease: a
    # standby dispatches nothing.
    runtime_health.mark_loaded(runtime_config, waiting_for=f"the session writer lease {LEASE_NAME}")
    interface_map = resolved.link_interface_map()
    interface_rates = resolved.interface_terminal_rates()
    log.debug("Interface map: %d link pairs", len(interface_map))
    session_id = require_resolved_session_run_id(resolved)
    expected_nodes = set(resolved.node_ids())

    # Pod location map — canonical node IDs from K8s labels
    loc = PodLocationMap()
    loc.load_from_k8s_api(
        namespace=get_platform_config().kubernetes_namespace,
        expected_node_ids=expected_nodes,
        session_id=session_id,
    )
    log.debug("Pod locations:\n%s", loc.summary())

    # --- Wiring gate: wait for Node Agent to complete wiring ---
    # The Scheduler must NOT dispatch OME events until wiring is done.
    # Signal: every session pod carries ready wiring proof for this manifest.
    # K8s config already loaded by loc.load_from_k8s_api() above.
    import kubernetes.client

    k8s_v1 = kubernetes.client.CoreV1Api()
    ns = get_platform_config().kubernetes_namespace
    wiring_manifest = wait_for_wiring_manifest_identity(k8s_v1=k8s_v1, namespace=ns)
    if wiring_manifest.session_id != session_id:
        raise RuntimeError(
            "Wiring manifest session mismatch: "
            f"manifest={wiring_manifest.session_id!r} scheduler={session_id!r}"
        )
    log.debug(
        "Wiring gate: waiting for %d nodes generation=%s",
        len(expected_nodes),
        wiring_manifest.wiring_generation,
    )
    wait_for_wiring_gate(
        k8s_v1=k8s_v1,
        namespace=ns,
        manifest=wiring_manifest,
        expected_nodes=expected_nodes,
    )
    substrate_measurements = wait_for_substrate_gate(
        k8s_v1=k8s_v1,
        namespace=ns,
        manifest=wiring_manifest,
    )

    # Agent pool
    pool = AgentPool()

    gs_terminal_capacities, gs_handover_modes, sat_ground_terminal_capacities = (
        _scheduler_capacity_maps(resolved)
    )
    max_latency_age_s, compression_factor = _dispatch_timing(resolved)
    mbb_dispatch = any(mode == "mbb" for mode in gs_handover_modes.values())
    log.info(
        "Ground handover by station: %s (protocol=%s)",
        ", ".join(f"{gs}={mode}" for gs, mode in sorted(gs_handover_modes.items())),
        _routing_protocol_label(resolved),
    )

    from nodal.logging import set_session

    set_session(session_id)
    log.info(
        "Scheduler starting [build=%s, session_id=%s, link_pairs=%d, nodes=%d, mbb=%s]",
        os.environ.get("NODAL_BUILD", "dev"),
        session_id,
        len(interface_map),
        len(loc.node_ids),
        mbb_dispatch,
    )

    writer_lease = WriterLease(
        kubernetes.client.CoordinationV1Api(),
        ns,
        session_id=session_id,
        instance=f"{socket.gethostname()}/{uuid.uuid4().hex[:12]}",
    )

    def _build_dispatcher(writer_epoch: int) -> Dispatcher:
        return Dispatcher(
            interface_map=interface_map,
            interface_rates=interface_rates,
            pod_locator=loc,
            agent_pool=pool,
            max_latency_age_s=max_latency_age_s,
            compression_factor=compression_factor,
            gs_terminal_capacities=gs_terminal_capacities,
            gs_handover_modes=gs_handover_modes,
            sat_ground_terminal_capacities=sat_ground_terminal_capacities,
            mbb_dispatch=mbb_dispatch,
            # Substrate compensation policy: half the measured RTT is the one-way
            # bound. The dispatcher rejects any other policy; this is the single
            # declared value, not a fallback.
            rtt_to_one_way_policy="half-rtt",
            clean_kernel_audit_interval_s=get_platform_config().scheduler_clean_kernel_audit_interval_s,
            session_id=session_id,
            wiring_generation=wiring_manifest.wiring_generation,
            required_substrate_pairs=wiring_manifest.required_substrate_pairs,
            substrate_measurements=substrate_measurements,
            read_lifecycle_identity=_make_lifecycle_identity_reader(k8s_v1, ns),
            writer_epoch=writer_epoch,
        )

    try:
        asyncio.run(_serve(writer_lease, _build_dispatcher, commanding=runtime_health.mark_serving))
    except WriterLeaseLost as exc:
        # The hold is unproven, not taken: the session has not moved on. The
        # kubelet restarts this container, and the new process takes the lease
        # at a higher epoch once no other holder renews it.
        log.error("%s", exc)
        sys.exit(1)
    except DispatcherSuperseded as exc:
        # The wiring authority moved to another session or generation. This
        # process served one session and never serves another: it stays up,
        # reports not ready, and dispatches nothing until the Operator
        # replaces the pod. Exiting would let the kubelet restart the
        # container in place, where it would load whatever session is
        # mounted next.
        log.info("%s", exc)
        runtime_health.mark_superseded(str(exc))
        # Closing the event loop restored the default SIGTERM disposition.
        signal.signal(signal.SIGTERM, _raise_terminated)
        while True:
            signal.pause()
    except KeyboardInterrupt:
        log.info("Scheduler interrupted")
    finally:
        pool.close()


if __name__ == "__main__":
    main()
