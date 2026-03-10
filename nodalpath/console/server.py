"""FastAPI operator console for NodalPath.

Serves on NODALPATH_CONSOLE_PORT (3100), bound to 0.0.0.0 (network-accessible).
No authentication — deploy behind a firewall or on a private interface.

The frontend is a React + TypeScript + D3 app built from nodalpath/console/frontend/.
If the dist/ directory exists, it is served via StaticFiles. Otherwise, a holding page
directs the operator to run `make build-nodalpath-console`.
"""

from __future__ import annotations

import logging
import os

from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from nodalpath.console.state import ConsoleState

log = logging.getLogger(__name__)


def build_app(state: ConsoleState, almanac_store=None) -> FastAPI:
    """Build and return the FastAPI application.

    Args:
        state: The shared ConsoleState instance written by LiveOrchestrator.
        almanac_store: Optional reference to the AlmanacStore instance.
                       Required for /api/v1/node/{node_id}/state.
                       If None, that endpoint returns {"available": false}.

    Returns:
        A configured FastAPI app ready to be served by uvicorn.
    """
    app = FastAPI(title="NodalPath Console", docs_url=None, redoc_url=None)

    # ── REST endpoints ───────────────────────────────────────────────────────

    @app.get("/api/health")
    async def health() -> JSONResponse:
        return JSONResponse({"status": "ok"})

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
        return JSONResponse({"available": True, **topo})

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

        return JSONResponse({
            "available": True,
            "node_id": node_id,
            "topology_state_id": topology_state_id,
            "forwarding_entries": forwarding_entries,
        })

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
