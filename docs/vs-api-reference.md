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

All examples below assume `$TOKEN` is set as shown above. Each example is a standalone command you can copy and paste.

```bash
# Get current constellation state
curl -s -H "Authorization: Bearer $TOKEN" http://localhost:8080/api/v1/state | python3 -m json.tool
```

```bash
# Count satellites, ground stations, and active links
curl -s -H "Authorization: Bearer $TOKEN" http://localhost:8080/api/v1/state | python3 -c "
import json, sys
s = json.load(sys.stdin)
sats = sum(1 for n in s['nodes'] if n['node_type'] == 'satellite')
gs = sum(1 for n in s['nodes'] if n['node_type'] == 'ground_station')
print(f'{sats} satellites, {gs} ground stations, {len(s[\"links\"])} links')
"
```

```bash
# Find which ground stations have active satellite connections
curl -s -H "Authorization: Bearer $TOKEN" http://localhost:8080/api/v1/state | python3 -c "
import json, sys
s = json.load(sys.stdin)
for link in s['links']:
    if link['link_type'] and 'ground' in link['link_type']:
        print(f\"{link['node_a']} <-> {link['node_b']}  latency={link['latency_ms']:.1f}ms  range={link['range_km']:.0f}km\")
"
```

```bash
# Get a specific satellite's position and neighbor count
curl -s -H "Authorization: Bearer $TOKEN" http://localhost:8080/api/v1/state | python3 -c "
import json, sys
s = json.load(sys.stdin)
node = next((n for n in s['nodes'] if n['node_id'] == 'sat-P00S00'), None)
if node:
    print(f\"Position: {node['lat_deg']:.2f}N, {node['lon_deg']:.2f}E, {node['alt_km']:.0f}km\")
    print(f\"Neighbors: {node['neighbor_count']} (ISL: {node['isl_count']}, Ground: {node['gnd_count']})\")
"
```

```bash
# Trace the forwarding path between two nodes
curl -s -X POST -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  http://localhost:8080/api/v1/trace \
  -d '{"src_node": "gs-hawthorne", "dst_node": "gs-frankfurt"}'
```

```bash
# List all link events in a time window
curl -s -H "Authorization: Bearer $TOKEN" \
  'http://localhost:8080/api/v1/links?start=2026-01-01T00:00:00Z&end=2026-01-01T00:10:00Z'
```

```bash
# Stream live state updates over WebSocket (Python)
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

Full StateSnapshot pushed at ~1Hz. No delta encoding. Each message is the complete constellation state.

**Connection flow:**
1. Client connects to the WebSocket endpoint
2. Server sends an initial snapshot immediately on connect
3. Server broadcasts updated snapshots at ~1Hz to all connected clients
4. Client should drop intermediate frames if processing is slower than 1Hz

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

**Response:** Array of link event objects:

```json
[
  {
    "id": 1,
    "sim_time": "2026-01-01T00:00:30.000000",
    "wall_time": "2026-03-01T12:00:30.000000",
    "event_type": "LinkUp",
    "node_a": "sat-P00S00",
    "node_b": "sat-P00S01",
    "interface_a": "eth1",
    "interface_b": "eth1",
    "latency_ms": 12.5,
    "bandwidth_mbps": 1000.0,
    "reason": "intra_plane_isl"
  }
]
```

### `GET /api/v1/metrics/convergence`

Query convergence events from the session database.

**Query parameters:**
- `start` (optional): Start time filter
- `end` (optional): End time filter

**Response:** Array of convergence event objects:

```json
[
  {
    "id": 1,
    "event_id": "conv-001",
    "sim_time_start": "2026-01-01T00:01:00.000000",
    "sim_time_end": "2026-01-01T00:01:02.500000",
    "converged": 1,
    "duration_ms": 2500.0,
    "packets_lost": 3,
    "packets_sent": 100,
    "triggering_link_event_id": 5
  }
]
```

### `GET /api/v1/metrics/flows/{flow_id}`

Query probe results for a specific traffic flow.

**Parameters:**
- `flow_id` (path): Flow identifier

**Query parameters:**
- `start` (optional): Start time filter
- `end` (optional): End time filter

**Response:** Array of probe result objects:

```json
[
  {
    "id": 1,
    "sim_time": "2026-01-01T00:01:00.000000",
    "flow_id": "gs-handover-flow",
    "src_node": "gs-hawthorne",
    "dst_node": "gs-ashburn",
    "packets_sent": 10,
    "packets_received": 9,
    "latency_min_ms": 25.1,
    "latency_max_ms": 42.3,
    "latency_avg_ms": 31.7,
    "jitter_ms": 5.2
  }
]
```

### `POST /api/v1/trace`

Request a forwarding path trace between two nodes via NATS.

**Request body:**

```json
{
  "src_node": "gs-hawthorne",
  "dst_node": "gs-ashburn"
}
```

**Response:**

```json
{
  "hops": ["gs-hawthorne", "sat-P02S03", "sat-P02S04", "sat-P03S04", "gs-ashburn"],
  "success": true
}
```

On error:

```json
{
  "hops": [],
  "error": "trace request timeout"
}
```

## StateSnapshot Schema

The complete payload sent over WebSocket and returned by `GET /api/v1/state`:

```json
{
  "sim_time": "2026-01-01T00:05:00.000000+00:00",
  "wall_time": "2026-03-01T12:05:00.000000+00:00",
  "schema_version": 1,
  "nodes": [
    {
      "node_id": "sat-P00S00",
      "node_type": "satellite",
      "lat_deg": 33.5,
      "lon_deg": -118.2,
      "alt_km": 550.0,
      "vel_x_km_s": 0.5,
      "vel_y_km_s": 7.2,
      "vel_z_km_s": 0.1,
      "plane": 0,
      "slot": 0,
      "routing_area": "49.0001",
      "neighbor_count": 4,
      "isl_count": 3,
      "gnd_count": 1,
      "prefix": null
    }
  ],
  "links": [
    {
      "node_a": "sat-P00S00",
      "node_b": "sat-P00S01",
      "state": "active",
      "link_type": "intra_plane_isl",
      "link_reason": "intra_plane_isl",
      "latency_ms": 12.5,
      "bandwidth_mbps": 1000.0,
      "range_km": 3582.0,
      "traffic_load_pct": null
    }
  ],
  "traced_paths": [],
  "active_flows": [],
  "recent_events": [
    {
      "sim_time": "2026-01-01T00:04:55.000000+00:00",
      "node_id": "sat-P02S03",
      "event_type": "link_up",
      "summary": "intra_plane_isl"
    }
  ],
  "network_health": {
    "status": "converged",
    "converging_since_ms": null,
    "unreachable_flows": 0,
    "last_convergence_ms": 2500.0
  },
  "routing_stack": "frr-isis-sr",
  "constellation_name": "starlink-early-44"
}
```

### Field Reference

**NodeState:**

| Field | Type | Description |
|-------|------|-------------|
| `node_id` | string | Unique node identifier |
| `node_type` | string | `"satellite"` or `"ground_station"` |
| `lat_deg` | float | WGS84 latitude in degrees |
| `lon_deg` | float | WGS84 longitude in degrees |
| `alt_km` | float | Altitude above sea level in km |
| `vel_x_km_s` | float? | ECEF velocity X (null for GS) |
| `vel_y_km_s` | float? | ECEF velocity Y (null for GS) |
| `vel_z_km_s` | float? | ECEF velocity Z (null for GS) |
| `plane` | int? | Orbital plane index (null for GS) |
| `slot` | int? | Slot within plane (null for GS) |
| `routing_area` | string? | Routing area ID |
| `neighbor_count` | int | Total neighbor count |
| `isl_count` | int | ISL link count |
| `gnd_count` | int | Ground link count |
| `prefix` | string? | Advertised prefix (GS only) |

**LinkState:**

| Field | Type | Description |
|-------|------|-------------|
| `node_a` | string | First endpoint node ID |
| `node_b` | string | Second endpoint node ID |
| `state` | string | `"active"` or `"inactive"` |
| `link_type` | string? | `intra_plane_isl`, `cross_plane_isl`, `ground_uplink`, `ground_downlink` |
| `latency_ms` | float | One-way propagation delay |
| `bandwidth_mbps` | float | Link capacity |
| `range_km` | float | Physical distance |
| `traffic_load_pct` | float? | Traffic load (null = no probe data) |

**NetworkHealth:**

| Field | Type | Description |
|-------|------|-------------|
| `status` | string | `"converged"`, `"converging"`, or `"degraded"` |
| `converging_since_ms` | int? | Duration in converging state |
| `unreachable_flows` | int | Number of broken traffic flows |
| `last_convergence_ms` | float? | Most recent convergence duration |

## Building Alternative Frontends

The VS-API is protocol-agnostic. Any WebSocket client can consume the StateSnapshot stream. To build an alternative frontend:

1. Connect to `ws://host:8080/ws/v1/state`
2. Parse incoming JSON messages as StateSnapshot objects
3. Use REST endpoints for historical queries and path traces
4. The `schema_version` field (currently `1`) will increment if breaking changes are made to the snapshot format
