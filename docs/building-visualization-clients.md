# Building Visualization Clients

How to build alternative frontends that consume Nodal Arc state data.

## Architecture Constraint

All visualization data flows through VS-API. No visualization client may communicate directly with OME, the Scheduler, or MI. VS-API is the single gateway. It subscribes to NATS JetStream internally and exposes a unified HTTP/WebSocket interface.

```
OME ──NATS──┐
Scheduler ──NATS──┤──▶ VS-API ──HTTP/WS──▶ Your Client
MI ──NATS──┘
```

## WebSocket Endpoint

**URL:** `ws://<host>:8080/ws/v1/state`

Delivers a full `StateSnapshot` JSON payload at approximately 1 Hz. The server sends the complete state each time. There are no delta updates, no patch messages, and no incremental encoding.

On connection, the server immediately sends one snapshot before entering the 1 Hz broadcast loop.

WebSocket connections require a token passed as a query parameter. Fetch the token from the unauthenticated `/api/v1/auth/token` endpoint first.

### Connection Example (Python)

```python
import asyncio
import json
import urllib.request
import websockets

# Fetch the API token
token = json.loads(urllib.request.urlopen("http://localhost:8080/api/v1/auth/token").read())["token"]

async def main():
    async with websockets.connect(f"ws://localhost:8080/ws/v1/state?token={token}") as ws:
        async for message in ws:
            snapshot = json.loads(message)
            print(f"sim_time={snapshot['sim_time']} "
                  f"nodes={len(snapshot['nodes'])} "
                  f"links={len(snapshot['links'])}")

asyncio.run(main())
```

### Connection Example (Browser JavaScript)

```javascript
// Fetch the API token first
const tokenResp = await fetch("http://localhost:8080/api/v1/auth/token");
const { token } = await tokenResp.json();

const ws = new WebSocket(`ws://localhost:8080/ws/v1/state?token=${token}`);

ws.onmessage = (event) => {
  const snapshot = JSON.parse(event.data);
  console.log(`sim_time=${snapshot.sim_time}`,
              `nodes=${snapshot.nodes.length}`,
              `links=${snapshot.links.length}`);
  // Render your UI with snapshot data
};

ws.onclose = () => {
  // Reconnect after a delay
  setTimeout(() => location.reload(), 2000);
};
```

## REST Endpoints

All REST endpoints return JSON and require a Bearer token in the `Authorization` header (see [VS-API Reference](vs-api-reference.md#authentication) for details). Base URL: `http://<host>:8080`.

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/v1/state` | Current state snapshot (same format as WebSocket) |
| GET | `/api/v1/state/{sim_time}` | Historical snapshot nearest to given sim_time (ISO 8601) |
| GET | `/api/v1/links?start=<t>&end=<t>` | Link events (LinkUp, LinkDown, LatencyUpdate) with optional time range |
| GET | `/api/v1/metrics/convergence` | All convergence events |
| GET | `/api/v1/metrics/flows/{flow_id}?start=<t>&end=<t>` | Probe results for a specific flow with optional time range |
| POST | `/api/v1/trace` | Request path trace (body: `{"src_node": "...", "dst_node": "..."}`) |

### Historical Playback

Historical snapshots are stored to SQLite every ~10 seconds. Query with `GET /api/v1/state/{sim_time}` where `sim_time` is an ISO 8601 timestamp. The server returns the nearest available snapshot.

### Path Tracing

`POST /api/v1/trace` sends a path trace request via NATS and returns the forwarding path:

```json
{
  "src_node": "sat-P00S00",
  "dst_node": "sat-P03S05"
}
```

Response:

```json
{
  "hops": ["sat-P00S00", "sat-P01S00", "sat-P02S02", "sat-P03S05"],
  "success": true
}
```

## StateSnapshot Schema

The full JSON Schema is at `vs_api/schema/snapshot_v1.json` in the repository. Key fields:

| Field | Type | Description |
|-------|------|-------------|
| `sim_time` | datetime | Simulation time |
| `wall_time` | datetime | Real wall-clock time |
| `schema_version` | int | Always 1 for Phase 1 |
| `nodes` | NodeState[] | All satellite and ground station positions |
| `links` | LinkState[] | All active links with latency and bandwidth |
| `traced_paths` | TracedPath[] | Active path traces |
| `active_flows` | ActiveFlow[] | Configured traffic flows |
| `recent_events` | RecentEvent[] | Last 50 events for the event log |
| `network_health` | NetworkHealth | Overall convergence status |
| `routing_stack` | string? | Active routing stack name |
| `constellation_name` | string? | Active constellation name |

### NodeState Fields

Each node has: `node_id`, `node_type` ("satellite" or "ground_station"), `lat_deg`, `lon_deg`, `alt_km`, velocity components (`vel_x_km_s`, `vel_y_km_s`, `vel_z_km_s`), `plane`, `slot`, `routing_area`, `neighbor_count`, `isl_count`, `gnd_count`, and optional `prefix`.

### LinkState Fields

Each link has: `node_a`, `node_b`, `state` ("active"), `link_type`, `link_reason`, `latency_ms`, `bandwidth_mbps`, `range_km`, and `traffic_load_pct` (null if no probe data).

## Design Rules

1. **Drop frames if behind.** If your render loop takes longer than 1 second, discard intermediate snapshots. Never queue snapshots. Always render the most recent one.

2. **Handle reconnection.** The WebSocket server does not persist client state. On disconnect, reconnect and resume from the next snapshot. There is no replay mechanism over WebSocket.

3. **Use `schema_version` for forward compatibility.** Check that `schema_version == 1`. Future versions may add fields. Unknown fields should be ignored, not rejected.

4. **Interpolate positions client-side.** Satellites move between 1 Hz snapshots. For smooth rendering, lerp node positions toward the latest target using exponential convergence in your render loop:
   ```
   current = lerp(current, target, 1 - e^(-speed * dt))
   ```

5. **Node IDs are stable identifiers.** Satellite IDs follow `sat-P{plane:02d}S{slot:02d}` (e.g., `sat-P00S00`). Ground station IDs follow `gs-{name}` (e.g., `gs-hawthorne`). Use these as dictionary keys for your node state.

6. **Link keys are unordered pairs.** `node_a < node_b` alphabetically. A link between `sat-P00S00` and `sat-P01S00` always appears with `node_a="sat-P00S00"`, `node_b="sat-P01S00"`.
