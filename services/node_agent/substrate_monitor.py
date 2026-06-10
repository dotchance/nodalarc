# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""Manifest-driven substrate latency measurement.

The Node Agent DaemonSet runs with hostNetwork, so ICMP ping measures RTT over
the physical Kubernetes-node network. Required measurements are declared in the
wiring manifest, written as durable ConfigMaps, and validated by the Scheduler
before dispatch. Missing, stale, failed, or generation-mismatched substrate
evidence is a control-plane fault.

VXLAN peer references remain as exact lifecycle diagnostics. They do not drive
measurement and are not dispatch authority.
"""

from __future__ import annotations

import asyncio
import logging
import os
import subprocess
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import kubernetes.client
from nodalarc.substrate.manifest_contract import WiringManifest
from nodalarc.substrate.measurement_contract import (
    RequiredSubstratePair,
    SubstrateMeasurement,
    SubstrateStatusDocument,
    decode_status_configmap_data,
    status_document_configmap_data,
    substrate_status_configmap_name,
    substrate_status_labels,
)

log = logging.getLogger(__name__)

DEFAULT_STALE_AFTER_S = 120.0


@dataclass(frozen=True, slots=True)
class PeerRef:
    """Exact substrate peer reference owned by one VXLAN endpoint."""

    session_id: str
    wiring_generation: str
    remote_ip: str
    vni: int
    local_ifname: str

    def validate(self) -> None:
        missing = [
            name
            for name, value in {
                "session_id": self.session_id,
                "wiring_generation": self.wiring_generation,
                "remote_ip": self.remote_ip,
                "local_ifname": self.local_ifname,
            }.items()
            if not value
        ]
        if missing:
            raise ValueError(f"substrate peer ref missing required fields: {', '.join(missing)}")
        if self.vni <= 0:
            raise ValueError("substrate peer ref vni must be > 0")


@dataclass(frozen=True, slots=True)
class MeasurementResult:
    """One physical RTT measurement attempt for a remote peer."""

    remote_ip: str
    timestamp: str
    stale_after: str
    status: str
    sample_count: int
    success_count: int
    median_rtt_ms: float | None
    min_rtt_ms: float | None
    max_rtt_ms: float | None
    error_message: str = ""


# Active VXLAN peer refs. Thread-safe: updated from handler threads
# (ThreadPoolExecutor), read from the async monitor task.
_peer_refs: set[PeerRef] = set()
_peers_lock = threading.Lock()

# Module-level runtime state.
_hostname: str = ""
# Session ID for NATS subject scoping. Set from the wiring manifest
# by __main__.py before init() is called.
_session_id: str = ""
_wiring_generation: str = ""
_required_pairs: list[RequiredSubstratePair] = []
_latest_status: SubstrateStatusDocument | None = None
_k8s_v1: kubernetes.client.CoreV1Api | None = None
_namespace: str = ""
_current_manifest: WiringManifest | None = None
_measure_lock = threading.Lock()
_state_lock = threading.RLock()


def set_identity(session_id: str, wiring_generation: str) -> None:
    """Set current runtime identity before NATS monitor startup."""
    if not session_id:
        raise ValueError("substrate monitor session_id is required")
    if not wiring_generation:
        raise ValueError("substrate monitor wiring_generation is required")
    global _session_id, _wiring_generation, _required_pairs, _latest_status, _current_manifest
    with _state_lock:
        changed = (session_id, wiring_generation) != (_session_id, _wiring_generation)
        _session_id = session_id
        _wiring_generation = wiring_generation
        if changed:
            _required_pairs = []
            _latest_status = None
            _current_manifest = None
            with _peers_lock:
                _peer_refs.clear()


def init(hostname: str) -> None:
    """Initialize module-level runtime identity after wiring has completed."""
    global _hostname
    if not _session_id or not _wiring_generation:
        raise ValueError(
            "substrate_monitor identity must be set before init() — wiring manifest not processed?"
        )
    _hostname = hostname


def _active_remote_ips_locked() -> set[str]:
    return {ref.remote_ip for ref in _peer_refs}


def add_peer_ref(ref: PeerRef) -> None:
    """Register one exact VXLAN peer reference for diagnostics."""
    ref.validate()
    with _state_lock:
        current_session_id = _session_id
        current_wiring_generation = _wiring_generation
        if (
            ref.session_id != current_session_id
            or ref.wiring_generation != current_wiring_generation
        ):
            raise ValueError(
                "substrate peer ref identity does not match monitor identity: "
                f"ref=({ref.session_id}, {ref.wiring_generation}) "
                f"monitor=({current_session_id}, {current_wiring_generation})"
            )
    with _peers_lock:
        before = _active_remote_ips_locked()
        _peer_refs.add(ref)
        is_new = ref.remote_ip not in before
    if is_new:
        log.debug("New VXLAN peer diagnostic ref: %s", ref.remote_ip)


def remove_peer_ref(ref: PeerRef) -> bool:
    """Remove one exact peer reference.

    Returns True when the reference existed. Measurement for the remote IP
    stops once its last exact reference is removed.
    """
    ref.validate()
    with _peers_lock:
        existed = ref in _peer_refs
        _peer_refs.discard(ref)
        still_active = ref.remote_ip in _active_remote_ips_locked()
    if existed and not still_active:
        log.debug("Removed VXLAN peer: %s (no more exact refs)", ref.remote_ip)
    if not existed:
        log.warning("Substrate peer ref was not active during removal: %s", ref)
    return existed


def get_active_peers() -> list[str]:
    """Return list of active peer IPs."""
    with _peers_lock:
        return sorted(_active_remote_ips_locked())


def get_active_refs() -> list[PeerRef]:
    """Return active exact peer refs for tests and diagnostics."""
    with _peers_lock:
        return sorted(
            _peer_refs,
            key=lambda ref: (
                ref.remote_ip,
                ref.session_id,
                ref.wiring_generation,
                ref.vni,
                ref.local_ifname,
            ),
        )


def _reset_for_tests() -> None:
    """Reset module state for isolated unit tests."""
    global _hostname, _session_id, _wiring_generation
    global _required_pairs, _latest_status, _k8s_v1, _namespace, _current_manifest
    with _state_lock:
        with _peers_lock:
            _peer_refs.clear()
        _hostname = ""
        _session_id = ""
        _wiring_generation = ""
        _required_pairs = []
        _latest_status = None
        _k8s_v1 = None
        _namespace = ""
        _current_manifest = None


def measure_one_detail(
    remote_ip: str,
    count: int = 10,
    *,
    stale_after_s: float = DEFAULT_STALE_AFTER_S,
) -> MeasurementResult:
    """Measure RTT to a single peer. Blocking, with structured evidence.

    Uses ICMP ping with 100ms interval. 10 samples ≈ 1 second.
    Computes median to reject outliers.
    """
    measured_at = datetime.now(UTC)
    stale_after = measured_at + timedelta(seconds=stale_after_s)
    timestamp = measured_at.isoformat()
    stale_after_iso = stale_after.isoformat()
    try:
        out = subprocess.run(
            ["ping", "-c", str(count), "-i", "0.1", "-W", "1", remote_ip],
            capture_output=True,
            text=True,
            timeout=15,
        )
        rtts = []
        for line in out.stdout.splitlines():
            if "time=" in line:
                try:
                    t = float(line.split("time=")[1].split()[0])
                    rtts.append(t)
                except IndexError, ValueError:
                    pass
        if not rtts:
            log.warning("No RTT samples from ping to %s", remote_ip)
            return MeasurementResult(
                remote_ip=remote_ip,
                timestamp=timestamp,
                stale_after=stale_after_iso,
                status="failed",
                sample_count=count,
                success_count=0,
                median_rtt_ms=None,
                min_rtt_ms=None,
                max_rtt_ms=None,
                error_message="no RTT samples",
            )
        rtts.sort()
        median = rtts[len(rtts) // 2]
        log.info(
            "Substrate to %s: median=%.3fms (min=%.3f, max=%.3f, n=%d)",
            remote_ip,
            median,
            rtts[0],
            rtts[-1],
            len(rtts),
        )
        return MeasurementResult(
            remote_ip=remote_ip,
            timestamp=timestamp,
            stale_after=stale_after_iso,
            status="ok",
            sample_count=count,
            success_count=len(rtts),
            median_rtt_ms=median,
            min_rtt_ms=rtts[0],
            max_rtt_ms=rtts[-1],
        )
    except Exception as exc:
        log.warning("Substrate measurement to %s failed: %s", remote_ip, exc)
        return MeasurementResult(
            remote_ip=remote_ip,
            timestamp=timestamp,
            stale_after=stale_after_iso,
            status="failed",
            sample_count=count,
            success_count=0,
            median_rtt_ms=None,
            min_rtt_ms=None,
            max_rtt_ms=None,
            error_message=str(exc),
        )


def measure_one(remote_ip: str, count: int = 10) -> float | None:
    """Measure median RTT to a single peer. Blocking. Returns ms or None."""
    return measure_one_detail(remote_ip, count).median_rtt_ms


def measure_required_pair(
    pair: RequiredSubstratePair,
    *,
    count: int = 10,
    stale_after_s: float = DEFAULT_STALE_AFTER_S,
    session_id: str | None = None,
    wiring_generation: str | None = None,
) -> SubstrateMeasurement:
    """Measure one manifest-required substrate pair from this host."""
    result = measure_one_detail(pair.target_ip, count=count, stale_after_s=stale_after_s)
    return SubstrateMeasurement(
        session_id=session_id or _session_id,
        wiring_generation=wiring_generation or _wiring_generation,
        source_node=pair.source_node,
        source_ip=pair.source_ip,
        target_node=pair.target_node,
        target_ip=pair.target_ip,
        measured_at=datetime.fromisoformat(result.timestamp),
        stale_after=datetime.fromisoformat(result.stale_after),
        status=result.status,
        sample_count=result.sample_count,
        success_count=result.success_count,
        median_rtt_ms=result.median_rtt_ms,
        min_rtt_ms=result.min_rtt_ms,
        max_rtt_ms=result.max_rtt_ms,
        error_message=result.error_message,
    )


MeasurePairFn = Callable[[RequiredSubstratePair], SubstrateMeasurement]


def _write_status_document(
    v1: kubernetes.client.CoreV1Api,
    namespace: str,
    document: SubstrateStatusDocument,
) -> None:
    """Create or replace this source node's durable substrate status document."""
    name = substrate_status_configmap_name(document.source_node)
    body = kubernetes.client.V1ConfigMap(
        metadata=kubernetes.client.V1ObjectMeta(
            name=name,
            namespace=namespace,
            labels=substrate_status_labels(),
        ),
        data=status_document_configmap_data(document),
    )
    try:
        v1.create_namespaced_config_map(namespace, body)
    except kubernetes.client.rest.ApiException as exc:
        if exc.status == 409:
            v1.replace_namespaced_config_map(name, namespace, body)
        else:
            raise


def _local_required_pairs(hostname: str, manifest: WiringManifest) -> list[RequiredSubstratePair]:
    return [pair for pair in manifest.required_substrate_pairs if pair.source_node == hostname]


def configure_required_measurements(
    *,
    v1: kubernetes.client.CoreV1Api,
    namespace: str,
    hostname: str,
    manifest: WiringManifest,
    measure_fn: MeasurePairFn | None = None,
) -> SubstrateStatusDocument | None:
    """Measure and publish durable status for manifest-required local pairs.

    This runs before the Node Agent subscribes to command requests. A failed
    measurement is still written as evidence, then surfaced as a fatal startup
    condition so the Scheduler cannot dispatch into unknown substrate truth.
    """
    global _required_pairs, _latest_status, _k8s_v1, _namespace, _hostname, _current_manifest
    with _state_lock:
        if manifest.session_id != _session_id or manifest.wiring_generation != _wiring_generation:
            raise ValueError(
                "substrate monitor identity does not match manifest: "
                f"monitor=({_session_id}, {_wiring_generation}) "
                f"manifest=({manifest.session_id}, {manifest.wiring_generation})"
            )

        pairs = _local_required_pairs(hostname, manifest)
        _required_pairs = list(pairs)
        _k8s_v1 = v1
        _namespace = namespace
        _hostname = hostname
        _current_manifest = manifest

    if not pairs:
        with _state_lock:
            if (
                manifest.session_id == _session_id
                and manifest.wiring_generation == _wiring_generation
            ):
                _latest_status = None
        log.info("No required substrate measurements for %s", hostname)
        return None

    host_ip = os.environ.get("HOST_IP", "").strip()
    source_ips = {pair.source_ip for pair in pairs}
    if len(source_ips) != 1:
        raise RuntimeError(
            "required substrate pairs for this node contain multiple source IPs: "
            + ", ".join(sorted(source_ips))
        )
    expected_host_ip = next(iter(source_ips))
    if host_ip != expected_host_ip:
        raise RuntimeError(
            f"HOST_IP mismatch for substrate measurement: env={host_ip!r} "
            f"manifest={expected_host_ip!r} node={hostname}"
        )

    if measure_fn is None:

        def measurement_fn(pair: RequiredSubstratePair) -> SubstrateMeasurement:
            return measure_required_pair(
                pair,
                session_id=manifest.session_id,
                wiring_generation=manifest.wiring_generation,
            )

    else:
        measurement_fn = measure_fn

    with _measure_lock:
        measurements = {
            pair.target_node: measurement_fn(pair)
            for pair in sorted(pairs, key=lambda required: required.directional_key)
        }
        document = SubstrateStatusDocument(
            session_id=manifest.session_id,
            wiring_generation=manifest.wiring_generation,
            source_node=hostname,
            measurements=measurements,
        )
    with _state_lock:
        if manifest.session_id != _session_id or manifest.wiring_generation != _wiring_generation:
            log.info(
                "Discarding substrate measurements for superseded identity: "
                "measured=(%s, %s) current=(%s, %s)",
                manifest.session_id,
                manifest.wiring_generation,
                _session_id,
                _wiring_generation,
            )
            return None
        _write_status_document(v1, namespace, document)
        _latest_status = document

    validation_now = datetime.now(UTC)
    validation_errors: list[str] = []
    for pair in sorted(pairs, key=lambda required: required.directional_key):
        measurement = measurements[pair.target_node]
        try:
            measurement.rtt_ms(
                now=validation_now,
                session_id=manifest.session_id,
                wiring_generation=manifest.wiring_generation,
                source_node=pair.source_node,
                source_ip=pair.source_ip,
                target_node=pair.target_node,
                target_ip=pair.target_ip,
            )
        except ValueError as exc:
            validation_errors.append(f"{pair.directional_key}: {exc}")
    if validation_errors:
        raise RuntimeError(
            "required substrate measurements failed for " + "; ".join(sorted(validation_errors))
        )
    log.info("Wrote substrate status for %s (%d targets)", hostname, len(measurements))
    return document


def latest_status_document() -> SubstrateStatusDocument | None:
    with _state_lock:
        return _latest_status


def read_local_status_document(
    v1: kubernetes.client.CoreV1Api,
    namespace: str,
    source_node: str,
) -> SubstrateStatusDocument:
    cm = v1.read_namespaced_config_map(substrate_status_configmap_name(source_node), namespace)
    return decode_status_configmap_data(cm.data)


def require_fresh_measurement_for_remote_ip(remote_ip: str) -> None:
    """Fail unless local durable status proves a fresh RTT to ``remote_ip``."""
    if not remote_ip:
        raise ValueError("remote_ip is required for substrate measurement verification")
    with _state_lock:
        document = _latest_status
        session_id = _session_id
        wiring_generation = _wiring_generation
    if document is None:
        raise RuntimeError("local substrate status has not been measured")
    host_ip = os.environ.get("HOST_IP", "").strip()
    matches = [
        measurement
        for measurement in document.measurements.values()
        if measurement.target_ip == remote_ip
    ]
    if not matches:
        raise RuntimeError(f"no local substrate measurement for remote IP {remote_ip}")
    measurement = matches[0]
    measurement.rtt_ms(
        now=datetime.now(UTC),
        session_id=session_id,
        wiring_generation=wiring_generation,
        source_node=document.source_node,
        source_ip=host_ip,
        target_node=measurement.target_node,
        target_ip=remote_ip,
    )


async def monitor_loop(hostname: str, interval_s: float = 60.0) -> None:
    """Background task: refresh manifest-required substrate measurements."""

    log.info(
        "Substrate monitor started (interval=%.0fs, session_id=%s, required_pairs=%d)",
        interval_s,
        _session_id,
        len(_required_pairs),
    )
    while True:
        await asyncio.sleep(interval_s)
        with _state_lock:
            has_required_pairs = bool(_required_pairs)
            k8s_v1 = _k8s_v1
            namespace = _namespace
            current_manifest = _current_manifest
        if not has_required_pairs:
            continue
        if k8s_v1 is None or not namespace:
            raise RuntimeError("substrate monitor missing Kubernetes status writer")
        if current_manifest is None:
            raise RuntimeError("substrate monitor missing current wiring manifest")
        stale_after_s = max(interval_s * 2.0, DEFAULT_STALE_AFTER_S)

        def _measure(
            pair: RequiredSubstratePair,
            *,
            _stale_after_s: float = stale_after_s,
            _session_id: str = current_manifest.session_id,
            _wiring_generation: str = current_manifest.wiring_generation,
        ) -> SubstrateMeasurement:
            return measure_required_pair(
                pair,
                stale_after_s=_stale_after_s,
                session_id=_session_id,
                wiring_generation=_wiring_generation,
            )

        await asyncio.get_running_loop().run_in_executor(
            None,
            lambda _v1=k8s_v1, _namespace=namespace, _hostname=hostname, _manifest=current_manifest, _measure_fn=_measure: (
                configure_required_measurements(
                    v1=_v1,
                    namespace=_namespace,
                    hostname=_hostname,
                    manifest=_manifest,
                    measure_fn=_measure_fn,
                )
            ),
        )
