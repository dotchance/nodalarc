"""Continuous path tracer — traces forward and reverse paths in a loop.

Runs as an asyncio task, executing tracepath (or CSPF) at regular intervals.
Enriches each link with live netem delay from the kernel. Predicts the next
topology change by scanning the OME timeline.

Per spec: uses tracepath (not traceroute). tracepath doesn't need -s source
binding because once IS-IS/OSPF converges, the FRR routing table has specific
routes to destination loopbacks via ISL/ground interfaces, which are more
specific than the K3s default route.

Forward and reverse traces run concurrently (spec line 358).
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import contextlib
import json
import logging
import socket
from collections.abc import Callable
from datetime import UTC, datetime

from nodalarc.models.path import (
    LiveTraceDirection,
    LiveTraceLink,
    LiveTraceResult,
    PathHop,
    TracepathResult,
)
from nodalarc.models.vs_api import TracedPath
from nodalarc.platform import PlatformConfig
from nodalarc.tracepath_parser import parse_tracepath

from nodalpath.models.topology import TopologyNode
from vs_api.timeline_scanner import TimelineScanner

log = logging.getLogger(__name__)


class ContinuousTracer:
    """Async continuous path tracer."""

    def __init__(
        self,
        deploy_socket: str,
        node_registry: dict[str, TopologyNode],
        interface_map: dict[tuple[str, str], tuple[str, str]],
        pid_map: dict[str, int],
        trace_mode: str,
        config: PlatformConfig,
        timeline_path: str | None,
        get_sim_time: Callable[[], str],
        on_path_change: Callable[[str, str, list[str], list[str]], None] | None = None,
    ) -> None:
        self._deploy_socket = deploy_socket
        self._node_registry = node_registry
        self._interface_map = interface_map
        self._pid_map = pid_map
        self._trace_mode = trace_mode
        self._config = config
        self._get_sim_time = get_sim_time
        self._on_path_change = on_path_change

        # Build IP → node_id lookup
        self._ip_to_node: dict[str, str] = {}
        for node_id, node in node_registry.items():
            self._ip_to_node[node.loopback_ipv4] = node_id

        self._timeline_scanner = TimelineScanner(timeline_path) if timeline_path else None
        self._task: asyncio.Task | None = None
        self._latest: LiveTraceResult | None = None
        self._src: str = ""
        self._dst: str = ""
        # Set by notify_topology_change() to wake the trace loop early
        self._retrace_event = asyncio.Event()
        # Seconds to wait after a topology change before re-tracing,
        # giving OSPF time to converge with the new satellite.
        self._convergence_delay_s = 2.0

    async def start(self, src: str, dst: str) -> None:
        """Start continuous tracing between src and dst."""
        await self.stop()
        self._src = src
        self._dst = dst
        self._latest = None
        self._task = asyncio.create_task(self._trace_loop())

    async def stop(self) -> None:
        """Stop the trace loop."""
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        self._latest = None

    def notify_topology_change(self, node_a: str, node_b: str) -> None:
        """Signal that a link changed — wake the trace loop early.

        Called by the VS-API link event handler when a LinkUp or LinkDown
        affects a ground station.  The trace loop wakes, waits for OSPF
        convergence, then re-traces.
        """
        if not self.active:
            return
        # Only wake if the change involves a node in the current trace path
        traced_nodes: set[str] = set()
        if self._latest is not None:
            traced_nodes = {h.node_id for h in self._latest.forward.hops}
            traced_nodes |= {h.node_id for h in self._latest.reverse.hops}
        # Always retrace if src/dst is involved, or if no path yet
        if (
            node_a in (self._src, self._dst)
            or node_b in (self._src, self._dst)
            or node_a in traced_nodes
            or node_b in traced_nodes
            or self._latest is None
        ):
            self._retrace_event.set()

    @property
    def active(self) -> bool:
        return self._task is not None and not self._task.done()

    @property
    def src(self) -> str:
        return self._src

    @property
    def dst(self) -> str:
        return self._dst

    @property
    def latest_result(self) -> LiveTraceResult | None:
        return self._latest

    @property
    def traced_path(self) -> TracedPath | None:
        """Convert latest result to TracedPath for StateSnapshot."""
        r = self._latest
        if r is None:
            return None
        fwd_hops = [h.node_id for h in r.forward.hops]
        rev_hops = [h.node_id for h in r.reverse.hops]
        fwd_rtts = [h.rtt_ms for h in r.forward.hops]
        rev_rtts = [h.rtt_ms for h in r.reverse.hops]
        return TracedPath(
            flow_id="__continuous_trace__",
            src_node=r.src,
            dst_node=r.dst,
            hops=fwd_hops,
            reverse_hops=rev_hops,
            hop_rtts=fwd_rtts,
            reverse_hop_rtts=rev_rtts,
            rtt_ms=r.forward.rtt_ms,
            reverse_rtt_ms=r.reverse.rtt_ms,
            asymmetry_detected=r.forward.asymmetry_detected or r.reverse.asymmetry_detected,
            method=r.method,
            path_valid_until=r.path_valid_until,
            path_valid_seconds=r.path_valid_seconds,
            traced_at=r.traced_at,
        )

    async def _trace_loop(self) -> None:
        """Main trace loop — runs until cancelled."""
        loop = asyncio.get_running_loop()
        prev_fwd_hops: list[str] = []

        while True:
            try:
                sim_time = self._get_sim_time()
                now = datetime.now(UTC).isoformat()

                if self._trace_mode == "cspf":
                    result = await self._trace_cspf(sim_time, now)
                else:
                    result = await loop.run_in_executor(
                        None,
                        self._trace_tracepath,
                        sim_time,
                        now,
                    )

                if result is not None:
                    fwd_hops = [h.node_id for h in result.forward.hops]
                    # Path change detection
                    if prev_fwd_hops and fwd_hops != prev_fwd_hops and self._on_path_change:
                        try:
                            self._on_path_change(self._src, self._dst, prev_fwd_hops, fwd_hops)
                        except Exception as exc:
                            log.warning("on_path_change callback error: %s", exc)
                    prev_fwd_hops = fwd_hops
                    self._latest = result

                # Adaptive sleep — wake early if a topology change is signalled
                interval = self._config.trace_interval_seconds
                if result and result.path_valid_seconds is not None:  # noqa: SIM102
                    if result.path_valid_seconds < self._config.trace_fast_window_seconds:
                        interval = self._config.trace_interval_fast_seconds

                # On failed trace, retry faster (1s) instead of full interval
                if result is not None and len(result.forward.hops) <= 1:
                    interval = 1.0

                self._retrace_event.clear()
                try:
                    await asyncio.wait_for(self._retrace_event.wait(), timeout=interval)
                    # Woke early from topology change — wait for OSPF convergence
                    log.info(
                        "Topology change detected, waiting %.1fs for convergence",
                        self._convergence_delay_s,
                    )
                    await asyncio.sleep(self._convergence_delay_s)
                except TimeoutError:
                    pass  # Normal interval elapsed

            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.error("Trace loop error: %s", exc, exc_info=True)
                await asyncio.sleep(self._config.trace_interval_seconds)

    def _run_tracepath(self, pod: str, target: str) -> dict:
        """Run tracepath via deploy daemon.

        Uses tracepath -n -b. No source binding needed — once the IGP
        converges, FRR routes are more specific than the K3s default route.
        """
        return self._daemon_request(
            {
                "action": "tracepath",
                "pod": pod,
                "target": target,
            }
        )

    def _trace_tracepath(self, sim_time: str, now: str) -> LiveTraceResult | None:
        """Run forward + reverse tracepath concurrently, then enrich."""
        src_node = self._node_registry.get(self._src)
        dst_node = self._node_registry.get(self._dst)
        if not src_node or not dst_node:
            return None

        # Run forward and reverse concurrently (spec line 358)
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            fwd_future = pool.submit(
                self._run_tracepath,
                self._src.lower(),
                dst_node.loopback_ipv4,
            )
            rev_future = pool.submit(
                self._run_tracepath,
                self._dst.lower(),
                src_node.loopback_ipv4,
            )
            fwd_resp = fwd_future.result()
            rev_resp = rev_future.result()

        fwd_stdout = fwd_resp.get("stdout", "")
        rev_stdout = rev_resp.get("stdout", "")

        if not fwd_stdout and not fwd_resp.get("ok", True):
            log.warning(
                "Forward tracepath %s→%s failed: %s",
                self._src,
                self._dst,
                fwd_resp.get("error", "unknown"),
            )
        if not rev_stdout and not rev_resp.get("ok", True):
            log.warning(
                "Reverse tracepath %s→%s failed: %s",
                self._dst,
                self._src,
                rev_resp.get("error", "unknown"),
            )

        # Parse with tracepath parser
        fwd_parsed = parse_tracepath(fwd_stdout)
        rev_parsed = parse_tracepath(rev_stdout)

        fwd_hops = self._map_hops(fwd_parsed, src_node)
        rev_hops = self._map_hops(rev_parsed, dst_node)

        # Build links and read netem delays
        fwd_links = self._build_links(fwd_hops)
        rev_links = self._build_links(rev_hops)

        all_links = fwd_links + rev_links
        all_queries = self._build_delay_queries(all_links)
        if all_queries:
            delay_resp = self._daemon_request(
                {
                    "action": "read_link_delays",
                    "queries": all_queries,
                }
            )
            delays = delay_resp.get("delays", [])
            self._apply_delays(all_links, delays)
            fwd_links = all_links[: len(fwd_links)]
            rev_links = all_links[len(fwd_links) :]

        # Path validity — spec line 258: path_valid_until is ISO 8601 sim_time
        path_valid_until: str | None = None
        path_valid_seconds: float | None = None
        if self._timeline_scanner is not None:
            all_node_ids = {h.node_id for h in fwd_hops} | {h.node_id for h in rev_hops}
            next_event_time = self._timeline_scanner.scan_next_event(all_node_ids, sim_time)
            if next_event_time:
                path_valid_until = next_event_time
                try:
                    t_next = datetime.fromisoformat(next_event_time)
                    t_now_sim = datetime.fromisoformat(sim_time)
                    path_valid_seconds = max(0.0, (t_next - t_now_sim).total_seconds())
                except Exception:
                    pass

        fwd_rtt = self._extract_rtt(fwd_parsed)
        rev_rtt = self._extract_rtt(rev_parsed)
        fwd_asymm = any(h.asymm is not None for h in fwd_parsed.hops)
        rev_asymm = any(h.asymm is not None for h in rev_parsed.hops)

        return LiveTraceResult(
            src=self._src,
            dst=self._dst,
            forward=LiveTraceDirection(
                hops=fwd_hops,
                links=fwd_links,
                rtt_ms=fwd_rtt,
                asymmetry_detected=fwd_asymm,
                pmtu=fwd_parsed.pmtu,
                raw_output=fwd_stdout if len(fwd_hops) <= 1 else None,
            ),
            reverse=LiveTraceDirection(
                hops=rev_hops,
                links=rev_links,
                rtt_ms=rev_rtt,
                asymmetry_detected=rev_asymm,
                pmtu=rev_parsed.pmtu,
                raw_output=rev_stdout if len(rev_hops) <= 1 else None,
            ),
            traced_at=now,
            sim_time=sim_time,
            topology_state_id="",
            path_valid_until=path_valid_until,
            path_valid_seconds=path_valid_seconds,
            method="tracepath",
            trace_mode=self._trace_mode,
        )

    async def _trace_cspf(self, sim_time: str, now: str) -> LiveTraceResult | None:
        """Run CSPF path derivation via NodalPath HTTP API."""
        import httpx
        from nodalarc.platform import get_platform_config

        cfg = get_platform_config()
        # NodalPath may run in a K8s container (NodePort 31100) or on the host (port 3100)
        np_host = cfg.zmq_connect_host_for("nodalpath")
        if np_host == cfg.zmq_connect_host:  # noqa: SIM108
            # Fallback to global host → NodalPath is on the host or via NodePort
            # Try NodePort first (31100), fall back to direct port (3100)
            np_port = 31100
        else:
            np_port = cfg.nodalpath_console_http_port

        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                fwd_resp, rev_resp = await asyncio.gather(
                    client.get(
                        f"http://{np_host}:{np_port}/api/v1/path",
                        params={"src": self._src, "dst": self._dst},
                    ),
                    client.get(
                        f"http://{np_host}:{np_port}/api/v1/path",
                        params={"src": self._dst, "dst": self._src},
                    ),
                )
        except Exception as exc:
            log.warning("CSPF trace failed: %s", exc)
            return None

        fwd_data = fwd_resp.json() if fwd_resp.status_code == 200 else {}
        rev_data = rev_resp.json() if rev_resp.status_code == 200 else {}

        fwd_hops = self._cspf_hops(fwd_data)
        rev_hops = self._cspf_hops(rev_data)

        fwd_links = self._cspf_links(fwd_data)
        rev_links = self._cspf_links(rev_data)

        fwd_ids = [h.node_id for h in fwd_hops]
        rev_ids = [h.node_id for h in rev_hops]

        return LiveTraceResult(
            src=self._src,
            dst=self._dst,
            forward=LiveTraceDirection(
                hops=fwd_hops,
                links=fwd_links,
                rtt_ms=fwd_data.get("total_latency_ms", 0.0),
                asymmetry_detected=fwd_ids != list(reversed(rev_ids)),
            ),
            reverse=LiveTraceDirection(
                hops=rev_hops,
                links=rev_links,
                rtt_ms=rev_data.get("total_latency_ms", 0.0),
                asymmetry_detected=fwd_ids != list(reversed(rev_ids)),
            ),
            traced_at=now,
            sim_time=sim_time,
            topology_state_id=fwd_data.get("topology_state_id", ""),
            method="cspf",
            trace_mode="cspf",
        )

    def _map_hops(self, parsed: TracepathResult, src_node: TopologyNode) -> list[PathHop]:
        """Map parsed tracepath hops to PathHop list.

        Deduplicates by hop_num — tracepath reports the same hop_num
        multiple times (retries at the same TTL).  We take the first
        entry with a valid, resolvable IP at each hop_num.
        """
        hops: list[PathHop] = [
            PathHop(
                node_id=src_node.node_id,
                node_type=src_node.node_type,
                sid=src_node.sid,
                rtt_ms=0.0,
            )
        ]
        # Collect the first resolvable IP per hop_num
        best_per_hop: dict[int, TracepathHop] = {}  # noqa: F821
        for th in parsed.hops:
            if th.ip is None:
                continue
            if th.hop_num in best_per_hop:
                continue  # already have a valid entry for this hop
            if self._ip_to_node.get(th.ip) is not None:
                best_per_hop[th.hop_num] = th

        for hop_num in sorted(best_per_hop):
            th = best_per_hop[hop_num]
            node_id = self._ip_to_node[th.ip]
            if node_id == src_node.node_id:
                continue
            node = self._node_registry[node_id]
            hops.append(
                PathHop(
                    node_id=node_id,
                    node_type=node.node_type,
                    sid=node.sid,
                    rtt_ms=round(th.rtt_ms, 3) if th.rtt_ms is not None else None,
                    responding_ip=th.ip,
                )
            )
        return hops

    def _build_links(self, hops: list[PathHop]) -> list[LiveTraceLink]:
        """Build LiveTraceLink list from consecutive hop pairs."""
        links: list[LiveTraceLink] = []
        for i in range(len(hops) - 1):
            a, b = hops[i].node_id, hops[i + 1].node_id
            key = (min(a, b), max(a, b))
            iface_pair = self._interface_map.get(key)
            iface = ""
            link_type = None
            if iface_pair:
                iface = iface_pair[0] if a == key[0] else iface_pair[1]
                link_type = "ground" if a.startswith("gs-") or b.startswith("gs-") else "isl"
            links.append(
                LiveTraceLink(
                    from_node=a,
                    to_node=b,
                    interface=iface,
                    link_type=link_type,
                )
            )
        return links

    def _build_delay_queries(self, links: list[LiveTraceLink]) -> list[dict]:
        """Build batch delay queries for links that have PID + interface info."""
        queries: list[dict] = []
        for link in links:
            pid = self._pid_map.get(link.from_node)
            if pid and link.interface:
                queries.append({"pid": pid, "ifname": link.interface})
            else:
                queries.append({"pid": 0, "ifname": ""})  # placeholder
        return queries

    def _apply_delays(self, links: list[LiveTraceLink], delays: list[dict]) -> None:
        """Apply delay results back to links by replacing frozen objects in-place."""
        for i, delay_info in enumerate(delays):
            if i < len(links) and delay_info.get("delay_ms") is not None:
                old = links[i]
                links[i] = LiveTraceLink(
                    from_node=old.from_node,
                    to_node=old.to_node,
                    interface=old.interface,
                    netem_delay_ms=delay_info["delay_ms"],
                    link_type=old.link_type,
                )

    @staticmethod
    def _extract_rtt(parsed: TracepathResult) -> float:
        """Extract end-to-end RTT from the last hop with an IP."""
        for hop in reversed(parsed.hops):
            if hop.rtt_ms is not None:
                return round(hop.rtt_ms, 3)
        return 0.0

    @staticmethod
    def _cspf_hops(data: dict) -> list[PathHop]:
        """Build PathHop list from CSPF response.

        Computes cumulative rtt_ms from per-hop latency_to_next_ms so the
        frontend can display per-hop timing. The CSPF response provides
        one-way latency per hop; cumulative values give increasing RTT
        from source to each hop (one-way, not round-trip).
        """
        raw_hops = data.get("hops", [])
        result = []
        cumulative_ms = 0.0
        for h in raw_hops:
            if isinstance(h, dict):
                # Override rtt_ms with cumulative latency before validation
                # (PathHop is frozen, so we set it during construction)
                h_copy = dict(h)
                h_copy["rtt_ms"] = cumulative_ms
                result.append(PathHop.model_validate(h_copy))
                cumulative_ms += h.get("latency_to_next_ms") or 0.0
            elif isinstance(h, str):
                result.append(PathHop(node_id=h, node_type="satellite", rtt_ms=cumulative_ms))
        return result

    @staticmethod
    def _cspf_links(data: dict) -> list[LiveTraceLink]:
        """Build LiveTraceLink list from CSPF response hop details."""
        raw_hops = data.get("hops", [])
        links: list[LiveTraceLink] = []
        for i in range(len(raw_hops) - 1):
            h = raw_hops[i]
            h_next = raw_hops[i + 1]
            if isinstance(h, dict) and isinstance(h_next, dict):
                from_id = h.get("node_id", "")
                to_id = h_next.get("node_id", "")
                links.append(
                    LiveTraceLink(
                        from_node=from_id,
                        to_node=to_id,
                        interface=h.get("out_interface", ""),
                        netem_delay_ms=h.get("latency_to_next_ms"),
                        link_type="ground"
                        if from_id.startswith("gs-") or to_id.startswith("gs-")
                        else "isl",
                    )
                )
        return links

    def _daemon_request(self, req: dict) -> dict:
        """Send a request to the deploy daemon via Unix socket."""
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(90.0)
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
                    return json.loads(buf[: buf.index(b"\n")])
            return {"ok": False, "error": "No response from deploy daemon"}
        except FileNotFoundError:
            return {"ok": False, "error": "Deploy daemon not running"}
        except ConnectionRefusedError:
            return {"ok": False, "error": "Deploy daemon connection refused"}
        except PermissionError:
            return {"ok": False, "error": "Deploy daemon socket permission denied"}
        except TimeoutError:
            return {"ok": False, "error": "Daemon request timed out"}
        except OSError as exc:
            return {"ok": False, "error": f"Daemon socket error: {exc}"}
        finally:
            sock.close()
