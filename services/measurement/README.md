# Measurement Infrastructure (MI)

Collects routing protocol events and probe measurements. Not yet containerized.

## Components

- **FRR Adapters** — poll `vtysh` and parse FRR logs for IS-IS/OSPF events
- **Convergence Detector** — flow-aware convergence detection with probe bursts
- **Convergence Gate** — REQ/REP endpoint for convergence wait/measure
- **Probe Daemon** — sidecar FastAPI service (port 9100) in each pod
- **Probe Client** — sends probe commands to pod sidecars

## Status

Uses NATS JetStream for transport.
