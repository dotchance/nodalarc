# API for Power Users

NodalArc exposes a REST and WebSocket API that gives you programmatic access to all constellation state. You can use it to automate experiments, build custom dashboards, or integrate NodalArc with other tools.

## Getting a Token

All API requests require an authentication token. Get one:

```bash
curl -s http://localhost:8080/api/v1/auth/token
# Returns: {"token": "..."}
```

Save it for subsequent requests:

```bash
TOKEN=$(curl -s http://localhost:8080/api/v1/auth/token | python3 -c "import json,sys; print(json.load(sys.stdin)['token'])")
```

## Quick Examples

### Get the full constellation state

```bash
curl -s -H "Authorization: Bearer $TOKEN" http://localhost:8080/api/v1/state | python3 -m json.tool
```

Returns a JSON snapshot with all nodes (positions, link counts), all links (latency, bandwidth, type), recent events, and network health status.

### Count satellites, ground stations, and links

```bash
curl -s -H "Authorization: Bearer $TOKEN" http://localhost:8080/api/v1/state | python3 -c "
import json, sys
s = json.load(sys.stdin)
sats = sum(1 for n in s['nodes'] if n['node_type'] == 'satellite')
gs = sum(1 for n in s['nodes'] if n['node_type'] == 'ground_station')
print(f'{sats} satellites, {gs} ground stations, {len(s[\"links\"])} active links')
"
```

### Find active ground connections with latency

```bash
curl -s -H "Authorization: Bearer $TOKEN" http://localhost:8080/api/v1/state | python3 -c "
import json, sys
s = json.load(sys.stdin)
for link in s['links']:
    if link.get('link_type') == 'ground':
        print(f\"{link['node_a']} <-> {link['node_b']}  {link['latency_ms']:.1f}ms\")
"
```

### Trace the forwarding path between two nodes

```bash
curl -s -X POST -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  http://localhost:8080/api/v1/trace \
  -d '{"src_node": "gs-hawthorne", "dst_node": "gs-frankfurt"}'
```

Returns the hop-by-hop path and total latency:

```json
{
  "hops": ["gs-hawthorne", "sat-P02S03", "sat-P02S04", "sat-P03S04", "gs-frankfurt"],
  "success": true,
  "total_latency_ms": 42.3
}
```

### Stream live state over WebSocket

```python
import asyncio, json, websockets, urllib.request

token = json.loads(
    urllib.request.urlopen("http://localhost:8080/api/v1/auth/token").read()
)["token"]

async def main():
    async with websockets.connect(f"ws://localhost:8080/ws/v1/state?token={token}") as ws:
        async for msg in ws:
            s = json.loads(msg)
            print(f"[{s['sim_time'][:19]}] {len(s['links'])} links active")

asyncio.run(main())
```

The WebSocket pushes a full state snapshot at ~1 Hz. On connect, you first receive the orbital ephemeris (for local position computation), then continuous state updates.

## Available Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/v1/state` | Current full state snapshot |
| GET | `/api/v1/state/{sim_time}` | Historical snapshot nearest to given time |
| POST | `/api/v1/trace` | Forwarding path trace between two nodes |
| GET | `/api/v1/links` | Link events with optional time range filter |
| GET | `/api/v1/health` | Health check (no auth required) |
| GET | `/api/v1/auth/token` | Get auth token (no auth required) |
| WS | `/ws/v1/state` | Real-time state stream (~1 Hz) |

## State Snapshot Schema

The state snapshot contains:

- **nodes** — array of all satellites and ground stations with position, link counts, and metadata
- **links** — array of all active links with latency, bandwidth, and type (intra_plane_isl, cross_plane_isl, ground)
- **recent_events** — last 50 link state changes and handoffs
- **network_health** — convergence status
- **sim_time** / **wall_time** — current simulation and wall-clock time
- **playback_paused** / **playback_speed** — time control state

For the full schema with field descriptions, see the [VS-API Reference](../dev/components/vs-api.md) in the developer documentation.

## Use Cases

- **Automated convergence testing** — deploy a session, wait for convergence, inject a link failure, measure time to reconverge via the API
- **Custom dashboards** — stream state over WebSocket and render your own visualization
- **CI/CD validation** — script path traces and verify reachability as part of a test pipeline
- **Data collection** — record state snapshots over time for offline analysis of routing behavior
- **Integration** — feed constellation state into external tools (Grafana, custom analysis scripts, etc.)
