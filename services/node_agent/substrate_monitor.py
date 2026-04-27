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

Ref-counting: a node may have 50 VXLAN tunnels to the same peer (50
cross-plane ISLs). We measure the peer once. add_peer increments the
ref count, remove_peer decrements. Measurement stops when ref reaches 0.
"""

from __future__ import annotations

import asyncio
import json
import logging
import subprocess
import threading
from datetime import UTC, datetime

log = logging.getLogger(__name__)

# Active VXLAN peers: remote_ip → ref_count
# Thread-safe: updated from handler threads (ThreadPoolExecutor),
# read from the async monitor task.
_peers: dict[str, int] = {}
_peers_lock = threading.Lock()

# Module-level references for cross-thread communication.
# Set by init() from the async server context.
_nc = None  # NATS connection
_hostname: str = ""
_event_loop = None  # asyncio event loop for scheduling from threads
# Session ID for NATS subject scoping. Set by init().
# The Node Agent doesn't load session config directly — the session_id
# is passed from __main__.py which reads it from the wiring manifest
# or the session ConfigMap. Falls back to "default" if unavailable.
_session_id: str = "default"


def init(nc, hostname: str, event_loop, session_id: str = "default") -> None:
    """Initialize module-level references for cross-thread communication.

    Called once from the async server context before handlers start.
    """
    global _nc, _hostname, _event_loop, _session_id
    _nc = nc
    _hostname = hostname
    _event_loop = event_loop
    _session_id = session_id


def add_peer(remote_ip: str) -> None:
    """Register a VXLAN peer. Triggers immediate measurement on first contact.

    Called from handler threads when VXLAN tunnels are created.
    Thread-safe via _peers_lock. If this is the first tunnel to a new peer,
    schedules an immediate substrate measurement in the async event loop.
    """
    with _peers_lock:
        is_new = remote_ip not in _peers
        _peers[remote_ip] = _peers.get(remote_ip, 0) + 1

    if is_new:
        log.info("New VXLAN peer: %s — scheduling immediate measurement", remote_ip)
        if _event_loop and _nc:
            asyncio.run_coroutine_threadsafe(
                measure_and_publish(_nc, _hostname, remote_ip),
                _event_loop,
            )


def remove_peer(remote_ip: str) -> None:
    """Unregister a VXLAN peer. Removes when ref_count reaches 0.

    Called from handler threads when VXLAN tunnels are destroyed.
    """
    with _peers_lock:
        count = _peers.get(remote_ip, 0) - 1
        if count <= 0:
            _peers.pop(remote_ip, None)
            log.info("Removed VXLAN peer: %s (no more tunnels)", remote_ip)
        else:
            _peers[remote_ip] = count


def get_active_peers() -> list[str]:
    """Return list of active peer IPs."""
    with _peers_lock:
        return list(_peers.keys())


def measure_one(remote_ip: str, count: int = 10) -> float | None:
    """Measure median RTT to a single peer. Blocking. Returns ms or None.

    Uses ICMP ping with 100ms interval. 10 samples ≈ 1 second.
    Computes median to reject outliers.
    """
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
            return None
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
        return median
    except Exception as exc:
        log.warning("Substrate measurement to %s failed: %s", remote_ip, exc)
        return None


async def measure_and_publish(nc, hostname: str, remote_ip: str) -> None:
    """Measure one peer and publish immediately. For first-contact.

    Runs ping in executor (non-blocking asyncio). Publishes result to NATS.
    Called when the first VXLAN tunnel to a new peer is created.
    """
    from nodalarc.nats_channels import substrate_latency_subject

    median = await asyncio.get_running_loop().run_in_executor(None, measure_one, remote_ip)
    if median is not None:
        payload = {
            "source_node": hostname,
            "peers": {remote_ip: round(median, 3)},
            "timestamp": datetime.now(UTC).isoformat(),
        }
        await nc.publish(substrate_latency_subject(_session_id), json.dumps(payload).encode())
        log.info("Published substrate latency: %s → %s = %.3fms", hostname, remote_ip, median)


async def monitor_loop(nc, hostname: str, interval_s: float = 60.0) -> None:
    """Background task: re-measure all active peers periodically.

    Runs forever. Sleeps for interval_s, then measures all active peers
    and publishes results. Catches all exceptions to prevent task death.
    """
    from nodalarc.nats_channels import substrate_latency_subject

    _subj = substrate_latency_subject(_session_id)
    log.info("Substrate monitor started (interval=%.0fs, session_id=%s)", interval_s, _session_id)
    while True:
        try:
            await asyncio.sleep(interval_s)
            peers = get_active_peers()
            if not peers:
                continue
            measurements = {}
            for ip in peers:
                m = await asyncio.get_running_loop().run_in_executor(None, measure_one, ip)
                if m is not None:
                    measurements[ip] = round(m, 3)
            if measurements:
                payload = {
                    "source_node": hostname,
                    "peers": measurements,
                    "timestamp": datetime.now(UTC).isoformat(),
                }
                await nc.publish(_subj, json.dumps(payload).encode())
        except asyncio.CancelledError:
            break
        except Exception as exc:
            log.warning("Substrate monitor error: %s", exc)
