"""Integration test verification helpers.

Called by na-integration-test.sh for WebSocket and data flow checks.
"""

from __future__ import annotations

import asyncio
import json
import sys
import time


async def read_ws_snapshots(
    host: str, port: int, token: str, count: int, max_seconds: float,
    min_interval: float = 0.0,
) -> dict:
    """Connect to VS-API WebSocket and read N snapshots.

    If min_interval > 0, wait at least that many seconds between keeping
    snapshots (intermediate messages are read and discarded).  This ensures
    collected snapshots span a wide enough time window for sim_time to advance.
    """
    import websockets

    url = f"ws://{host}:{port}/ws/v1/state"
    if token:
        url += f"?token={token}"

    snapshots = []
    start = time.monotonic()
    try:
        async with asyncio.timeout(max_seconds):
            async with websockets.connect(url) as ws:
                last_keep = 0.0
                while len(snapshots) < count:
                    msg = await asyncio.wait_for(ws.recv(), timeout=5.0)
                    now = time.monotonic()
                    if min_interval > 0 and last_keep > 0 and (now - last_keep) < min_interval:
                        continue  # discard, too soon after last kept snapshot
                    snap = json.loads(msg)
                    snapshots.append(snap)
                    last_keep = now
    except Exception as e:
        return {
            "error": str(e),
            "count": len(snapshots),
            "snapshots": snapshots,
        }
    elapsed = time.monotonic() - start
    return {"ok": True, "elapsed": elapsed, "snapshots": snapshots}


def verify_snapshots(
    snapshots: list[dict],
    elapsed: float,
    expected_sat_count: int,
    expected_constellation: str,
) -> list[str]:
    """Verify data flow properties across snapshots. Returns list of errors."""
    errors = []

    for i, s in enumerate(snapshots):
        nodes = s.get("nodes", [])
        links = s.get("links", [])
        status = s.get("session_status", "")
        rstack = s.get("routing_stack")
        cname = s.get("constellation_name")

        if not nodes:
            errors.append(f"Snapshot {i}: nodes list empty")
        if not links:
            errors.append(f"Snapshot {i}: links list empty")
        if status != "ready":
            errors.append(f"Snapshot {i}: session_status={status} (expected ready)")
        if not rstack:
            errors.append(f"Snapshot {i}: routing_stack is null")
        if not cname:
            errors.append(f"Snapshot {i}: constellation_name is null")

    # Constellation name check
    actual_c = snapshots[0].get("constellation_name", "")
    if expected_constellation and actual_c and expected_constellation != actual_c:
        errors.append(
            f"constellation_name mismatch: expected={expected_constellation} actual={actual_c}"
        )

    # Satellite count
    for i, s in enumerate(snapshots):
        sat_nodes = [
            n for n in s.get("nodes", []) if n.get("node_type") == "satellite"
        ]
        if len(sat_nodes) != expected_sat_count:
            errors.append(
                f"Snapshot {i}: satellite count={len(sat_nodes)} expected={expected_sat_count}"
            )
            break

    # sim_time must advance
    sim_times = [s.get("sim_time", "") for s in snapshots]
    if sim_times[0] == sim_times[-1]:
        errors.append(
            f"sim_time frozen: all {len(snapshots)} snapshots have sim_time={sim_times[0]}"
        )
        errors.append(f"All sim_times: {sim_times}")

    # wall_time must advance
    wall_times = [s.get("wall_time", "") for s in snapshots]
    if wall_times[0] == wall_times[-1]:
        errors.append(f"wall_time frozen: {wall_times[0]}")

    # Position must change
    first_nodes = {
        n["node_id"]: (n.get("lat_deg"), n.get("lon_deg"))
        for n in snapshots[0].get("nodes", [])
        if n.get("node_type") == "satellite"
    }
    position_changed = False
    for s in snapshots[1:]:
        for n in s.get("nodes", []):
            if n.get("node_type") != "satellite":
                continue
            nid = n["node_id"]
            if nid in first_nodes:
                orig = first_nodes[nid]
                if (n.get("lat_deg"), n.get("lon_deg")) != orig:
                    position_changed = True
                    break
        if position_changed:
            break
    if not position_changed:
        first_sat_id = list(first_nodes.keys())[0] if first_nodes else "none"
        positions = []
        for s in snapshots:
            for n in s.get("nodes", []):
                if n["node_id"] == first_sat_id:
                    positions.append((n.get("lat_deg"), n.get("lon_deg")))
                    break
        errors.append("Satellite positions frozen across all snapshots")
        errors.append(f"First satellite ({first_sat_id}) positions: {positions}")

    return errors


async def read_ws_with_origin_header(
    host: str, port: int, token: str, count: int, max_seconds: float,
    origin: str, min_interval: float = 0.0,
) -> dict:
    """Like read_ws_snapshots but passes an Origin header, simulating a browser."""
    import websockets

    url = f"ws://{host}:{port}/ws/v1/state"
    if token:
        url += f"?token={token}"

    snapshots = []
    start = time.monotonic()
    try:
        async with asyncio.timeout(max_seconds):
            async with websockets.connect(
                url, additional_headers={"Origin": origin}
            ) as ws:
                last_keep = 0.0
                while len(snapshots) < count:
                    msg = await asyncio.wait_for(ws.recv(), timeout=5.0)
                    now = time.monotonic()
                    if min_interval > 0 and last_keep > 0 and (now - last_keep) < min_interval:
                        continue
                    snap = json.loads(msg)
                    snapshots.append(snap)
                    last_keep = now
    except Exception as e:
        return {
            "error": str(e),
            "count": len(snapshots),
            "snapshots": snapshots,
        }
    elapsed = time.monotonic() - start
    return {"ok": True, "elapsed": elapsed, "snapshots": snapshots}


def cmd_read_ws():
    """Read WebSocket snapshots. Args: host port token count max_seconds [min_interval]"""
    host = sys.argv[2]
    port = int(sys.argv[3])
    token = sys.argv[4]
    count = int(sys.argv[5])
    max_seconds = float(sys.argv[6])
    min_interval = float(sys.argv[7]) if len(sys.argv) > 7 else 0.0
    result = asyncio.run(read_ws_snapshots(host, port, token, count, max_seconds, min_interval))
    print(json.dumps(result))
    sys.exit(0 if result.get("ok") else 1)


def cmd_read_ws_with_origin():
    """Read WebSocket snapshots with Origin header. Args: host port token count max_seconds origin [min_interval]"""
    host = sys.argv[2]
    port = int(sys.argv[3])
    token = sys.argv[4]
    count = int(sys.argv[5])
    max_seconds = float(sys.argv[6])
    origin = sys.argv[7]
    min_interval = float(sys.argv[8]) if len(sys.argv) > 8 else 0.0
    result = asyncio.run(read_ws_with_origin_header(host, port, token, count, max_seconds, origin, min_interval))
    print(json.dumps(result))
    sys.exit(0 if result.get("ok") else 1)


def cmd_verify():
    """Verify snapshots from JSON file. Args: json_file expected_sats expected_constellation"""
    json_file = sys.argv[2]
    expected_sats = int(sys.argv[3])
    expected_constellation = sys.argv[4]

    with open(json_file) as f:
        data = json.load(f)

    if "error" in data and not data.get("ok"):
        print(
            f"FAIL: WebSocket error: {data['error']} (got {data.get('count', 0)} snapshots)"
        )
        sys.exit(1)

    snaps = data["snapshots"]
    elapsed = data.get("elapsed", 0)
    errors = verify_snapshots(snaps, elapsed, expected_sats, expected_constellation)

    if errors:
        print("FAIL: Data flow verification errors:")
        for e in errors:
            print(f"  - {e}")
        sim_times = [s.get("sim_time", "") for s in snaps]
        wall_times = [s.get("wall_time", "") for s in snaps]
        sat_nodes = [
            n
            for n in snaps[0].get("nodes", [])
            if n.get("node_type") == "satellite"
        ]
        print(f"sim_times: {sim_times}")
        print(f"wall_times: {wall_times}")
        print(f"elapsed: {elapsed:.1f}s")
        print(f"satellite_count: {len(sat_nodes)}")
        print(f"expected_sat_count: {expected_sats}")
        sys.exit(1)
    else:
        sat_nodes = [
            n
            for n in snaps[0].get("nodes", [])
            if n.get("node_type") == "satellite"
        ]
        print(
            f"OK: {len(snaps)} snapshots verified ({elapsed:.1f}s), sim_time advancing, positions changing, {len(sat_nodes)} satellites"
        )
        sys.exit(0)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: na_integration_verify.py <read_ws|verify> [args...]")
        sys.exit(1)
    cmd = sys.argv[1]
    if cmd == "read_ws":
        cmd_read_ws()
    elif cmd == "read_ws_with_origin":
        cmd_read_ws_with_origin()
    elif cmd == "verify":
        cmd_verify()
    else:
        print(f"Unknown command: {cmd}")
        sys.exit(1)
