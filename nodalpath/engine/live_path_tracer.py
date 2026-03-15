"""Live path tracer — runs real traceroute through emulated FRR nodes.

For non-nodalpath-fwd sessions (IS-IS, OSPF, etc.), traces the actual
forwarding path by executing `traceroute` from the source pod to the
destination node's loopback IP. Intermediate hop IPs are mapped back
to node_ids using the node registry.

Trace modes:
  - "ip":         Plain IP traceroute (frr-ospf-te). All hops visible.
  - "sr-uniform": SR-MPLS with ip_ttl_propagate=1. Hops visible via MPLS TTL.
  - "sr-pipe":    SR-MPLS with ip_ttl_propagate=0. Core collapsed to 1 hop.

For SR modes, traceroute uses -s <loopback> so ICMP replies route back
through the IGP rather than the K3s default route.

All kubectl exec goes through the deploy daemon Unix socket.
"""

from __future__ import annotations

import json
import logging
import re
import socket
from datetime import datetime, timezone

from nodalarc.models.path import PathHop, PathResult
from nodalpath.models.topology import TopologyNode

log = logging.getLogger(__name__)

# Match a hop line containing an IP + RTT, allowing leading stars from
# timed-out probes when -q >1 (e.g., "  3  *  10.0.0.3  1.000 ms")
_HOP_RE = re.compile(
    r"^\s*(\d+)\s+(?:\*\s+)*(\d+\.\d+\.\d+\.\d+)\s+([\d.]+)\s*ms"
)
# All-star line: "  3  * *" or "  3  *"
_STAR_RE = re.compile(r"^\s*(\d+)\s+\*(?:\s+\*)*\s*$")

_METHOD_MAP = {
    "ip": "traceroute",
    "sr-uniform": "traceroute-sr",
    "sr-pipe": "traceroute-sr-pipe",
}


class LivePathTracer:
    """Traces real paths through live FRR pods via traceroute."""

    def __init__(
        self,
        node_registry: dict[str, TopologyNode],
        trace_mode: str = "ip",
        deploy_socket: str | None = None,
        timeout: float = 90.0,
    ) -> None:
        self._node_registry = node_registry
        self._trace_mode = trace_mode
        self._ip_to_node: dict[str, str] = {}
        for node_id, node in node_registry.items():
            self._ip_to_node[node.loopback_ipv4] = node_id
        if deploy_socket is None:
            from nodalarc.platform import get_platform_config
            deploy_socket = get_platform_config().deploy_daemon_unix_socket_path
        self._deploy_socket = deploy_socket
        self._timeout = timeout

    @property
    def trace_mode(self) -> str:
        return self._trace_mode

    def trace(self, src: str, dst: str) -> PathResult:
        """Run traceroute from src to dst's loopback IP."""
        now = datetime.now(timezone.utc).isoformat()
        method = _METHOD_MAP.get(self._trace_mode, "traceroute")
        pipe_mode = self._trace_mode == "sr-pipe"

        src_node = self._node_registry.get(src)
        dst_node = self._node_registry.get(dst)
        if src_node is None:
            return self._unreachable(src, dst, now, method, pipe_mode,
                                     f"unknown source node: {src}")
        if dst_node is None:
            return self._unreachable(src, dst, now, method, pipe_mode,
                                     f"unknown destination node: {dst}")

        req: dict = {
            "action": "traceroute",
            "pod": src.lower(),
            "target": dst_node.loopback_ipv4,
        }
        # SR modes: use loopback as source so ICMP replies route through IGP
        if self._trace_mode in ("sr-uniform", "sr-pipe"):
            req["source"] = src_node.loopback_ipv4

        resp = self._daemon_request(req)

        # Grab stdout even on daemon-level failures (e.g. timeout with partial output)
        stdout = resp.get("stdout", "")
        if not stdout and not resp.get("ok", False):
            return self._unreachable(src, dst, now, method, pipe_mode,
                                     resp.get("error", "traceroute failed"))

        hops = self._parse(stdout, src_node) if stdout else []

        if not hops:
            log.warning("traceroute %s -> %s: no hops parsed. raw:\n%s", src, dst, stdout[:500])
            return self._unreachable(src, dst, now, method, pipe_mode,
                                     "no hops resolved from traceroute output",
                                     raw_output=stdout)

        # Check reachability: dst appears anywhere in the hop list
        dst_seen = any(h.node_id == dst for h in hops)

        if not dst_seen:
            hop_ids = [h.node_id for h in hops]
            log.warning(
                "traceroute %s -> %s: dst not in hops %s (target_ip=%s). raw:\n%s",
                src, dst, hop_ids, dst_node.loopback_ipv4, stdout[:500],
            )

        # Total is the last hop's RTT (end-to-end round trip), not a sum
        last_rtt = next(
            (h.rtt_ms for h in reversed(hops) if h.rtt_ms is not None),
            0.0,
        )
        return PathResult(
            src=src, dst=dst, hops=hops, total_latency_ms=round(last_rtt, 3),
            method=method, sim_time=now, topology_state_id="",
            reachable=dst_seen,
            unreachable_reason=None if dst_seen else f"traceroute did not reach {dst}",
            pipe_mode=pipe_mode,
            raw_output=stdout if not dst_seen else None,
        )

    def _parse(self, output: str, src_node: TopologyNode) -> list[PathHop]:
        """Parse traceroute output into PathHop list."""
        raw: list[tuple[int, str | None, float | None]] = []

        for line in output.splitlines():
            m = _HOP_RE.match(line)
            if m:
                raw.append((int(m.group(1)), m.group(2), float(m.group(3))))
                continue
            m = _STAR_RE.match(line)
            if m:
                raw.append((int(m.group(1)), None, None))

        if not raw:
            return []

        # Build hops — start with src (RTT 0)
        hops: list[PathHop] = [PathHop(
            node_id=src_node.node_id,
            node_type=src_node.node_type,
            sid=src_node.sid,
            rtt_ms=0.0,
        )]

        for _hop_num, ip, rtt in raw:
            if ip is None:
                continue
            node_id = self._ip_to_node.get(ip)
            if node_id is None:
                continue
            if node_id == src_node.node_id:
                continue
            node = self._node_registry[node_id]
            hops.append(PathHop(
                node_id=node_id,
                node_type=node.node_type,
                sid=node.sid,
                rtt_ms=round(rtt, 3) if rtt is not None else None,
                responding_ip=ip,
            ))

        return hops

    def _daemon_request(self, req: dict) -> dict:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(self._timeout)
        try:
            sock.connect(self._deploy_socket)
            sock.sendall((json.dumps(req) + "\n").encode())
            buf = b""
            while True:
                chunk = sock.recv(4096)
                if not chunk:
                    break
                buf += chunk
                if b"\n" in buf:
                    return json.loads(buf[:buf.index(b"\n")])
            return {"ok": False, "error": "No response from deploy daemon"}
        except FileNotFoundError:
            return {"ok": False, "error": "Deploy daemon not running"}
        except ConnectionRefusedError:
            return {"ok": False, "error": "Deploy daemon connection refused"}
        except socket.timeout:
            return {"ok": False, "error": "Traceroute timed out"}
        finally:
            sock.close()

    @staticmethod
    def _unreachable(
        src: str, dst: str, sim_time: str,
        method: str, pipe_mode: bool, reason: str,
        raw_output: str | None = None,
    ) -> PathResult:
        return PathResult(
            src=src, dst=dst, hops=[], total_latency_ms=0.0,
            method=method, sim_time=sim_time, topology_state_id="",
            reachable=False, unreachable_reason=reason,
            pipe_mode=pipe_mode, raw_output=raw_output,
        )
