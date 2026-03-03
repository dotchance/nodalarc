"""VS-API — Visualization State API server.

FastAPI server with WebSocket (full snapshots at ~1Hz) and REST endpoints.
Subscribes to ZMQ PUB sockets from OME, TO, and MI to maintain state.

Run: python -m vs_api.main --session <path> --db <sqlite_path> --port 8080
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sqlite3
import threading
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml
import zmq
import zmq.asyncio
from fastapi import FastAPI, Query, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import TypeAdapter

from nodalarc.constants import LOG_FORMAT
from nodalarc.db.queries import (
    insert_snapshot,
    query_adapter_events,
    query_convergence_events,
    query_link_events,
    query_nearest_snapshot,
    query_probe_results,
)
from nodalarc.db.schema import create_tables
from nodalarc.models.events import PositionEvent
from nodalarc.models.link_events import LatencyUpdate, LinkDown, LinkUp
from nodalarc.models.metrics import AdapterEvent, ConvergenceResult, ProbeResult
from nodalarc.models.session import SessionConfig
from nodalarc.models.vs_api import (
    ActiveFlow,
    LinkState,
    NetworkHealth,
    NodeState,
    RecentEvent,
    StateSnapshot,
    TracedPath,
)
from nodalarc.models.metrics import TraceRequest, TraceResponse
from nodalarc.zmq_channels import (
    MI_EVENTS_CONNECT,
    MI_TRACE_CONNECT,
    OME_EVENTS_CONNECT,
    TO_EVENTS_CONNECT,
    decode_message,
    TOPIC_ADAPTER_EVENT,
    TOPIC_CONVERGENCE_RESULT,
    TOPIC_LATENCY_UPDATE,
    TOPIC_LINK_DOWN,
    TOPIC_LINK_UP,
    TOPIC_POSITION_EVENT,
    TOPIC_PROBE_RESULT,
)
from vs_api.session_manager import SessionManager

log = logging.getLogger(__name__)

# Module-level state (populated before app starts)
_state = {
    "nodes": {},  # node_id -> NodeState dict
    "links": {},  # "nodeA:nodeB" -> LinkState dict
    "recent_events": [],  # list of RecentEvent dicts (last 50)
    "active_flows": [],  # list of ActiveFlow dicts
    "network_health": {
        "status": "converged",
        "converging_since_ms": None,
        "unreachable_flows": 0,
        "last_convergence_ms": None,
    },
    "sim_time": datetime.now(timezone.utc).isoformat(),
}
_state_lock = threading.Lock()
_db_path: str = ""
_routing_stack: str | None = None
_constellation_name: str | None = None
_ws_clients: list[WebSocket] = []
_ws_lock = asyncio.Lock()
_session_manager: SessionManager | None = None


def _update_position(event_data: dict) -> None:
    """Update node positions from PositionEvent."""
    with _state_lock:
        _state["sim_time"] = event_data.get("sim_time", _state["sim_time"])
        for node in event_data.get("positions", []):
            node_id = node.get("node_id", "")
            if not node_id:
                continue
            _state["nodes"][node_id] = {
                "node_id": node_id,
                "node_type": node.get("node_type", "satellite"),
                "lat_deg": node.get("lat_deg", 0.0),
                "lon_deg": node.get("lon_deg", 0.0),
                "alt_km": node.get("alt_km", 0.0),
                "vel_x_km_s": node.get("vel_x_km_s"),
                "vel_y_km_s": node.get("vel_y_km_s"),
                "vel_z_km_s": node.get("vel_z_km_s"),
                "plane": node.get("plane"),
                "slot": node.get("slot"),
                "routing_area": node.get("routing_area"),
                "neighbor_count": node.get("neighbor_count", 0),
                "isl_count": node.get("isl_count", 0),
                "gnd_count": node.get("gnd_count", 0),
                "prefix": node.get("prefix"),
            }


def _update_link_up(event_data: dict) -> None:
    """Update link state on LinkUp."""
    key = _link_key(event_data.get("node_a", ""), event_data.get("node_b", ""))
    with _state_lock:
        _state["links"][key] = {
            "node_a": event_data.get("node_a", ""),
            "node_b": event_data.get("node_b", ""),
            "state": "active",
            "link_type": event_data.get("reason", ""),
            "link_reason": event_data.get("reason", ""),
            "latency_ms": event_data.get("latency_ms", 0.0),
            "bandwidth_mbps": event_data.get("bandwidth_mbps", 0.0),
            "range_km": 0.0,
            "traffic_load_pct": None,
        }


def _update_link_down(event_data: dict) -> None:
    """Remove link state on LinkDown."""
    key = _link_key(event_data.get("node_a", ""), event_data.get("node_b", ""))
    with _state_lock:
        _state["links"].pop(key, None)


def _update_latency(event_data: dict) -> None:
    """Update link latency."""
    key = _link_key(event_data.get("node_a", ""), event_data.get("node_b", ""))
    with _state_lock:
        if key in _state["links"]:
            _state["links"][key]["latency_ms"] = event_data.get("latency_ms", 0.0)
            _state["links"][key]["range_km"] = event_data.get("range_km", 0.0)


def _add_recent_event(event_data: dict, event_type: str) -> None:
    """Add to recent events list (cap at 50)."""
    with _state_lock:
        _state["recent_events"].append({
            "sim_time": event_data.get("sim_time", datetime.now(timezone.utc).isoformat()),
            "node_id": event_data.get("node_id", event_data.get("node_a", "")),
            "event_type": event_type,
            "summary": event_data.get("detail", event_data.get("reason", event_type)),
        })
        if len(_state["recent_events"]) > 50:
            _state["recent_events"] = _state["recent_events"][-50:]


def _update_convergence(event_data: dict) -> None:
    """Update network health from convergence result."""
    with _state_lock:
        if event_data.get("converged"):
            _state["network_health"]["status"] = "converged"
            _state["network_health"]["converging_since_ms"] = None
            _state["network_health"]["last_convergence_ms"] = event_data.get("duration_ms")
        else:
            _state["network_health"]["status"] = "converging"


def _link_key(node_a: str, node_b: str) -> str:
    return f"{min(node_a, node_b)}:{max(node_a, node_b)}"


def _build_snapshot() -> dict:
    """Build a StateSnapshot dict from current state."""
    with _state_lock:
        now = datetime.now(timezone.utc)
        nodes = [NodeState(**n) for n in _state["nodes"].values()]
        links = [LinkState(**l) for l in _state["links"].values()]
        recent = [RecentEvent(
            sim_time=datetime.fromisoformat(e["sim_time"]) if isinstance(e["sim_time"], str) else e["sim_time"],
            node_id=e["node_id"],
            event_type=e["event_type"],
            summary=e["summary"],
        ) for e in _state["recent_events"]]
        health = NetworkHealth(**_state["network_health"])

        snapshot = StateSnapshot(
            sim_time=datetime.fromisoformat(_state["sim_time"]) if isinstance(_state["sim_time"], str) else _state["sim_time"],
            wall_time=now,
            schema_version=1,
            nodes=nodes,
            links=links,
            traced_paths=[],
            active_flows=[],
            recent_events=recent,
            network_health=health,
            routing_stack=_routing_stack,
            constellation_name=_constellation_name,
            session_status=_session_manager.status if _session_manager else None,
            session_status_detail=_session_manager.status_detail if _session_manager else None,
        )
        return json.loads(snapshot.model_dump_json())


# --- ZMQ subscriber (asyncio per PRD 13.2) ---

_zmq_ctx: zmq.asyncio.Context | None = None


async def _zmq_subscriber() -> None:
    """Async ZMQ subscriber: subscribe to all ZMQ PUB sockets."""
    global _zmq_ctx
    _zmq_ctx = zmq.asyncio.Context()

    ome_sub = _zmq_ctx.socket(zmq.SUB)
    ome_sub.connect(OME_EVENTS_CONNECT)
    ome_sub.setsockopt(zmq.SUBSCRIBE, b"")

    to_sub = _zmq_ctx.socket(zmq.SUB)
    to_sub.connect(TO_EVENTS_CONNECT)
    to_sub.setsockopt(zmq.SUBSCRIBE, b"")

    mi_sub = _zmq_ctx.socket(zmq.SUB)
    mi_sub.connect(MI_EVENTS_CONNECT)
    mi_sub.setsockopt(zmq.SUBSCRIBE, b"")

    poller = zmq.asyncio.Poller()
    poller.register(ome_sub, zmq.POLLIN)
    poller.register(to_sub, zmq.POLLIN)
    poller.register(mi_sub, zmq.POLLIN)

    log.info("VS-API ZMQ subscriber started (asyncio)")

    try:
        while True:
            try:
                socks = dict(await poller.poll(timeout=100))
            except zmq.ZMQError:
                break

            for sock in [ome_sub, to_sub, mi_sub]:
                if sock not in socks:
                    continue
                raw = await sock.recv(zmq.NOBLOCK)
                try:
                    topic, payload = decode_message(raw)
                    data = json.loads(payload)

                    if topic == TOPIC_POSITION_EVENT:
                        _update_position(data)
                    elif topic == TOPIC_LINK_UP:
                        _update_link_up(data)
                        _add_recent_event(data, "link_up")
                    elif topic == TOPIC_LINK_DOWN:
                        _update_link_down(data)
                        _add_recent_event(data, "link_down")
                    elif topic == TOPIC_LATENCY_UPDATE:
                        _update_latency(data)
                    elif topic == TOPIC_CONVERGENCE_RESULT:
                        _update_convergence(data)
                        _add_recent_event(data, "convergence")
                    elif topic == TOPIC_ADAPTER_EVENT:
                        _add_recent_event(data, data.get("event_type", "adapter"))
                    elif topic == TOPIC_PROBE_RESULT:
                        pass  # Probe results don't update snapshot state directly

                except Exception as exc:
                    log.warning(f"ZMQ message processing error: {exc}")
    except asyncio.CancelledError:
        pass
    finally:
        ome_sub.close()
        to_sub.close()
        mi_sub.close()


# --- FastAPI app ---

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Start ZMQ subscriber and WebSocket broadcaster on startup."""
    # Start ZMQ subscriber as asyncio task (PRD 13.2)
    sub_task = asyncio.create_task(_zmq_subscriber())

    # Start WebSocket broadcaster
    broadcast_task = asyncio.create_task(_ws_broadcaster())

    yield

    sub_task.cancel()
    broadcast_task.cancel()
    if _zmq_ctx is not None:
        _zmq_ctx.term()


app = FastAPI(title="Nodal Arc VS-API", version="1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


def _clear_state() -> None:
    """Reset in-memory state during session switch."""
    with _state_lock:
        _state["nodes"].clear()
        _state["links"].clear()
        _state["recent_events"].clear()
        _state["active_flows"].clear()
        _state["network_health"] = {
            "status": "converged",
            "converging_since_ms": None,
            "unreachable_flows": 0,
            "last_convergence_ms": None,
        }
        _state["sim_time"] = datetime.now(timezone.utc).isoformat()


def _update_session_globals(session_path: str, new_db_path: str) -> None:
    """Reload routing_stack, constellation_name, and db_path from new session."""
    global _routing_stack, _constellation_name, _db_path
    session_data = yaml.safe_load(Path(session_path).read_text())
    session = SessionConfig.model_validate(session_data)
    _routing_stack = Path(session.routing.stack).name
    _constellation_name = Path(session.constellation).stem
    _db_path = new_db_path

    # Ensure tables exist in new DB
    import sqlite3 as _sqlite3
    conn = _sqlite3.connect(new_db_path)
    create_tables(conn)
    conn.close()


async def _ws_broadcaster() -> None:
    """Broadcast StateSnapshot to all WebSocket clients at ~1Hz.

    Also records snapshots to SQLite every 10 seconds for historical playback.
    """
    tick = 0
    while True:
        await asyncio.sleep(1.0)
        snapshot = _build_snapshot()

        # Store snapshot every 10 ticks (~10 seconds) for historical playback
        tick += 1
        if tick % 10 == 0 and _db_path:
            try:
                conn = sqlite3.connect(_db_path)
                insert_snapshot(
                    conn,
                    sim_time=snapshot["sim_time"],
                    wall_time=snapshot["wall_time"],
                    snapshot_json=json.dumps(snapshot),
                )
                conn.close()
            except Exception as exc:
                log.warning(f"Failed to store snapshot: {exc}")

        async with _ws_lock:
            dead = []
            for ws in _ws_clients:
                try:
                    await ws.send_json(snapshot)
                except Exception:
                    dead.append(ws)
            for ws in dead:
                _ws_clients.remove(ws)


@app.websocket("/ws/v1/state")
async def ws_state(websocket: WebSocket) -> None:
    """WebSocket endpoint — full state snapshots at ~1Hz."""
    await websocket.accept()
    async with _ws_lock:
        _ws_clients.append(websocket)
    log.info("WebSocket client connected")

    # Send initial snapshot immediately
    try:
        snapshot = _build_snapshot()
        await websocket.send_json(snapshot)
    except Exception:
        pass

    try:
        while True:
            # Keep connection alive — client sends nothing
            await websocket.receive_text()
    except WebSocketDisconnect:
        log.info("WebSocket client disconnected")
    finally:
        async with _ws_lock:
            if websocket in _ws_clients:
                _ws_clients.remove(websocket)


@app.get("/api/v1/state")
def get_state() -> dict:
    """Current state snapshot."""
    return _build_snapshot()


@app.get("/api/v1/state/{sim_time}")
def get_historical_state(sim_time: str) -> dict:
    """Historical state at a specific sim_time (nearest snapshot from SQLite)."""
    if not _db_path:
        return {"error": "No database configured"}
    conn = sqlite3.connect(_db_path)
    try:
        result = query_nearest_snapshot(conn, sim_time)
        if result is None:
            return JSONResponse(status_code=404, content={"error": "No snapshots available"})
        return json.loads(result["snapshot_json"])
    finally:
        conn.close()


@app.get("/api/v1/links")
def get_link_events(
    start: str = Query(None),
    end: str = Query(None),
) -> list[dict]:
    """Query link events from SQLite."""
    if not _db_path:
        return []
    conn = sqlite3.connect(_db_path)
    try:
        return query_link_events(conn, start_time=start, end_time=end)
    finally:
        conn.close()


@app.get("/api/v1/metrics/convergence")
def get_convergence_events(
    start: str = Query(None),
    end: str = Query(None),
) -> list[dict]:
    """Query convergence events from SQLite."""
    if not _db_path:
        return []
    conn = sqlite3.connect(_db_path)
    try:
        return query_convergence_events(conn)
    finally:
        conn.close()


@app.get("/api/v1/metrics/flows/{flow_id}")
def get_flow_metrics(
    flow_id: str,
    start: str = Query(None),
    end: str = Query(None),
) -> list[dict]:
    """Query probe results for a flow from SQLite."""
    if not _db_path:
        return []
    conn = sqlite3.connect(_db_path)
    try:
        return query_probe_results(conn, flow_id=flow_id, start_time=start, end_time=end)
    finally:
        conn.close()


@app.post("/api/v1/trace")
def trace_path(body: dict) -> dict:
    """Request path trace via ZMQ REQ to MI trace endpoint."""
    src = body.get("src_node", "")
    dst = body.get("dst_node", "")
    if not src or not dst:
        return {"hops": [], "error": "src_node and dst_node required"}

    ctx = zmq.Context()
    sock = ctx.socket(zmq.REQ)
    sock.setsockopt(zmq.RCVTIMEO, 10000)  # 10s timeout
    sock.setsockopt(zmq.LINGER, 0)
    sock.connect(MI_TRACE_CONNECT)

    try:
        req = TraceRequest(src_node=src, dst_node=dst)
        sock.send(req.model_dump_json().encode())
        raw = sock.recv()
        resp = TraceResponse.model_validate_json(raw)
        result = {"hops": resp.hops, "success": resp.success}
        if resp.error:
            result["error"] = resp.error
        return result
    except zmq.Again:
        return {"hops": [], "error": "MI trace endpoint timeout"}
    except Exception as exc:
        return {"hops": [], "error": str(exc)}
    finally:
        sock.close()
        ctx.term()


@app.get("/api/v1/sessions")
def list_sessions() -> list[dict]:
    """List available sessions with active flag."""
    if _session_manager is None:
        return []
    return _session_manager.list_sessions()


@app.post("/api/v1/sessions/switch", response_model=None)
async def switch_session(body: dict):
    """Trigger async session switch. Returns immediately."""
    if _session_manager is None:
        return JSONResponse(status_code=503, content={"error": "Session manager not initialized"})
    if _session_manager.status == "switching":
        return JSONResponse(status_code=409, content={"error": "Switch already in progress"})
    session_path = body.get("session", "")
    if not session_path:
        return JSONResponse(status_code=400, content={"error": "session field required"})
    valid_files = _session_manager._valid_session_files()
    if session_path not in valid_files:
        return JSONResponse(status_code=400, content={"error": "Unknown session file"})
    asyncio.create_task(_run_switch(session_path))
    return {"status": "switching"}


async def _run_switch(session_path: str) -> None:
    """Run session switch in thread executor (blocking subprocess calls)."""
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(
        None,
        _session_manager.switch,
        session_path,
        _clear_state,
        _update_session_globals,
    )


def main() -> None:
    import uvicorn

    logging.basicConfig(format=LOG_FORMAT, level=logging.INFO)
    parser = argparse.ArgumentParser(description="VS-API server")
    parser.add_argument("--session", default=None, help="Path to session YAML (optional)")
    parser.add_argument("--db", default=None, help="Path to SQLite database (optional)")
    from nodalarc.zmq_channels import VS_API_HTTP_PORT
    parser.add_argument("--port", type=int, default=VS_API_HTTP_PORT, help="HTTP port")
    parser.add_argument("--sessions-dir", default="configs/sessions", help="Directory with session YAMLs")
    args = parser.parse_args()

    global _db_path, _routing_stack, _constellation_name, _session_manager

    # Initialize SessionManager
    _session_manager = SessionManager(args.sessions_dir, initial_db_path=args.db)

    if args.session and args.db:
        _db_path = args.db

        # Load session metadata for snapshot enrichment
        session_data = yaml.safe_load(Path(args.session).read_text())
        session = SessionConfig.model_validate(session_data)
        _routing_stack = Path(session.routing.stack).name
        _constellation_name = Path(session.constellation).stem
        _session_manager.set_active(args.session)
        _session_manager._status = "ready"

        # Ensure tables exist
        conn = sqlite3.connect(args.db)
        create_tables(conn)
        conn.close()

    uvicorn.run(app, host="0.0.0.0", port=args.port, log_level="info")


if __name__ == "__main__":
    main()
