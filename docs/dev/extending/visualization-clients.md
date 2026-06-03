# Building Visualization Clients

How to build alternative frontends, dashboards, or automation tools that consume NodalArc constellation data.

## Architecture

All visualization data flows through the VS-API. Your client connects to VS-API over HTTP/WebSocket. It never talks directly to the OME, Scheduler, or any other backend component.

```
OME ──NATS──┐
Scheduler ──NATS──┤──→ VS-API ──HTTP/WS──→ Your Client
Node Agent ─────┘
```

## Authentication

All endpoints except `/api/v1/health` and `/api/v1/auth/token` require a Bearer token.

```bash
# Get token
TOKEN=$(curl -s http://localhost:8080/api/v1/auth/token | python3 -c "import json,sys; print(json.load(sys.stdin)['token'])")

# Use in HTTP
curl -H "Authorization: Bearer $TOKEN" http://localhost:8080/api/v1/state

# Use in WebSocket URL
ws://host:8080/ws/v1/state?token=$TOKEN
```

## WebSocket: Real-Time State

**URL:** `ws://<host>:8080/ws/v1/state?token=<token>`

### Connection sequence

1. Connect → server sends `SessionEphemeris` (node ephemeris plus body/frame facts for local propagation)
2. Server sends current `StateSnapshot`
3. Server broadcasts updated snapshots at ~1 Hz

### Message types

| `msg_type` | Content | When |
|------------|---------|------|
| `session_ephemeris` | Ephemeris inputs for all nodes, fixed ground positions, and body/frame facts | On connect, on epoch change |
| `state_snapshot` | Full state (nodes, links, events, health) | ~1 Hz continuous |
| `session_transitioning` | Progress updates during session switch | During switch only |
| `playback_state` | Playing/paused/speed | On change |

### Python client example

```python
import asyncio
import json
import urllib.request
import websockets

def get_token():
    return json.loads(
        urllib.request.urlopen("http://localhost:8080/api/v1/auth/token").read()
    )["token"]

async def stream_state():
    token = get_token()
    url = f"ws://localhost:8080/ws/v1/state?token={token}"

    async with websockets.connect(url) as ws:
        async for raw in ws:
            msg = json.loads(raw)
            msg_type = msg.get("msg_type", "state_snapshot")

            if msg_type == "session_ephemeris":
                print(f"Received ephemeris for {len(msg.get('nodes', {}))} nodes")
            elif msg_type == "state_snapshot":
                print(f"[{msg['sim_time'][:19]}] {len(msg['links'])} links")

asyncio.run(stream_state())
```

### JavaScript client example

```javascript
const tokenResp = await fetch("http://localhost:8080/api/v1/auth/token");
const { token } = await tokenResp.json();

const ws = new WebSocket(`ws://localhost:8080/ws/v1/state?token=${token}`);

ws.onmessage = (event) => {
  const msg = JSON.parse(event.data);
  if (msg.msg_type === "session_ephemeris") {
    // Store orbital elements for local propagation
    initPropagation(msg.satellites);
  } else {
    // State snapshot
    updateDashboard(msg);
  }
};
```

## REST: On-Demand Queries

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/v1/state` | Current full snapshot |
| GET | `/api/v1/state/{iso_time}` | Historical snapshot nearest to time |
| POST | `/api/v1/trace` | Path trace: `{"src_node": "...", "dst_node": "..."}` |
| GET | `/api/v1/links?start=...&end=...` | Link events in time range |

## StateSnapshot Schema

See [VS-API component docs](../components/vs-api.md) for the full schema. Key fields:

```json
{
  "sim_time": "2026-04-03T19:48:03Z",
  "nodes": [
    {"node_id": "space-sat-p00s00", "node_type": "satellite", "segment_id": "space", "reference_body": "earth", ...}
  ],
  "links": [
    {"node_a": "space-sat-p00s00", "node_b": "space-sat-p00s01", "link_type": "intra_plane_isl", "latency_ms": 13.0, ...}
  ],
  "recent_events": [
    {"sim_time": "...", "node_id": "ground-gs-hawthorne", "event_type": "link_up", "summary": "..."}
  ],
  "network_health": {"status": "converged", ...}
}
```

## Design Considerations

### Position computation

The VS-API does NOT push per-frame positions. It pushes `SessionEphemeris` once
per epoch, and your client computes positions locally.

If you're building a real-time visualization:
- Read each node's published ephemeris type and frame metadata
- Compute node positions at your frame rate from the cached ephemeris
- Apply body/frame transforms for multi-body sessions

If you're building a dashboard or non-real-time tool:
- Use `lat_deg`, `lon_deg`, `alt_km` from the state snapshot (updated at ~1 Hz)
- Sufficient for most non-visualization use cases

### Reconnection

Handle WebSocket disconnections gracefully. On reconnect, the server resends `SessionEphemeris` and the current snapshot. You don't need to maintain state across reconnections.

### Rate limiting

The WebSocket pushes at ~1 Hz. You cannot request a faster rate. If you need higher-frequency position data, implement local propagation from the ephemeris.
