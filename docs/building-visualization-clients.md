# Building Visualization Clients

How to build alternative frontends or dashboards that consume NodalArc constellation data.

## Architecture

All visualization data flows through the VS-API. Your client connects to VS-API over HTTP/WebSocket. It never talks directly to the OME, Scheduler, or any other backend component.

```
OME ──NATS──┐
Scheduler ──NATS──┤──> VS-API ──HTTP/WS──> Your Client
MI ──NATS──┘
```

## Authentication

All endpoints except `/api/v1/health` and `/api/v1/auth/token` require a Bearer token.

Fetch the token (unauthenticated):
```bash
curl -s http://localhost:8080/api/v1/auth/token
# Returns: {"token": "..."}
```

Use it in HTTP headers: `Authorization: Bearer <token>`

Use it in WebSocket URLs: `ws://host:8080/ws/v1/state?token=<token>`

## WebSocket: Real-Time State Stream

**URL:** `ws://<host>:8080/ws/v1/state?token=<token>`

Delivers a full JSON snapshot of the entire constellation at ~1 Hz. Every message contains the complete state (all nodes, all links, all events). There are no delta updates.

On connect, the server sends one snapshot immediately, then continues at ~1 Hz.

### Python Example

```python
import asyncio
import json
import urllib.request
import websockets

token = json.loads(
    urllib.request.urlopen("http://localhost:8080/api/v1/auth/token").read()
)["token"]

async def main():
    url = f"ws://localhost:8080/ws/v1/state?token={token}"
    async with websockets.connect(url) as ws:
        async for message in ws:
            snap = json.loads(message)
            sats = sum(1 for n in snap["nodes"] if n["node_type"] == "satellite")
            links = len(snap["links"])
            print(f"[{snap['sim_time'][:19]}] {sats} satellites, {links} links")

asyncio.run(main())
```

### JavaScript Example

```javascript
const resp = await fetch("http://localhost:8080/api/v1/auth/token");
const { token } = await resp.json();

const ws = new WebSocket(`ws://localhost:8080/ws/v1/state?token=${token}`);

ws.onmessage = (event) => {
  const snap = JSON.parse(event.data);
  console.log(snap.sim_time, snap.nodes.length, "nodes", snap.links.length, "links");
};

ws.onclose = () => setTimeout(() => location.reload(), 2000);
```

## REST Endpoints

Base URL: `http://<host>:8080`. All require `Authorization: Bearer <token>` header.

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/v1/state` | Current full snapshot (same format as WebSocket) |
| GET | `/api/v1/state/{sim_time}` | Historical snapshot nearest to given ISO 8601 time |
| GET | `/api/v1/links` | Link events with optional `?start=` and `?end=` time filters |
| POST | `/api/v1/trace` | Path trace between two nodes |

## Snapshot Format

Here is a real snapshot from a running 176-satellite constellation. This is the exact JSON your client receives over WebSocket or from `GET /api/v1/state`.

### Top-Level Fields

```json
{
  "sim_time": "2026-04-03T19:48:03.567875Z",
  "wall_time": "2026-04-03T19:49:34.619726Z",
  "schema_version": 1,
  "session_status": "ready",
  "playback_paused": false,
  "playback_speed": 1.0,
  "stale": false,
  "routing_stack": "isis-traffic-engineering",
  "constellation_name": "constellation",
  "nodes": [ ... ],
  "links": [ ... ],
  "recent_events": [ ... ],
  "network_health": { ... },
  "traced_paths": [],
  "active_flows": []
}
```

| Field | Type | Description |
|-------|------|-------------|
| `sim_time` | string | Current simulation time (ISO 8601) |
| `wall_time` | string | Current wall-clock time |
| `schema_version` | int | Always 1. Will increment if the format changes. |
| `session_status` | string | `"ready"`, `"creating"`, `"wiring"`, or `"idle"` |
| `playback_paused` | bool | Whether the simulation is paused |
| `playback_speed` | float | Time compression factor (1.0 = real-time) |
| `stale` | bool | True if no OME data received recently |
| `routing_stack` | string | Active routing stack name |
| `constellation_name` | string | Active constellation name |

### Node Object

Satellite:
```json
{
  "node_id": "sat-P00S00",
  "node_type": "satellite",
  "lat_deg": 42.95,
  "lon_deg": -84.98,
  "alt_km": 552.75,
  "vel_x_km_s": 5.57,
  "vel_y_km_s": 3.45,
  "vel_z_km_s": 3.19,
  "plane": 0,
  "slot": 0,
  "isl_count": 4,
  "gnd_count": 1,
  "neighbor_count": 0,
  "routing_area": null,
  "prefix": null,
  "min_elevation_deg": null,
  "beam_falloff_exponent": 2.0
}
```

Ground station:
```json
{
  "node_id": "gs-hawthorne",
  "node_type": "ground_station",
  "lat_deg": 33.92,
  "lon_deg": -118.33,
  "alt_km": 0.02,
  "vel_x_km_s": 0.0,
  "vel_y_km_s": 0.0,
  "vel_z_km_s": 0.0,
  "plane": null,
  "slot": null,
  "isl_count": 0,
  "gnd_count": 1,
  "neighbor_count": 0,
  "routing_area": null,
  "prefix": null,
  "min_elevation_deg": 15.0,
  "beam_falloff_exponent": null
}
```

| Field | Type | Description |
|-------|------|-------------|
| `node_id` | string | Stable identifier. `sat-P{plane}S{slot}` for satellites, `gs-{name}` for ground stations |
| `node_type` | string | `"satellite"` or `"ground_station"` |
| `lat_deg`, `lon_deg` | float | WGS84 position in degrees |
| `alt_km` | float | Altitude above sea level in km |
| `vel_x_km_s`, `vel_y_km_s`, `vel_z_km_s` | float | ECEF velocity in km/s (0 for ground stations) |
| `plane`, `slot` | int or null | Orbital plane and slot index (null for ground stations) |
| `isl_count` | int | Number of active ISL links on this node |
| `gnd_count` | int | Number of active ground links on this node |
| `neighbor_count` | int | Total routing neighbor count |
| `min_elevation_deg` | float or null | Minimum satellite elevation angle (ground stations only) |
| `beam_falloff_exponent` | float or null | Signal degradation model parameter |

### Link Object

ISL link (between two satellites):
```json
{
  "node_a": "sat-P00S00",
  "node_b": "sat-P00S01",
  "state": "active",
  "link_type": "intra_plane_isl",
  "link_reason": "",
  "latency_ms": 13.01,
  "bandwidth_mbps": 1000.0,
  "range_km": 0.0,
  "traffic_load_pct": null
}
```

Ground link (satellite to ground station):
```json
{
  "node_a": "gs-frankfurt",
  "node_b": "sat-P02S01",
  "state": "active",
  "link_type": "ground",
  "link_reason": "",
  "latency_ms": 2.50,
  "bandwidth_mbps": 1000.0,
  "range_km": 0.0,
  "traffic_load_pct": null
}
```

| Field | Type | Description |
|-------|------|-------------|
| `node_a`, `node_b` | string | Endpoint node IDs. `node_a < node_b` alphabetically. |
| `state` | string | `"active"` |
| `link_type` | string | `"intra_plane_isl"`, `"cross_plane_isl"`, or `"ground"` |
| `link_reason` | string | Why this link was created (may be empty) |
| `latency_ms` | float | One-way propagation delay in milliseconds |
| `bandwidth_mbps` | float | Link capacity |
| `range_km` | float | Physical distance between endpoints |
| `traffic_load_pct` | float or null | Traffic load percentage (null if no probe data) |

### Event Object

```json
{
  "sim_time": "2026-04-03T19:34:58.567875Z",
  "node_id": "gs-ashburn",
  "event_type": "link_down",
  "summary": "vis_lost"
}
```

The `recent_events` array contains the last 50 events. Event types include `link_up`, `link_down`, and `latency_update`.

### Network Health

```json
{
  "status": "converged",
  "converging_since_ms": null,
  "unreachable_flows": 0,
  "last_convergence_ms": null
}
```

## Design Rules for Clients

**Drop frames if behind.** If your render loop takes longer than 1 second, discard intermediate snapshots. Never queue them. Always render the most recent one.

**Handle reconnection.** The WebSocket server does not persist client state. On disconnect, reconnect and resume from the next snapshot.

**Check `schema_version`.** Currently 1. Future versions may add fields. Ignore unknown fields, don't reject them.

**Interpolate positions.** Satellites move between 1 Hz snapshots. For smooth rendering, interpolate node positions toward the latest target:
```
current = lerp(current, target, 1 - e^(-speed * dt))
```

**Node IDs are stable keys.** Use `node_id` as your dictionary key. Satellite IDs are `sat-P{plane:02d}S{slot:02d}` (e.g., `sat-P00S00`). Ground station IDs are `gs-{name}` (e.g., `gs-hawthorne`). These never change within a session.

**Link keys are ordered pairs.** `node_a` is always alphabetically less than `node_b`. Use `(node_a, node_b)` as your link key.
