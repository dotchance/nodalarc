# Services

Backend services that run as containers on K3s. Each directory is a Python
package with its own Dockerfile. All share `lib/nodalarc/` as a dependency.

## Services

| Service | Purpose | Entry Point |
|---------|---------|-------------|
| **ome/** | Orbital Mechanics Engine — computes satellite positions, ISL visibility, ground station access. Publishes events to NATS JetStream. | `python -m ome.main --continuous` |
| **scheduler/** | Topology dispatcher — subscribes to OME events, reconciles link state, dispatches BatchLinkUp/Down to the Node Agent via NATS request/reply. | `python -m scheduler` |
| **node_agent/** | DaemonSet — executes privileged kernel operations (veth pairs, tc shaping, bridge management) on each K3s node. Receives commands from the Scheduler. | `python -m node_agent` |
| **vs_api/** | Visualization State API — FastAPI server aggregating all NATS events into an in-memory state snapshot. Serves REST + WebSocket to the frontend. | `python -m vs_api.main` |
| **nodalarc_operator/** | K8s Operator — watches ConstellationSpec CRDs, manages session lifecycle (create, switch, teardown). | `kopf run -m nodalarc_operator` |
| **measurement/** | Measurement Infrastructure — FRR protocol adapters, convergence detection, probe daemon. Containerised but opt-in per session (`mi.enabled` flag); active development deferred until NodalArc + NodalPath are solid. | `python -m measurement.mi_main` |

## Startup Order

1. **NATS** — JetStream server (deployed by Helm, not a service in this directory)
2. **OME** — must create JetStream streams before other services subscribe (init container)
3. **Node Agent** — must complete wiring before accepting NATS requests (wiring gate)
4. **Scheduler** — must wait for wiring-status ConfigMap before dispatching (wiring gate)
5. **VS-API** — subscribes to all NATS subjects, can start anytime after NATS
6. **Operator** — watches CRDs, can start anytime after Helm install

## Building

```bash
make build          # builds all service images
make build-ome      # build a single service
```

All Dockerfiles use the repo root as build context (`docker build -f services/ome/Dockerfile .`)
and set `PYTHONPATH=/app/lib:/app/services:/app` so `import ome`, `import scheduler`, etc. resolve correctly.
