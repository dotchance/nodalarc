# VS-API - Visualization State API

**Location:** `services/vs_api/`
**Deployment:** Kubernetes Deployment (1 replica)
**Entry point:** `services/vs_api/main.py`
**Port:** 8080

## Responsibility

The VS-API aggregates session state from all backend components and serves it to
the VF and external clients via REST and WebSocket. It also manages session
lifecycle: create, switch, upload, and teardown.

## Architecture

```
NATS Streams ──subscribe──→ VS-API ──WebSocket/REST──→ Clients
                                │
                                ├── Session management (create/switch CR)
                                ├── Terminal proxy (SSH → WebSocket)
                                └── SQLite snapshots (historical)
```

## What It Subscribes To

| Stream/Subject | Purpose |
|---------------|---------|
| NODALARC_OME | ClockTick for timing, VisibilityEvents for event log |
| NODALARC_LINKS | LinkUp/Down for event log, LinkStateSnapshot for current link state |
| NODALARC_OPS | Operational event log, Scheduler actuation health, and OME MBB terminal lifecycle events persisted to SQLite |
| NODALARC_SESSION | SessionEphemeris (cached, forwarded to clients on connect), PlaybackState |

## REST Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/v1/state` | Current full state snapshot |
| GET | `/api/v1/state/{sim_time}` | Historical snapshot (nearest stored) |
| POST | `/api/v1/trace` | Forwarding path trace between two nodes |
| GET | `/api/v1/links` | Link events with time range filter |
| GET | `/api/v1/health` | Health check (no auth) |
| GET | `/api/v1/auth/token` | Get auth token (no auth) |
| GET | `/api/v1/metrics/convergence` | Convergence events |
| GET | `/api/v1/metrics/flows/{flow_id}` | Probe results for a flow |
| POST | `/api/v1/sessions/deploy` | Deploy a new session (wizard backend) |
| GET | `/api/v1/sessions/coverage-preview` | Coverage preview for wizard |

## WebSocket

**URL:** `ws://host:8080/ws/v1/state?token=<token>`

Connection flow:
1. Client connects
2. Server sends cached `SessionEphemeris` (`msg_type: "session_ephemeris"`)
3. Server sends current state snapshot
4. Server broadcasts state updates at ~1 Hz (link state, events, clock)
5. On epoch change: server pushes new `SessionEphemeris`

Also broadcasts:
- `session_transitioning` messages during session switch (with progress detail)
- `playback_state` changes (pause/resume/speed)

## Session Management

### Deploy (from wizard)

1. Resolve segment session YAML through the shared resolver
2. Create ConstellationSpec CR
3. Return immediately - Operator handles pod creation async
4. WebSocket broadcasts progress as session deploys

The wizard generator emits the same segment grammar accepted by YAML upload.
There is no separate wizard-only session format.

### Switch

1. Broadcast `session_transitioning` to all WebSocket clients
2. Execute `_run_switch`:
   - Delete old ConstellationSpec CR
   - Wait for old pods to terminate
   - Create new ConstellationSpec CR
   - Wait for new session Ready
3. Broadcast new SessionEphemeris to all clients

### Important: VS-API must NOT be restarted during session switch

The Operator's `restart_platform_pods` excludes VS-API. It orchestrates the switch and holds WebSocket connections open throughout.

## Terminal Proxy

Browser terminal access: xterm.js → WebSocket → VS-API → SSH → pod vtysh

VS-API reads the SSH private key from `nodalarc-terminal-keys` Secret on first terminal connection (in-memory only). Proxies the SSH session bidirectionally over WebSocket.

## Authentication

Auto-generated token on startup. All endpoints except `/health` and `/auth/token` require `Authorization: Bearer <token>`.

Fixed token: set `NODAL_API_KEY` environment variable.

## Bootstrap

On startup, VS-API:
1. Polls for a ConstellationSpec CR every 5 seconds until one appears
2. Reads and resolves session config from the CR's `sessionYaml` field
3. Subscribes to NATS streams
4. Begins serving clients

## Key Files

| File | Content |
|------|---------|
| `main.py` | FastAPI app, routes, WebSocket handler, bootstrap |
| `session_context.py` | NATS subscriptions, state aggregation |
| `session_manager.py` | Session switch orchestration |
| `state_snapshot.py` | StateSnapshot model for clients |
| `terminal.py` | SSH proxy for browser terminal |
