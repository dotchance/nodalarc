# Building Visualization Clients

How to build alternative frontends, dashboards, or automation tools that consume NodalArc constellation data.

## Architecture

All visualization data flows through the VS-API. Your client connects to VS-API over HTTP/WebSocket. It never talks directly to the OME, Scheduler, or any other backend component.

```
OME --NATS--+
Scheduler --NATS--+---> VS-API --HTTP/WS---> Your Client
Node Agent -----+
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

1. Connect -> server sends `SessionEphemeris` (node ephemeris plus body/frame facts for local propagation)
2. Server sends current `StateSnapshot`
3. Server broadcasts updated snapshots at ~1 Hz

### Message types

| `msg_type` | Content | When |
|------------|---------|------|
| `session_ephemeris` | Ephemeris inputs for all nodes, fixed ground positions, and body/frame facts | On connect, on epoch change |
| `state_snapshot` | Full state: nodes, links, traced paths, recent events, ops events | ~1 Hz continuous |
| `wiring_progress` | One wiring progress line from the Node Agent | While a session wires |
| `session_transitioning` | Progress detail during a session switch | During a switch |
| `session_ready` | The switched-to session's first state snapshot | When a switch completes |
| `session_failed` | Why the session switch failed | When a switch fails |

Each message is one member of the `StateFeedFrame` union in
`lib/nodalarc/models/vs_api.py`, told apart by `msg_type`.

### Messages the client sends

The state socket accepts the `StateSocketCommand` messages: `debug_stream` and
`debug_stop` with a list of `sources`, and `debug_stop_all`. Any other message
closes the socket with code 4400.

### Close codes

| Code | Socket | Meaning |
|------|--------|---------|
| 1000 | terminal | The node's shell ended |
| 4400 | state, terminal | The client sent a message the socket does not accept |
| 4404 | terminal | The session has no node with that ID |
| 4409 | terminal | The node's workload declares no terminal access |
| 4410 | terminal | The session switched; every open terminal closes |
| 4503 | terminal | VS-API cannot read the terminal key |

A wrong or missing token is refused before VS-API accepts the socket, so the
client sees a failed connection and no close code.

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
            msg_type = msg["msg_type"]

            if msg_type == "session_ephemeris":
                print(f"Received ephemeris for {len(msg['nodes'])} nodes")
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
    initPropagation(msg.nodes);
  } else if (msg.msg_type === "state_snapshot") {
    updateDashboard(msg);
  }
};
```

## REST: On-Demand Queries

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/v1/state` | Current full snapshot |
| GET | `/api/v1/state/{iso_time}` | Historical snapshot nearest to time |
| GET | `/api/v1/links?start=...&end=...&node=...&peer=...&order=...&limit=...&cursor=...` | One page of recorded link events, at most 200; `next_cursor` requests the next page (sessions deployed with history recording) |
| POST | `/api/v1/trace` | Trace the path between two nodes once, in both directions; returns a `TracedPath` |
| POST | `/api/v1/trace/start` | Start the live trace between two nodes; its result rides every state snapshot |
| GET | `/api/v1/trace/status` | The live trace's endpoints and latest `TracedPath` |
| POST | `/api/v1/trace/stop` | Stop the live trace |

## StateSnapshot Schema

The schema is the `StateSnapshot` model in `lib/nodalarc/models/vs_api.py`.
`GET /openapi.json` serves it with every route's request and answer, and the
browser's TypeScript types are generated from the same models into
`frontend/src/generated/vsApi.ts`. Key fields:

```json
{
  "sim_time": "2026-04-03T19:48:03Z",
  "nodes": [
    {"node_id": "space-sat-p00s00", "node_type": "satellite", "segment_id": "space", "reference_body": "earth", ...}
  ],
  "links": [
    {"node_a": "space-sat-p00s00", "node_b": "space-sat-p00s01", "link_type": "isl", "latency_ms": 13.0, ...}
  ],
  "recent_events": [
    {"sim_time": "...", "node_id": "ground-gs-hawthorne", "event_type": "link_up", "summary": "..."}
  ]
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
