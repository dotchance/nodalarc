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


def build_app(
    state: ConsoleState,
    almanac_store=None,
    prefix_map: dict[str, str] | None = None,
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

    # ── Timeline + Historical endpoints ──────────────────────────────────────

    @app.get("/api/v1/timeline")
    async def timeline() -> JSONResponse:
        """Return all timeline ticks with push and deviation annotations."""
        if almanac_store is None:
            return JSONResponse({
                "available": True,
                "tick_count": 0,
                "lookahead_status": state.get_lookahead_status(),
                "ticks": [],
            })

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

            annotated.append({
                "sim_time": tick["sim_time"],
                "topology_state_id": tick["topology_state_id"],
                "node_count": node_count,
                "is_future": tick["is_future"],
                "push_succeeded": (push["nodes_failed"] == 0) if push else None,
                "push_failed_count": push["nodes_failed"] if push else 0,
                "had_deviation": tick["topology_state_id"] in deviation_states,
                "node_count_delta": delta,
            })

        return JSONResponse({
            "available": True,
            "tick_count": len(annotated),
            "lookahead_status": state.get_lookahead_status(),
            "ticks": annotated,
        })

    @app.get("/api/v1/topology/at/{sim_time:path}")
    async def topology_at(sim_time: str) -> JSONResponse:
        """Return the topology state at or before the given sim_time."""
        if almanac_store is None:
            return JSONResponse({"available": False, "reason": "almanac_store not wired"})

        topo = almanac_store.get_topology_at(sim_time, _prefix_map)
        if topo is None:
            return JSONResponse({"available": False, "reason": "no entry at or before requested sim_time"})

        return JSONResponse({
            "available": True,
            "is_historical": True,
            "is_future": topo.get("is_future", False),
            "links_available": False,
            **topo,
        })

    @app.get("/api/v1/node/{node_id}/state/at/{sim_time:path}")
    async def node_state_at(node_id: str, sim_time: str) -> JSONResponse:
        """Return forwarding table for a node at a historical sim_time."""
        if almanac_store is None:
            return JSONResponse({"available": False, "reason": "almanac_store not wired"})

        entry = almanac_store.get_entry_at(sim_time)
        if entry is None:
            return JSONResponse({"available": False, "reason": "no entry at or before requested sim_time"})

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

        return JSONResponse({
            "available": True,
            "node_id": node_id,
            "topology_state_id": entry.topology_state_id,
            "is_future": entry.is_future,
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
