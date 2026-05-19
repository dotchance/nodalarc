# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""Probe daemon — lightweight custom UDP probe sidecar for ground station pods.

REST API on port 9100. Sends timestamped UDP probe packets with sequence
numbering to measure end-to-end reachability through the constellation.
Lightweight UDP probe with sequence numbering; runs as sidecar in ground
station pods.

Run: python -m measurement.probe_daemon
     uvicorn measurement.probe_daemon:app --host 0.0.0.0 --port 9100
"""

from __future__ import annotations

import logging
import socket
import struct
import threading
import time

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

log = logging.getLogger(__name__)

from contextlib import asynccontextmanager


@asynccontextmanager
async def _lifespan(app: FastAPI):
    _start_echo_server()
    yield
    _echo_stop.set()


app = FastAPI(title="Nodal Arc Probe Daemon", version="1.0", lifespan=_lifespan)

# UDP probe packet format: 8-byte sequence number + 8-byte send timestamp (microseconds)
# Total: 16 bytes per probe packet
from nodalarc.platform_config import get_platform_config

PROBE_PACKET_FMT = "!Qq"  # network byte order: unsigned long long + signed long long
PROBE_PACKET_SIZE = struct.calcsize(PROBE_PACKET_FMT)


# --- Request/Response Models ---


class FlowConfig(BaseModel):
    """Probe flow configuration."""

    flow_id: str
    dst_ip: str
    protocol: str = "udp"
    bandwidth_kbps: float = 100
    probe_type: str = "continuous"  # "continuous" or "burst"
    interval_ms: int = 1000


class FlowStatus(BaseModel):
    """Status of a probe flow."""

    flow_id: str
    dst_ip: str
    probe_type: str
    active: bool
    packets_sent: int
    packets_received: int


class ProbeResults(BaseModel):
    """Accumulated probe results."""

    flow_id: str
    packets_sent: int
    packets_received: int
    latency_min_ms: float
    latency_max_ms: float
    latency_avg_ms: float
    jitter_ms: float


class BurstRequest(BaseModel):
    """Request for a synchronous probe burst."""

    count: int = 10
    interval_ms: int = 200


# --- Flow State ---


class _FlowState:
    """Internal state for a running probe flow."""

    def __init__(self, config: FlowConfig) -> None:
        self.config = config
        self.active = True
        self.packets_sent = 0
        self.packets_received = 0
        self.latencies: list[float] = []
        self.thread: threading.Thread | None = None
        self._stop = threading.Event()

    def stop(self) -> None:
        self._stop.set()
        self.active = False

    def drain_results(self) -> ProbeResults:
        """Drain accumulated results and reset counters."""
        sent = self.packets_sent
        recv = self.packets_received
        lats = list(self.latencies)
        self.packets_sent = 0
        self.packets_received = 0
        self.latencies.clear()

        if not lats:
            return ProbeResults(
                flow_id=self.config.flow_id,
                packets_sent=sent,
                packets_received=recv,
                latency_min_ms=0.0,
                latency_max_ms=0.0,
                latency_avg_ms=0.0,
                jitter_ms=0.0,
            )

        avg = sum(lats) / len(lats)
        jitter = 0.0
        if len(lats) > 1:
            jitter = sum(abs(lats[i] - lats[i - 1]) for i in range(1, len(lats))) / (len(lats) - 1)

        return ProbeResults(
            flow_id=self.config.flow_id,
            packets_sent=sent,
            packets_received=recv,
            latency_min_ms=min(lats),
            latency_max_ms=max(lats),
            latency_avg_ms=avg,
            jitter_ms=jitter,
        )


# Global flow registry
_flows: dict[str, _FlowState] = {}
_lock = threading.Lock()


# --- UDP probe sender/receiver ---


def _send_udp_probe(
    dst_ip: str,
    dst_port: int,
    seq: int,
    sock: socket.socket,
) -> float:
    """Send a single UDP probe packet. Returns send timestamp in microseconds."""
    send_ts = int(time.time() * 1_000_000)
    packet = struct.pack(PROBE_PACKET_FMT, seq, send_ts)
    try:
        sock.sendto(packet, (dst_ip, dst_port))
    except OSError as exc:
        log.debug(f"UDP send failed to {dst_ip}:{dst_port}: {exc}")
    return send_ts / 1_000_000


def _run_continuous_probe(flow_id: str) -> None:
    """Background thread for continuous UDP probe."""
    with _lock:
        state = _flows.get(flow_id)
        if state is None:
            return

    interval_s = max(0.1, state.config.interval_ms / 1000.0)
    dst_ip = state.config.dst_ip
    dst_port = get_platform_config().probe_daemon_udp_data_port

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(interval_s * 2)

    seq = 0
    try:
        while not state._stop.is_set():
            send_ts = time.monotonic()
            packet = struct.pack(PROBE_PACKET_FMT, seq, int(time.time() * 1_000_000))
            try:
                sock.sendto(packet, (dst_ip, dst_port))
                state.packets_sent += 1
            except OSError:
                state.packets_sent += 1
                seq += 1
                state._stop.wait(interval_s)
                continue

            # Wait for echo reply (non-blocking with timeout)
            try:
                data, addr = sock.recvfrom(PROBE_PACKET_SIZE + 64)
                recv_ts = time.monotonic()
                if len(data) >= PROBE_PACKET_SIZE:
                    reply_seq, reply_send_ts = struct.unpack(
                        PROBE_PACKET_FMT, data[:PROBE_PACKET_SIZE]
                    )
                    rtt_ms = (recv_ts - send_ts) * 1000
                    state.packets_received += 1
                    state.latencies.append(rtt_ms)
            except TimeoutError:
                pass  # No reply — packet lost

            seq += 1
            state._stop.wait(interval_s)
    except Exception as exc:
        log.warning(f"Continuous probe for {flow_id} failed: {exc}")
    finally:
        sock.close()


def _run_burst_probe(
    dst_ip: str,
    count: int,
    interval_ms: int,
) -> ProbeResults:
    """Run a synchronous burst of N UDP probe packets."""
    interval_s = max(0.05, interval_ms / 1000.0)
    dst_port = get_platform_config().probe_daemon_udp_data_port

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(interval_s * 3)

    sent = 0
    received = 0
    latencies: list[float] = []

    try:
        for seq in range(count):
            send_ts = time.monotonic()
            packet = struct.pack(PROBE_PACKET_FMT, seq, int(time.time() * 1_000_000))
            try:
                sock.sendto(packet, (dst_ip, dst_port))
                sent += 1
            except OSError:
                sent += 1
                time.sleep(interval_s)
                continue

            try:
                data, addr = sock.recvfrom(PROBE_PACKET_SIZE + 64)
                recv_ts = time.monotonic()
                if len(data) >= PROBE_PACKET_SIZE:
                    rtt_ms = (recv_ts - send_ts) * 1000
                    received += 1
                    latencies.append(rtt_ms)
            except TimeoutError:
                pass

            if seq < count - 1:
                time.sleep(interval_s)
    finally:
        sock.close()

    if not latencies:
        return ProbeResults(
            flow_id="burst",
            packets_sent=sent,
            packets_received=received,
            latency_min_ms=0.0,
            latency_max_ms=0.0,
            latency_avg_ms=0.0,
            jitter_ms=0.0,
        )

    avg = sum(latencies) / len(latencies)
    jitter = 0.0
    if len(latencies) > 1:
        jitter = sum(abs(latencies[i] - latencies[i - 1]) for i in range(1, len(latencies))) / (
            len(latencies) - 1
        )

    return ProbeResults(
        flow_id="burst",
        packets_sent=sent,
        packets_received=received,
        latency_min_ms=min(latencies),
        latency_max_ms=max(latencies),
        latency_avg_ms=avg,
        jitter_ms=jitter,
    )


# --- UDP echo receiver (runs in background) ---

_echo_thread: threading.Thread | None = None
_echo_stop = threading.Event()


def _udp_echo_server() -> None:
    """Background thread: echo UDP probe packets back to sender."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    udp_port = get_platform_config().probe_daemon_udp_data_port
    sock.bind(("0.0.0.0", udp_port))
    sock.settimeout(1.0)
    log.info(f"UDP echo server listening on port {udp_port}")

    while not _echo_stop.is_set():
        try:
            data, addr = sock.recvfrom(PROBE_PACKET_SIZE + 64)
            sock.sendto(data, addr)
        except TimeoutError:
            continue
        except Exception as exc:
            if not _echo_stop.is_set():
                log.warning(f"Echo server error: {exc}")

    sock.close()


def _start_echo_server() -> None:
    """Start the UDP echo server if not already running."""
    global _echo_thread
    if _echo_thread is not None and _echo_thread.is_alive():
        return
    _echo_stop.clear()
    _echo_thread = threading.Thread(
        target=_udp_echo_server,
        daemon=True,
        name="probe-echo",
    )
    _echo_thread.start()


# --- REST Endpoints ---


@app.post("/flows", status_code=201)
def create_flow(config: FlowConfig) -> dict[str, str]:
    """Configure a new probe flow."""
    with _lock:
        if config.flow_id in _flows:
            raise HTTPException(status_code=409, detail=f"Flow {config.flow_id} already exists")
        state = _FlowState(config)
        _flows[config.flow_id] = state

    if config.probe_type == "continuous":
        t = threading.Thread(
            target=_run_continuous_probe,
            args=(config.flow_id,),
            daemon=True,
            name=f"probe-{config.flow_id}",
        )
        t.start()
        state.thread = t

    return {"flow_id": config.flow_id, "status": "created"}


@app.get("/flows")
def list_flows() -> list[FlowStatus]:
    """List all active flows."""
    with _lock:
        return [
            FlowStatus(
                flow_id=s.config.flow_id,
                dst_ip=s.config.dst_ip,
                probe_type=s.config.probe_type,
                active=s.active,
                packets_sent=s.packets_sent,
                packets_received=s.packets_received,
            )
            for s in _flows.values()
        ]


@app.get("/flows/{flow_id}/results")
def get_results(flow_id: str) -> ProbeResults:
    """Get accumulated results for a flow (resets after read)."""
    with _lock:
        state = _flows.get(flow_id)
        if state is None:
            raise HTTPException(status_code=404, detail=f"Flow {flow_id} not found")
        return state.drain_results()


@app.delete("/flows/{flow_id}", status_code=204)
def delete_flow(flow_id: str) -> None:
    """Stop and remove a probe flow."""
    with _lock:
        state = _flows.pop(flow_id, None)
    if state is None:
        raise HTTPException(status_code=404, detail=f"Flow {flow_id} not found")
    state.stop()


@app.post("/flows/{flow_id}/burst")
def burst(flow_id: str, req: BurstRequest) -> ProbeResults:
    """Run a synchronous burst of N packets."""
    with _lock:
        state = _flows.get(flow_id)
        if state is None:
            raise HTTPException(status_code=404, detail=f"Flow {flow_id} not found")
        dst_ip = state.config.dst_ip

    result = _run_burst_probe(dst_ip, req.count, req.interval_ms)
    return ProbeResults(
        flow_id=flow_id,
        packets_sent=result.packets_sent,
        packets_received=result.packets_received,
        latency_min_ms=result.latency_min_ms,
        latency_max_ms=result.latency_max_ms,
        latency_avg_ms=result.latency_avg_ms,
        jitter_ms=result.jitter_ms,
    )


if __name__ == "__main__":
    import uvicorn
    from nodal.logging import configure as _configure_logging
    from nodalarc.nats_channels import probe_daemon_port

    _configure_logging("nodal.arc.probe")
    uvicorn.run(app, host="0.0.0.0", port=probe_daemon_port())
