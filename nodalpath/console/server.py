"""FastAPI operator console for NodalPath.

Serves on NODALPATH_CONSOLE_PORT (3100), bound to 0.0.0.0 (network-accessible).
No authentication — deploy behind a firewall or on a private interface.

The frontend is a React + TypeScript + D3 app built from nodalpath/console/frontend/.
If the dist/ directory exists, it is served via StaticFiles. Otherwise, a holding page
directs the operator to run `make build-nodalpath-console`.
"""

from __future__ import annotations

import asyncio
import logging
import os

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from nodalpath.console.state import ConsoleState

log = logging.getLogger(__name__)


def build_app(
    state: ConsoleState,
    almanac_store=None,
    prefix_map: dict[str, list[str]] | None = None,
    link_state_store=None,
    path_deriver=None,
    node_inspector=None,
    live_path_tracer=None,
    trace_mode: str | None = None,
) -> FastAPI:
    """Build and return the FastAPI application.

    Args:
        state: The shared ConsoleState instance written by LiveOrchestrator.
        almanac_store: Optional reference to the AlmanacStore instance.
                       Required for /api/v1/node/{node_id}/state.
                       If None, that endpoint returns {"available": false}.
        prefix_map: Optional node_id -> prefix mapping for topology reconstruction.

    Returns:
        A configured FastAPI app ready to be served by uvicorn.
    """
    _prefix_map = prefix_map or {}

    app = FastAPI(title="NodalPath Console", docs_url=None, redoc_url=None)

    # ── REST endpoints ───────────────────────────────────────────────────────

    @app.get("/api/health")
    async def health() -> JSONResponse:
        return JSONResponse({"status": "ok"})

    @app.get("/api/config")
    async def frontend_config() -> JSONResponse:
        """Runtime config for the console frontend — no hardcoded ports."""
        from nodalarc.platform import get_platform_config

        cfg = get_platform_config()
        return JSONResponse(
            {
                "globe_port": cfg.vs_api_http_port,  # VF proxied through VS-API / Vite
                "console_port": cfg.nodalpath_console_http_port,
            }
        )

    @app.get("/api/v1/trace-config")
    async def trace_config_endpoint() -> JSONResponse:
        """Return the trace mode configuration for the current session."""
        # For nodalpath-fwd sessions (path_deriver is wired), report cspf mode
        effective_mode = trace_mode
        if effective_mode is None and path_deriver is not None:
            effective_mode = "cspf"
        return JSONResponse(
            {
                "trace_mode": effective_mode,
                "pipe_mode": effective_mode == "sr-pipe",
                "has_sr": effective_mode in ("sr-uniform", "sr-pipe"),
            }
        )

    @app.get("/api/status")
    async def status() -> JSONResponse:
        return JSONResponse(state.snapshot())

    @app.get("/api/almanac")
    async def almanac() -> JSONResponse:
        snap = state.snapshot()
        return JSONResponse(snap["almanac_history"])

    @app.get("/api/pushes")
    async def pushes() -> JSONResponse:
        snap = state.snapshot()
        return JSONResponse(snap["push_history"])

    @app.get("/api/deviations")
    async def deviations() -> JSONResponse:
        snap = state.snapshot()
        return JSONResponse(snap["deviation_history"])

    @app.get("/api/events")
    async def events() -> JSONResponse:
        """Unified event log, newest-first."""
        snap = state.snapshot()
        return JSONResponse(snap["event_log"])

    @app.post("/api/recompute")
    async def recompute() -> JSONResponse:
        state.request_recompute()
        log.info("Manual recompute requested via console")
        return JSONResponse({"ok": True})

    # ── Topology + Node state endpoints ──────────────────────────────────────

    @app.get("/api/v1/topology/current")
    async def topology_current() -> JSONResponse:
        """Return the current topology snapshot in D3-ready format."""
        topo = state.get_topology()
        if topo is None:
            return JSONResponse({"available": False})

        links = topo.get("links", [])
        for lnk in links:
            lnk["state"] = "active"
            lnk.setdefault("visible", True)
            lnk.setdefault("scheduled", True)

        return JSONResponse({"available": True, **topo, "links": links})

    @app.get("/api/v1/node/{node_id}/state")
    async def node_state(node_id: str) -> JSONResponse:
        """Return the current forwarding table entries for a node."""
        if almanac_store is None:
            return JSONResponse({"available": False, "reason": "almanac_store not wired"})

        topo = state.get_topology()
        if topo is None:
            return JSONResponse({"available": False, "reason": "no topology state yet"})

        topology_state_id = topo.get("topology_state_id")
        if topology_state_id is None:
            return JSONResponse({"available": False, "reason": "no topology_state_id"})

        try:
            ft = almanac_store.get_forwarding_entries_for_node(
                node_id=node_id,
                topology_state_id=topology_state_id,
            )
        except Exception as exc:
            log.warning("Failed to query almanac for node %s: %s", node_id, exc)
            return JSONResponse({"available": False, "reason": str(exc)})

        if ft is None:
            return JSONResponse({"available": False, "reason": "node not found in almanac"})

        # Serialize ForwardingTable
        forwarding_entries = [
            {
                "destination": b.out_interface,
                "next_hop": b.out_interface,
                "outgoing_label": b.out_label,
                "incoming_label": b.in_label,
                "operation": b.action,
            }
            for b in ft.lsr_bindings
        ] + [
            {
                "destination": r.dst_prefix,
                "next_hop": r.out_interface,
                "outgoing_label": r.push_label,
                "incoming_label": None,
                "operation": "push",
            }
            for r in ft.ler_ingress_rules
        ]

        return JSONResponse(
            {
                "available": True,
                "node_id": node_id,
                "topology_state_id": topology_state_id,
                "forwarding_entries": forwarding_entries,
            }
        )

    # ── Timeline + Historical endpoints ──────────────────────────────────────

    @app.get("/api/v1/timeline")
    async def timeline() -> JSONResponse:
        """Return all timeline ticks with push and deviation annotations."""
        if almanac_store is None:
            return JSONResponse(
                {
                    "available": True,
                    "tick_count": 0,
                    "lookahead_status": state.get_lookahead_status(),
                    "ticks": [],
                }
            )

        ticks = almanac_store.get_timeline_ticks()
        snap = state.snapshot()
        push_history = snap.get("push_history", [])
        deviation_history = snap.get("deviation_history", [])

        push_by_state: dict[str, dict] = {}
        for p in push_history:
            push_by_state[p["topology_state_id"]] = p

        deviation_states: set[str] = {d["topology_state_id"] for d in deviation_history}

        annotated = []
        prev_node_count = None
        for tick in ticks:
            push = push_by_state.get(tick["topology_state_id"])
            node_count = tick["node_count"]
            delta = (node_count - prev_node_count) if prev_node_count is not None else None
            prev_node_count = node_count

            annotated.append(
                {
                    "sim_time": tick["sim_time"],
                    "topology_state_id": tick["topology_state_id"],
                    "node_count": node_count,
                    "is_future": tick["is_future"],
                    "push_succeeded": (push["nodes_failed"] == 0) if push else None,
                    "push_failed_count": push["nodes_failed"] if push else 0,
                    "had_deviation": tick["topology_state_id"] in deviation_states,
                    "node_count_delta": delta,
                }
            )

        return JSONResponse(
            {
                "available": True,
                "tick_count": len(annotated),
                "lookahead_status": state.get_lookahead_status(),
                "ticks": annotated,
            }
        )

    @app.get("/api/v1/topology/at/{sim_time:path}")
    async def topology_at(sim_time: str) -> JSONResponse:
        """Return the topology state at or before the given sim_time."""
        if almanac_store is None:
            return JSONResponse({"available": False, "reason": "almanac_store not wired"})

        topo = almanac_store.get_topology_at(sim_time, _prefix_map)
        if topo is None:
            return JSONResponse(
                {"available": False, "reason": "no entry at or before requested sim_time"}
            )

        links_payload = []
        links_available = False

        if link_state_store is not None:
            records = link_state_store.get_by_sim_time(sim_time)
            if records is not None:
                links_available = True
                for r in records:
                    links_payload.append(
                        {
                            "node_a": r.node_a,
                            "node_b": r.node_b,
                            "visible": r.visible,
                            "scheduled": r.scheduled,
                            "range_km": r.range_km,
                            "link_type": r.link_type,
                            "state": "active"
                            if (r.visible and r.scheduled)
                            else "visible_unscheduled"
                            if r.visible
                            else "inactive",
                        }
                    )

        return JSONResponse(
            {
                "available": True,
                "is_historical": True,
                "is_future": topo.get("is_future", False),
                "links_available": links_available,
                **topo,
                "links": links_payload,
            }
        )

    @app.get("/api/v1/node/{node_id}/state/at/{sim_time:path}")
    async def node_state_at(node_id: str, sim_time: str) -> JSONResponse:
        """Return forwarding table for a node at a historical sim_time."""
        if almanac_store is None:
            return JSONResponse({"available": False, "reason": "almanac_store not wired"})

        entry = almanac_store.get_entry_at(sim_time)
        if entry is None:
            return JSONResponse(
                {"available": False, "reason": "no entry at or before requested sim_time"}
            )

        ft = None
        for forwarding_table in entry.forwarding_tables:
            if forwarding_table.node_id == node_id:
                ft = forwarding_table
                break

        if ft is None:
            return JSONResponse({"available": False, "reason": "node not found in entry"})

        forwarding_entries = [
            {
                "destination": b.out_interface,
                "next_hop": b.out_interface,
                "outgoing_label": b.out_label,
                "incoming_label": b.in_label,
                "operation": b.action,
            }
            for b in ft.lsr_bindings
        ] + [
            {
                "destination": r.dst_prefix,
                "next_hop": r.out_interface,
                "outgoing_label": r.push_label,
                "incoming_label": None,
                "operation": "push",
            }
            for r in ft.ler_ingress_rules
        ]

        return JSONResponse(
            {
                "available": True,
                "node_id": node_id,
                "topology_state_id": entry.topology_state_id,
                "is_future": entry.is_future,
                "forwarding_entries": forwarding_entries,
            }
        )

    # ── Path derivation endpoint ─────────────────────────────────────────────

    @app.get("/api/v1/path")
    async def get_path(src: str, dst: str, sim_time: str | None = None) -> JSONResponse:
        """Derive or trace the forwarding path from src to dst.

        Uses CSPF path deriver when available (nodalpath-fwd sessions).
        Falls back to live traceroute through FRR pods (IS-IS/OSPF sessions).
        """
        if path_deriver is not None:
            loop = asyncio.get_running_loop()
            result = await loop.run_in_executor(None, path_deriver.derive, src, dst, sim_time)
            return JSONResponse(result.model_dump())

        if live_path_tracer is not None:
            loop = asyncio.get_running_loop()
            result = await loop.run_in_executor(None, live_path_tracer.trace, src, dst)
            return JSONResponse(result.model_dump(mode="json"))

        return JSONResponse(
            {
                "reachable": False,
                "unreachable_reason": "no path tracer available",
                "src": src,
                "dst": dst,
                "hops": [],
                "total_latency_ms": 0.0,
                "method": "none",
                "sim_time": sim_time or "",
                "topology_state_id": "",
            }
        )

    # ── Continuous trace proxy endpoints ────────────────────────────────────

    @app.post("/api/v1/trace/start")
    async def trace_start(request: Request) -> JSONResponse:
        """Proxy trace/start to VS-API."""
        return await _proxy_to_vsapi("POST", "/api/v1/trace/start", await request.json())

    @app.post("/api/v1/trace/stop")
    async def trace_stop() -> JSONResponse:
        """Proxy trace/stop to VS-API."""
        return await _proxy_to_vsapi("POST", "/api/v1/trace/stop")

    @app.get("/api/v1/trace/status")
    async def trace_status() -> JSONResponse:
        """Proxy trace/status to VS-API."""
        return await _proxy_to_vsapi("GET", "/api/v1/trace/status")

    async def _proxy_to_vsapi(method: str, path: str, body: dict | None = None) -> JSONResponse:
        """Proxy a request to the VS-API server."""
        import httpx
        from nodalarc.platform import get_platform_config

        cfg = get_platform_config()
        vs_host = cfg.zmq_connect_host_for("vs-api")
        vs_port = cfg.vs_api_http_port
        url = f"http://{vs_host}:{vs_port}{path}"
        # Fetch VS-API auth token (unauthenticated endpoint)
        headers: dict[str, str] = {}
        try:
            async with httpx.AsyncClient(timeout=3.0) as tc:
                token_resp = await tc.get(f"http://{vs_host}:{vs_port}/api/v1/auth/token")
                if token_resp.status_code == 200:
                    token = token_resp.json().get("token", "")
                    if token:
                        headers["Authorization"] = f"Bearer {token}"
        except Exception:
            pass
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                if method == "POST":
                    r = await client.post(url, json=body or {}, headers=headers)
                else:
                    r = await client.get(url, headers=headers)
                if r.status_code == 404:
                    return JSONResponse(
                        {
                            "error": f"VS-API endpoint {path} not found — restart VS-API to pick up new routes"
                        },
                        status_code=502,
                    )
                return JSONResponse(r.json(), status_code=r.status_code)
        except httpx.ConnectError:
            return JSONResponse({"error": "VS-API not reachable — is it running?"}, status_code=503)
        except httpx.TimeoutException:
            return JSONResponse({"error": "VS-API timeout"}, status_code=504)
        except Exception as exc:
            return JSONResponse({"error": str(exc)}, status_code=500)

    # ── Inspection endpoints ────────────────────────────────────────────────

    @app.get("/api/v1/inspect/runs")
    async def inspect_runs(n: int = 10) -> JSONResponse:
        """Return summaries of recent inspection runs."""
        if node_inspector is None:
            return JSONResponse({"error": "inspection not available"}, status_code=503)
        n = min(max(n, 1), 50)
        runs = node_inspector.recent_runs(n)
        return JSONResponse(
            {"runs": [r.model_dump(mode="json", exclude={"node_results"}) for r in runs]}
        )

    @app.get("/api/v1/inspect/runs/{run_id}")
    async def inspect_run_detail(run_id: str) -> JSONResponse:
        """Return full inspection run with per-node results."""
        if node_inspector is None:
            return JSONResponse({"error": "inspection not available"}, status_code=503)
        run = node_inspector.get_run(run_id)
        if run is None:
            return JSONResponse({"error": "run not found"}, status_code=404)
        return JSONResponse(run.model_dump(mode="json"))

    @app.post("/api/v1/inspect/trigger")
    async def inspect_trigger(request: Request) -> JSONResponse:
        """Trigger an operator inspection."""
        if node_inspector is None:
            return JSONResponse({"error": "inspection not available"}, status_code=503)
        node_ids = None
        try:
            body = await request.json()
            if body and "node_ids" in body:
                node_ids = body["node_ids"]
        except Exception:
            pass  # Empty body is fine — inspect all
        run = await node_inspector.trigger_operator(node_ids=node_ids)
        return JSONResponse({"run_id": run.run_id, "status": "completed"}, status_code=200)

    @app.get("/api/v1/inspect/latest")
    async def inspect_latest() -> JSONResponse:
        """Return the most recent inspection run summary."""
        if node_inspector is None:
            return JSONResponse({"error": "inspection not available"}, status_code=503)
        run = node_inspector.latest_run
        if run is None:
            return JSONResponse({"run": None})
        return JSONResponse({"run": run.model_dump(mode="json", exclude={"node_results"})})

    # ── Static files or holding page ─────────────────────────────────────────

    _dist = os.path.join(os.path.dirname(__file__), "frontend", "dist")
    if os.path.isdir(_dist):
        app.mount("/", StaticFiles(directory=_dist, html=True), name="static")
    else:

        @app.get("/", response_class=HTMLResponse)
        async def holding_page() -> HTMLResponse:
            return HTMLResponse(
                "<html><body style='background:#0d0d1a;color:#e0e0e0;font-family:monospace;"
                "padding:40px'><h2 style='color:#00d4aa'>NodalPath Console</h2>"
                "<p>Frontend not built. Run: <code>make build-nodalpath-console</code></p>"
                "</body></html>"
            )

    return app
