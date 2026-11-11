# NodalArc Operations Guide

This guide is for infrastructure engineers who deploy and maintain NodalArc on Kubernetes clusters. You need to know Kubernetes basics (pods, namespaces, Helm charts, kubectl). You don't need to know orbital mechanics or routing protocol internals.

## Contents

1. [Getting Started](getting-started.md) - Prerequisites, installation, and first deployment
2. [Configuration Guide](configuration.md) - The catalog model, sessions, link rules, sites, nodes, and terminals
3. [Configuration Grammar](configuration-grammar.md) - Formal grammar for catalog primitives and sessions
4. [Multi-Node Deployment](multi-node.md) - Registry setup, pod placement, VXLAN tunnels
5. [Scaling](scaling.md) - Resource requirements, capacity planning, performance characteristics
6. [Operations](operations.md) - Teardown, session switching, upgrades, health monitoring
7. [Security](security.md) - Pod hardening, SSH key lifecycle, network isolation
8. [Troubleshooting](troubleshooting.md) - Diagnosing and fixing common deployment issues

## Architecture at a Glance

NodalArc deploys as a Helm chart on Kubernetes. The platform consists of:

- **6 backend services** - OME (orbital mechanics), Scheduler (topology dispatch), Node Agent (kernel ops), VS-API (API server), Operator (session lifecycle), NATS (messaging)
- **1 frontend** - VF (visualization), served by nginx
- **N session pods** - one per satellite, relay, ground node, or carried host, each running its profile's workload

A session with 176 satellites and 7 ground nodes creates approximately 192 pods
total (183 session pods + 9 platform pods). Multi-segment sessions add one pod
per resolved satellite, relay, or ground node. The platform services are always
running; session pods are created and destroyed as sessions are deployed or
torn down.

Runtime facts and commands use NATS and JetStream. Kubernetes lifecycle,
mounted configuration, and readiness proof use the Kubernetes API and health
probes; they do not redefine the runtime messaging contracts.

## Quick Reference

```bash
RELEASE=0.8.1   # the newest on https://github.com/dotchance/nodalarc/releases

# Install a release
helm install nodalarc oci://ghcr.io/dotchance/nodalarc --version $RELEASE \
  --namespace nodalarc --create-namespace --wait

# Check the platform
helm test nodalarc -n nodalarc --logs

# The address of the browser interface: port 3000 of this server
kubectl -n nodalarc get pod -l app=nodalarc-vf -o jsonpath='{.items[0].status.hostIP}'

# Remove the session, the platform and NodalArc's network devices on every server
helm uninstall nodalarc -n nodalarc
# then the saved session data and the ConstellationSpec definition
kubectl delete namespace nodalarc
kubectl delete crd constellationspecs.nodalarc.io
```

Sessions are deployed and switched from the Sessions window of the browser interface. To move
to another release, remove this one and install the other.
Developers who build NodalArc from source use `make`; see the
[Developer Guide](../dev/getting-started.md).
