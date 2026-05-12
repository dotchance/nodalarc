# Copyright 2024-2026 .chance (dotchance)
# Licensed under the NodalArc Source Available License 1.0. See LICENSE file.
"""Peer-only substrate latency measurement.

Measures physical network latency between this K8s node and its active
VXLAN peers. Publishes measurements to NATS for Scheduler consumption.

The Node Agent DaemonSet runs with hostNetwork — ICMP ping goes through
the physical network, giving accurate substrate latency measurements.
Median of 10 samples rejects outliers (ARP cold-start, CPU spikes).

Measurement triggers:
1. On first VXLAN tunnel to a new peer (immediate, synchronous)
2. Periodically every 60 seconds (background asyncio task)

Peer-only: only measures to nodes with active VXLAN tunnels. Scales
with O(active_peers) not O(N²). At 10K sats with planePerNode across
100 nodes, each node has ~4-6 peers, not 99.

Peer references are exact and generation fenced. A node may have many VXLAN
tunnels to the same remote IP, but each active reference records the session,
wiring generation, VNI, and local interface that caused measurement. We
measure each remote IP once while at least one exact reference is active.
"""

from __future__ import annotations

import asyncio
import json
import logging
import subprocess
import threading
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta

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

# Module-level references for cross-thread communication.
# Set by init() from the async server context.
_nc = None  # NATS connection
_js = None  # JetStream context
_hostname: str = ""
_event_loop = None  # asyncio event loop for scheduling from threads
# Session ID for NATS subject scoping. Set from the wiring manifest
# by __main__.py before init() is called.
_session_id: str = ""
_wiring_generation: str = ""


def set_identity(session_id: str, wiring_generation: str) -> None:
    """Set current runtime identity before NATS monitor startup."""
    if not session_id:
        raise ValueError("substrate monitor session_id is required")
    if not wiring_generation:
        raise ValueError("substrate monitor wiring_generation is required")
    global _session_id, _wiring_generation
    _session_id = session_id
    _wiring_generation = wiring_generation


def init(nc, hostname: str, event_loop) -> None:
    """Initialize module-level references for cross-thread communication.

    Called once from the async server context before handlers start.
    """
    global _nc, _js, _hostname, _event_loop
    if not _session_id or not _wiring_generation:
        raise ValueError(
            "substrate_monitor identity must be set before init() — wiring manifest not processed?"
        )
    _nc = nc
    _js = nc.jetstream()
    _hostname = hostname
    _event_loop = event_loop


def _active_remote_ips_locked() -> set[str]:
    return {ref.remote_ip for ref in _peer_refs}


def _refs_for_ip(remote_ip: str) -> list[PeerRef]:
    with _peers_lock:
        return sorted(
            (ref for ref in _peer_refs if ref.remote_ip == remote_ip),
            key=lambda ref: (ref.session_id, ref.wiring_generation, ref.vni, ref.local_ifname),
        )


def add_peer_ref(ref: PeerRef) -> None:
    """Register one exact VXLAN peer reference.

    Triggers immediate measurement only when this is the first active
    reference for the remote IP.
    """
    ref.validate()
    if ref.session_id != _session_id or ref.wiring_generation != _wiring_generation:
        raise ValueError(
            "substrate peer ref identity does not match monitor identity: "
            f"ref=({ref.session_id}, {ref.wiring_generation}) "
            f"monitor=({_session_id}, {_wiring_generation})"
        )
    with _peers_lock:
        before = _active_remote_ips_locked()
        _peer_refs.add(ref)
        is_new = ref.remote_ip not in before
    if is_new:
        log.info("New VXLAN peer: %s — scheduling immediate measurement", ref.remote_ip)
        if _event_loop and _nc:
            asyncio.run_coroutine_threadsafe(
                measure_and_publish(_nc, _hostname, ref.remote_ip),
                _event_loop,
            )


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
        log.info("Removed VXLAN peer: %s (no more exact refs)", ref.remote_ip)
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
    global _nc, _js, _hostname, _event_loop, _session_id, _wiring_generation
    with _peers_lock:
        _peer_refs.clear()
    _nc = None
    _js = None
    _hostname = ""
    _event_loop = None
    _session_id = ""
    _wiring_generation = ""


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
                except (IndexError, ValueError):
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


async def measure_and_publish(
    nc,
    hostname: str,
    remote_ip: str,
    *,
    stale_after_s: float = DEFAULT_STALE_AFTER_S,
) -> None:
    """Measure one peer and publish structured evidence.

    Runs ping in executor (non-blocking asyncio). Publishes result to NATS.
    Called when the first VXLAN tunnel to a new peer is created.
    """
    from nodalarc.nats_channels import substrate_latency_subject

    measurement = await asyncio.get_running_loop().run_in_executor(
        None,
        lambda: measure_one_detail(remote_ip, stale_after_s=stale_after_s),
    )
    refs = _refs_for_ip(remote_ip)
    if not refs:
        log.debug("Dropping substrate measurement for inactive peer %s", remote_ip)
        return
    measurement_payload = asdict(measurement)
    measurement_payload["refs"] = [asdict(ref) for ref in refs]
    payload = {
        "source_node": hostname,
        "session_id": _session_id,
        "wiring_generation": _wiring_generation,
        "timestamp": measurement.timestamp,
        "measurements": {remote_ip: measurement_payload},
    }
    try:
        await _js.publish(substrate_latency_subject(_session_id), json.dumps(payload).encode())
    except Exception as exc:
        log.error("Failed to publish substrate latency for %s: %s", remote_ip, exc)
        raise
    if measurement.status == "ok":
        log.info(
            "Published substrate latency: %s -> %s = %.3fms",
            hostname,
            remote_ip,
            measurement.median_rtt_ms,
        )
    else:
        log.warning(
            "Published substrate latency failure: %s -> %s: %s",
            hostname,
            remote_ip,
            measurement.error_message,
        )


async def monitor_loop(nc, hostname: str, interval_s: float = 60.0) -> None:
    """Background task: re-measure all active peers periodically.

    Runs forever. Sleeps for interval_s, then measures all active peers
    and publishes results. Catches all exceptions to prevent task death.
    """
    from nodalarc.nats_channels import substrate_latency_subject

    log.info("Substrate monitor started (interval=%.0fs, session_id=%s)", interval_s, _session_id)
    while True:
        try:
            await asyncio.sleep(interval_s)
            _subj = substrate_latency_subject(_session_id)
            peers = get_active_peers()
            if not peers:
                continue
            published = 0
            stale_after_s = max(interval_s * 2.0, DEFAULT_STALE_AFTER_S)
            for ip in peers:
                await measure_and_publish(nc, hostname, ip, stale_after_s=stale_after_s)
                published += 1
            if published:
                log.debug("Published %d substrate measurement event(s) on %s", published, _subj)
        except asyncio.CancelledError:
            break
        except Exception as exc:
            log.warning("Substrate monitor error: %s", exc)
