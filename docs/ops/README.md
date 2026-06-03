# NodalArc Operations Guide

This guide is for infrastructure engineers who deploy and maintain NodalArc on Kubernetes clusters. You need to know Kubernetes basics (pods, namespaces, Helm charts, kubectl). You don't need to know orbital mechanics or routing protocol internals.

## Contents

1. [Getting Started](getting-started.md) - Prerequisites, installation, and first deployment
2. [Configuration](configuration.md) - Session YAML, constellations, ground stations, satellite types
3. [Multi-Node Deployment](multi-node.md) - Registry setup, pod placement, VXLAN tunnels
4. [Scaling](scaling.md) - Resource requirements, capacity planning, performance characteristics
5. [Operations](operations.md) - Teardown, session switching, upgrades, health monitoring
6. [Security](security.md) - Pod hardening, SSH key lifecycle, network isolation
7. [Troubleshooting](troubleshooting.md) - Diagnosing and fixing common deployment issues

## Architecture at a Glance

NodalArc deploys as a Helm chart on Kubernetes. The platform consists of:

- **6 backend services** - OME (orbital mechanics), Scheduler (topology dispatch), Node Agent (kernel ops), VS-API (API server), Operator (session lifecycle), NATS (messaging)
- **1 frontend** - VF (visualization), served by nginx
- **N session pods** - one per satellite and ground station, each running FRR

A session with 176 satellites and 7 ground stations creates approximately 192 pods total (183 session pods + 9 platform pods). The platform services are always running; session pods are created/destroyed as sessions are deployed/torn down.

All inter-service communication uses NATS JetStream. There is no direct HTTP between backend services.

## Quick Reference

```bash
# Deploy from a clean NodalArc state
make all

# Prove square-one recovery; K3s remains installed
make nuke && make all

# Start a specific session
make session DEFAULT_SESSION=configs/sessions/earth-leo-walker.yaml

# Update an existing platform without teardown
make build && make load && make upgrade

# Destructive platform refresh through the official teardown path
make build && make load && make reinstall && make session

# Check status
make status

# Teardown
make teardown

# Square-one reset only
make nuke
```

`make install` refuses existing platform state. Use `make upgrade` for in-place updates, `make reinstall` for a destructive platform refresh, and `make nuke && make all` for full from-scratch validation.
