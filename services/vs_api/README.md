# VS-API — Visualization State API

FastAPI server that subscribes to NATS JetStream (NODALARC_OME, NODALARC_LINKS,
NODALARC_SESSION) and maintains an in-memory state snapshot of the constellation.
Caches SessionEphemeris and sends it to WebSocket clients on connect so the browser
can run local Keplerian propagation.

## Endpoints

- `ws://host:8080/ws/v1/state` — constellation state at ~1Hz + SessionEphemeris on connect (WebSocket)
- `GET /api/v1/state` — current snapshot (REST)
- `POST /api/v1/sessions` — create session via ConstellationSpec CRD
- `GET /api/v1/sessions` — list available sessions
- `POST /api/v1/trace` — request forwarding path trace
- `GET /api/v1/auth/token` — authentication token

## Port: 8080
