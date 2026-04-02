# VS-API — Visualization State API

FastAPI server that subscribes to all NATS JetStream subjects and maintains
an in-memory state snapshot of the constellation.

## Endpoints

- `ws://host:8080/ws/v1/state` — full StateSnapshot at ~1Hz (WebSocket)
- `GET /api/v1/state` — current snapshot (REST)
- `POST /api/v1/sessions` — create session via ConstellationSpec CRD
- `GET /api/v1/sessions` — list available sessions
- `POST /api/v1/trace` — request forwarding path trace
- `GET /api/v1/auth/token` — authentication token

## Port: 8080
