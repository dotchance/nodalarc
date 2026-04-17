# Measurement Infrastructure (MI)

Collects routing protocol events and probe measurements. Persists to SQLite.

## Components

- **FRR Adapters** (`adapters/frr_isis_adapter.py`, `adapters/frr_ospf_adapter.py`) — poll vtysh over `kubectl exec` and tail FRR logs for adjacency / SPF / LSP events; normalise to `AdapterEvent`.
- **Convergence Detector** — flow-aware convergence detection; drives probe bursts via the probe daemon REST API and evaluates results against a stability window.
- **Convergence Gate** — NATS request/reply on `SUBJECT_MI_CONVERGENCE_GATE`; used by the Scenario runner's `wait_converge` action.
- **Probe Daemon** — FastAPI sidecar (port 9100) deployed in ground-station pods. Originates and terminates UDP probe traffic.
- **Probe Client** — dispatches probe start/stop commands to sidecars over HTTP.

## Transport and persistence

- All messaging: NATS JetStream. Publishes `AdapterEvent`, `ProbeResult`, `ConvergenceResult`; subscribes to convergence-gate and trace requests.
- SQLite per-session database — append-only, WAL-enabled for concurrent reads from VS-API.

## Deployment status

- **Containerised.** Image defined in `services/measurement/Dockerfile`; built via `make build-measurement`.
- **Opt-in per session.** MI is not deployed by default. Set `mi.enabled: true` in the session YAML to activate; the Operator deploys the MI workload and probe sidecars as part of that session.
- **Not a Helm default workload.** There is no always-on MI Deployment in `deploy/helm/templates/`.

## Development status — DEFERRED

Active MI development is deferred until NodalArc (the emulation substrate) and
NodalPath (the PCE) are solid end-to-end. The code here runs, but no new
features, integrations, or refactors will land until the base platform is
stable.

## Scope notes (current code)

- MI does not subscribe to `SUBJECT_LINK_UP` / `SUBJECT_LINK_DOWN`. Link-state history lives in the `NODALARC_LINKS` JetStream stream itself; extending MI to mirror those into SQLite is tracked as a follow-up, not a current responsibility.
