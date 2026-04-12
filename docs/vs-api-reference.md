# VS-API Reference

The Visualization State API (VS-API) is a FastAPI server that aggregates constellation state from all backend components and serves it to the VF and other clients.

**Default port:** 8080

## Authentication

All API endpoints (except `/api/v1/health` and `/api/v1/auth/token`) require a Bearer token. The VS-API auto-generates a token on startup. Fetch it:

```bash
TOKEN=$(curl -s http://localhost:8080/api/v1/auth/token | python3 -c "import json,sys; print(json.load(sys.stdin)['token'])")
```

Use it in the `Authorization` header on all subsequent requests:

```bash
curl -s -H "Authorization: Bearer $TOKEN" http://localhost:8080/api/v1/state
```

To use a fixed token instead of auto-generated, set the `NODAL_API_KEY` environment variable on the VS-API deployment.

WebSocket connections pass the token as a query parameter: `ws://host:8080/ws/v1/state?token=YOUR_TOKEN`

## Quick Examples

All examples below assume `$TOKEN` is set as shown above.

### Get the full constellation state

```bash
curl -s -H "Authorization: Bearer $TOKEN" http://localhost:8080/api/v1/state | python3 -m json.tool
```

### Count satellites, ground stations, and active links

```bash
curl -s -H "Authorization: Bearer $TOKEN" http://localhost:8080/api/v1/state | python3 -c "
import json, sys
s = json.load(sys.stdin)
sats = sum(1 for n in s['nodes'] if n['node_type'] == 'satellite')
gs = sum(1 for n in s['nodes'] if n['node_type'] == 'ground_station')
print(f'{sats} satellites, {gs} ground stations, {len(s[\"links\"])} links')
"
```

### Find active ground station connections

```bash
curl -s -H "Authorization: Bearer $TOKEN" http://localhost:8080/api/v1/state | python3 -c "
import json, sys
s = json.load(sys.stdin)
for link in s['links']:
    if link['link_type'] and 'ground' in link['link_type']:
        print(f\"{link['node_a']} <-> {link['node_b']}  latency={link['latency_ms']:.1f}ms  range={link['range_km']:.0f}km\")
"
```

### Look up a specific satellite

```bash
curl -s -H "Authorization: Bearer $TOKEN" http://localhost:8080/api/v1/state | python3 -c "
import json, sys
s = json.load(sys.stdin)
node = next((n for n in s['nodes'] if n['node_id'] == 'sat-P00S00'), None)
if node:
    print(f\"Position: {node['lat_deg']:.2f}N, {node['lon_deg']:.2f}E, {node['alt_km']:.0f}km\")
    print(f\"Neighbors: {node['neighbor_count']} (ISL: {node['isl_count']}, Ground: {node['gnd_count']})\")
"
```

### Trace the forwarding path between two nodes

```bash
curl -s -X POST -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  http://localhost:8080/api/v1/trace \
  -d '{"src_node": "gs-hawthorne", "dst_node": "gs-frankfurt"}'
```

### Query link events in a time window

```bash
curl -s -H "Authorization: Bearer $TOKEN" \
  'http://localhost:8080/api/v1/links?start=2026-01-01T00:00:00Z&end=2026-01-01T00:10:00Z'
```

### Stream live state over WebSocket

```bash
python3 -c "
import asyncio, json, websockets, urllib.request
token = json.loads(urllib.request.urlopen('http://localhost:8080/api/v1/auth/token').read())['token']
async def main():
    async with websockets.connect(f'ws://localhost:8080/ws/v1/state?token={token}') as ws:
        async for msg in ws:
            s = json.loads(msg)
            links = len(s['links'])
            print(f\"[{s['sim_time'][:19]}] {links} links active\")
asyncio.run(main())
"
```

## WebSocket Endpoint

### `ws://host:8080/ws/v1/state`

Constellation state pushed at ~1Hz. On connect, the server sends the cached `SessionEphemeris` (orbital elements for local propagation, `msg_type: "session_ephemeris"`) followed by the current link state snapshot. Subsequent messages at ~1Hz include link state updates, latency changes, and events. Position data is NOT pushed per-tick — the browser runs local Keplerian propagation from the ephemeris.

**Connection flow:**
1. Client connects to the WebSocket endpoint
2. Server sends `SessionEphemeris` immediately on connect (contains orbital elements for all satellites, fixed positions for all ground stations)
3. Server sends current link state and node metadata
4. Server broadcasts link state updates, ClockTick, and events at ~1Hz to all connected clients
5. On epoch change (seek), server pushes new `SessionEphemeris`

**Message format:** JSON-encoded `StateSnapshot` (see schema below).

## REST Endpoints

### `GET /api/v1/state`

Returns the current StateSnapshot as JSON.

**Response:** Same schema as the WebSocket snapshot.

### `GET /api/v1/state/{sim_time}`

Returns the nearest stored snapshot to the given simulation time.

**Parameters:**
- `sim_time` (path): ISO 8601 timestamp

**Response:** StateSnapshot JSON, or `404` if no snapshots are stored.

**Note:** Snapshots are stored to SQLite every ~10 seconds. The returned snapshot is the closest match, not an exact time match.

### `GET /api/v1/links`

Query link events from the session database.

**Query parameters:**
- `start` (optional): Start time filter (ISO 8601)
- `end` (optional): End time filter (ISO 8601)

**Response:** Array of link event objects. Returns an empty array if no events match the time range.

### `GET /api/v1/metrics/convergence`

Query convergence events from the session database.

**Query parameters:**
- `start` (optional): Start time filter
- `end` (optional): End time filter

**Response:** Array of convergence event objects.

### `GET /api/v1/metrics/flows/{flow_id}`

Query probe results for a specific traffic flow.

**Parameters:**
- `flow_id` (path): Flow identifier

**Query parameters:**
- `start` (optional): Start time filter
- `end` (optional): End time filter

**Response:** Array of probe result objects.

### `POST /api/v1/trace`

Request a forwarding path trace between two nodes.

**Request body:**

```json
{
  "src_node": "gs-hawthorne",
  "dst_node": "gs-frankfurt"
}
```

**Successful response:**

```json
{
  "hops": ["gs-hawthorne", "sat-P02S03", "sat-P02S04", "sat-P03S04", "gs-frankfurt"],
  "success": true,
  "total_latency_ms": 42.3
}
```

**Error response:**

```json
{
  "hops": [],
  "error": "Trace unavailable"
}
```

## StateSnapshot Schema

The snapshot is the complete payload sent over WebSocket and returned by `GET /api/v1/state`. All data below is from a real running 176-satellite constellation.

### Top-Level Structure

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

### Satellite Node

Each satellite has its current orbital position, velocity, and link counts:

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

### Ground Station Node

Ground stations are fixed locations with tracking antenna parameters:

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

### Node Field Reference

| Field | Type | Description |
|-------|------|-------------|
| `node_id` | string | Stable identifier. `sat-P{plane}S{slot}` for satellites, `gs-{name}` for ground stations |
| `node_type` | string | `"satellite"` or `"ground_station"` |
| `lat_deg`, `lon_deg` | float | WGS84 position in degrees |
| `alt_km` | float | Altitude above sea level in km |
| `vel_x_km_s`, `vel_y_km_s`, `vel_z_km_s` | float | ECEF velocity in km/s (0 for ground stations) |
| `plane`, `slot` | int or null | Orbital plane and slot index (null for ground stations) |
| `isl_count` | int | Number of active ISL links |
| `gnd_count` | int | Number of active ground links |
| `neighbor_count` | int | Total routing neighbor count |
| `min_elevation_deg` | float or null | Minimum satellite elevation angle (ground stations only) |
| `beam_falloff_exponent` | float or null | Signal degradation model parameter |

### ISL Link (Satellite to Satellite)

Inter-satellite links connect two satellites within the same orbital plane (`intra_plane_isl`) or between adjacent planes (`cross_plane_isl`):

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

### Ground Link (Satellite to Ground Station)

Ground links appear and disappear as satellites pass over ground station coverage areas:

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

### Link Field Reference

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

### Recent Events

The last 50 link state changes, handoffs, and convergence events:

```json
{
  "sim_time": "2026-04-03T19:34:58.567875Z",
  "node_id": "gs-ashburn",
  "event_type": "link_down",
  "summary": "vis_lost"
}
```

Event types: `link_up`, `link_down`, `latency_update`. The `summary` field gives a short reason (e.g., `vis_lost` when a satellite moves out of range).

### Network Health

Overall convergence status:

```json
{
  "status": "converged",
  "converging_since_ms": null,
  "unreachable_flows": 0,
  "last_convergence_ms": null
}
```

| Field | Type | Description |
|-------|------|-------------|
| `status` | string | `"converged"`, `"converging"`, or `"degraded"` |
| `converging_since_ms` | int or null | How long the network has been converging |
| `unreachable_flows` | int | Number of broken traffic flows |
| `last_convergence_ms` | float or null | Duration of the most recent convergence event |

## Building Alternative Frontends

See [Building Visualization Clients](building-visualization-clients.md) for a complete guide with connection examples in Python and JavaScript, design rules for rendering, and the full snapshot schema.
