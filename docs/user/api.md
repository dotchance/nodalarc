# API for Power Users

NodalArc exposes a REST and WebSocket API that gives you programmatic access to
the current session state. You can use it to automate experiments, build custom
dashboards, or integrate NodalArc with other tools.

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

Returns a JSON snapshot with all nodes (positions, link counts), all links (latency, each direction's transmit rate, type), recent events, and network health status.

### Count satellites, ground nodes, and links

```bash
curl -s -H "Authorization: Bearer $TOKEN" http://localhost:8080/api/v1/state | python3 -c "
import json, sys
s = json.load(sys.stdin)
sats = sum(1 for n in s['nodes'] if n['node_type'] == 'satellite')
ground = sum(1 for n in s['nodes'] if n['node_type'] == 'ground_station')
print(f'{sats} satellites, {ground} ground nodes, {len(s[\"links\"])} active links')
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

### Trace the path between two nodes

NodalArc traces a path by running traceroute inside the two nodes' own
containers, one run from each end toward the other. The result is the path
real packets took through the forwarding plane at that moment.

```bash
curl -s -X POST -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  http://localhost:8080/api/v1/trace \
  -d '{"src_node": "earth-it-fucino-gw1", "dst_node": "earth-us-ca-goldstone-gw1"}'
```

The request returns when both traceroutes finish. A destination that does not
answer costs up to 20 hops at 4 seconds each. The response, from an
`earth-geo-tdrs` session:

```json
{
  "flow_id": "__trace__",
  "src_node": "earth-it-fucino-gw1",
  "dst_node": "earth-us-ca-goldstone-gw1",
  "hops": [
    "earth-it-fucino-gw1",
    "geo-tdrs-049w",
    "geo-tdrs-085w",
    "geo-tdrs-111w",
    "geo-tdrs-171w",
    "earth-us-ca-goldstone-gw1"
  ],
  "hop_rtts": [
    null,
    260.158,
    434.503,
    561.195,
    842.272,
    1103.524
  ],
  "state": "reached",
  "rtt_ms": 1103.524,
  "error": null,
  "reverse_hops": [
    "earth-us-ca-goldstone-gw1",
    "geo-tdrs-171w",
    "geo-tdrs-111w",
    "geo-tdrs-085w",
    "geo-tdrs-049w",
    "earth-it-fucino-gw1"
  ],
  "reverse_hop_rtts": [
    null,
    261.621,
    543.337,
    669.568,
    843.685,
    1104.216
  ],
  "reverse_state": "reached",
  "reverse_rtt_ms": 1104.216,
  "reverse_error": null,
  "asymmetry_detected": false,
  "tracing": false,
  "traced_at": "2026-09-23T20:41:57.253131+00:00",
  "sim_time": "2026-06-08T00:00:00+00:00"
}
```

Each direction lists what answered at every hop, starting with the node the
trace runs from:

- A node id means the answering address belongs to that node.
- An address means no node in the session owns it.
- `*` means nothing answered at that hop.

`hop_rtts` gives the round trip to each hop. The source has none.

`state` says how each direction ended:

- `reached`: the destination answered. `rtt_ms` is its round trip.
- `not_reached`: the trace ended without an answer from the destination.
- `failed`: the trace could not run. `error` gives the reason.

`asymmetry_detected` is `null` unless both directions reached their destination
and every hop between the ends answered from a node's address.

### Trace a path continuously

The Trace Path panel in the browser uses the live trace. It repeats the trace
every few seconds, and again whenever a link on the path changes, until you
stop it:

```bash
curl -s -X POST -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  http://localhost:8080/api/v1/trace/start \
  -d '{"src_node": "earth-it-fucino-gw1", "dst_node": "earth-us-ca-goldstone-gw1"}'
curl -s -H "Authorization: Bearer $TOKEN" http://localhost:8080/api/v1/trace/status
curl -s -X POST -H "Authorization: Bearer $TOKEN" http://localhost:8080/api/v1/trace/stop
```

`/api/v1/trace/status` returns the trace's endpoints and its latest result in
the same form as above. The latest result also appears in every state snapshot
under `traced_paths`.

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

The WebSocket pushes a full state snapshot at ~1 Hz. On connect, you first
receive the session ephemeris and body/frame metadata used for local position
computation, then continuous state updates.

## Available Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/v1/state` | Current full state snapshot |
| GET | `/api/v1/state/{sim_time}` | Recorded snapshot nearest to given time (recorded sessions only) |
| POST | `/api/v1/trace` | Trace the path between two nodes once, in both directions |
| POST | `/api/v1/trace/start` | Start the live trace between two nodes |
| GET | `/api/v1/trace/status` | The live trace's endpoints and latest result |
| POST | `/api/v1/trace/stop` | Stop the live trace |
| GET | `/api/v1/links` | Recorded link events, with optional `start`, `end` and `node` filters (recorded sessions only) |
| POST | `/api/v1/playback` | Playback control: pause, resume, set_speed, seek |
| GET | `/api/v1/health` | Health check (no auth required) |
| GET | `/api/v1/auth/token` | Get auth token (no auth required) |
| WS | `/ws/v1/state` | Real-time state stream (~1 Hz) |

## Session History

History recording is chosen each time a session is deployed: the
"Record session history" checkbox in the Sessions launcher or the Session
Builder, or `"record_history": true` in the body of a deploy request
(`POST /api/v1/sessions/switch`, `POST /api/v1/session/deploy-from-yaml`,
`POST /api/v1/builder/session/deploy`). A recorded session run keeps one
history file with its state snapshots (about every ten seconds), link events,
operator interventions and OME lifecycle events. The history endpoints answer
for the active session: `409 history.not_recorded` when it was deployed
without recording, and `503 history.failed` when a write failed and recording
stopped.

## State Snapshot Schema

The state snapshot contains:

- **nodes** - array of all satellites, relay nodes, and ground nodes with position, link counts, segment metadata, and body/frame metadata
- **links** - array of all active links with latency, each direction's transmit rate (`transmit_mbps_a` for node_a to node_b, `transmit_mbps_b` for node_b to node_a), type, and rule-derived relationship where available
- **recent_events** - last 50 link state changes and handoffs
- **network_health** - convergence status
- **sim_time** / **wall_time** - current simulation and wall-clock time
- **playback_paused** / **playback_speed** - time control state

Runtime node IDs are assigned by the shared resolver. Generated space-node IDs
combine the segment ID with the generated local node ID, such as
`leo-sat-p00s00`, `meo-sat-p00s00`, or `geo-sat-p00s00`. Ground-node IDs
combine the catalog site ID with the installed node ID, such as
`earth-us-hawthorne-gw1` or `luna-artemis-base-gw1`; the session placement
segment does not replace the site ID. Query `/api/v1/state` first and use the
node IDs it returns.

For the full schema with field descriptions, see the [VS-API Reference](../dev/components/vs-api.md) in the developer documentation.

## Use Cases

- **Automated convergence testing** - deploy a session, wait for convergence, inject a link failure, measure time to reconverge via the API
- **Custom dashboards** - stream state over WebSocket and render your own visualization
- **CI/CD validation** - script path traces and verify reachability as part of a test pipeline
- **Data collection** - record state snapshots over time for offline analysis of routing behavior
- **Integration** - feed constellation state into external tools (Grafana, custom analysis scripts, etc.)
