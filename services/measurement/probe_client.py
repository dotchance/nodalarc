"""HTTP client for probe daemons on GS pods.

Uses urllib.request (stdlib) — no requests or httpx.
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from typing import Any

from nodalarc.nats_channels import probe_daemon_port

log = logging.getLogger(__name__)


def _url(pod_ip: str, path: str) -> str:
    return f"http://{pod_ip}:{probe_daemon_port()}{path}"


def _request(
    url: str,
    method: str = "GET",
    data: dict[str, Any] | None = None,
    timeout: float = 10.0,
) -> dict[str, Any] | None:
    """Send an HTTP request and return parsed JSON response."""
    body = json.dumps(data).encode() if data else None
    req = urllib.request.Request(
        url,
        data=body,
        method=method,
        headers={"Content-Type": "application/json"} if body else {},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            if resp.status == 204:
                return None
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        log.warning(f"HTTP {exc.code} from {url}: {exc.read().decode()}")
        raise
    except urllib.error.URLError as exc:
        log.warning(f"URL error for {url}: {exc.reason}")
        raise


def configure_flow(
    pod_ip: str,
    flow_id: str,
    dst_ip: str,
    protocol: str = "icmp",
    bandwidth_kbps: float = 100,
    probe_type: str = "continuous",
    interval_ms: int = 1000,
) -> dict[str, Any]:
    """Configure a new probe flow on a GS pod."""
    return _request(
        _url(pod_ip, "/flows"),
        method="POST",
        data={
            "flow_id": flow_id,
            "dst_ip": dst_ip,
            "protocol": protocol,
            "bandwidth_kbps": bandwidth_kbps,
            "probe_type": probe_type,
            "interval_ms": interval_ms,
        },
    )


def get_results(pod_ip: str, flow_id: str) -> dict[str, Any]:
    """Get accumulated results for a flow (resets after read)."""
    return _request(_url(pod_ip, f"/flows/{flow_id}/results"))


def list_flows(pod_ip: str) -> list[dict[str, Any]]:
    """List all active flows on a probe daemon."""
    return _request(_url(pod_ip, "/flows"))


def delete_flow(pod_ip: str, flow_id: str) -> None:
    """Stop and remove a probe flow."""
    _request(_url(pod_ip, f"/flows/{flow_id}"), method="DELETE")


def burst(
    pod_ip: str,
    flow_id: str,
    count: int = 10,
    interval_ms: int = 200,
) -> dict[str, Any]:
    """Run a synchronous burst of N packets."""
    return _request(
        _url(pod_ip, f"/flows/{flow_id}/burst"),
        method="POST",
        data={"count": count, "interval_ms": interval_ms},
    )
