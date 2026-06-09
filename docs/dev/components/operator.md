# Operator - Session Lifecycle Manager

**Location:** `services/nodalarc_operator/`
**Deployment:** Kubernetes Deployment (1 replica)
**Entry point:** `services/nodalarc_operator/main.py`
**Framework:** kopf (Kubernetes Operator Pythonic Framework)

## Responsibility

The Operator watches for `ConstellationSpec` custom resources and manages the full lifecycle of session pods: creation, configuration delivery, placement, and teardown via garbage collection.

## ConstellationSpec CRD

```yaml
apiVersion: nodalarc.io/v1
kind: ConstellationSpec
metadata:
  name: current-session
  namespace: nodalarc
spec:
  sessionYaml: |
    session:
      name: earth-leo-simple
    segments:
      - id: leo
        source: nodalarc:constellations/earth/leo/earth-leo-ring-36.yaml
      - id: ground
        placement:
          from_site_set: nodalarc:site-sets/earth/leo/earth-leo-starlink-pop-sites.yaml
    link_rules:
      - id: leo_access
        topology: {mode: visible_candidates}
        endpoints:
          - select:   {all: [{segment: ground}, {tag: leo}]}
            terminal: {all: [{role: access}, {medium: rf}]}
            min_elevation_deg: 25
          - select:   {segment: leo}
            terminal: {all: [{role: access}, {medium: rf}]}
    time:
      step_seconds: 10
    ...
status:
  phase: Ready       # Creating | Wiring | Ready | Error
  readyPods: 42
  podCount: 42
  platformHash: abc123
```

## Session Creation Sequence

When a ConstellationSpec CR is created:

1. **Resolve session config** from `spec.sessionYaml` through the shared resolver
2. **Validate runtime support** - reject unsupported future grammar before pods are valid
3. **Compute pod placement** - assign resolved nodes to Kubernetes nodes
4. **Render FRR configs** - Jinja2 templates receive resolved node, terminal, routing, SID, and prefix facts
5. **Create ConfigMaps** - one per node with rendered FRR config
6. **Create session pods** - with ownerReference to CR (enables GC cascade)
7. **Wait for pods Running** - poll until all pods reach Running state
8. **Deliver FRR config** - exec into each pod, copy configs, touch startup sentinel
9. **Write wiring manifest** - `nodalarc-topology-wiring` ConfigMap
10. **Wait for wiring complete** - Node Agent signals via `nodalarc-wiring-status`
11. **Advance phase to Ready**

## Pod Placement

Pod placement assigns each resolved session node to a Kubernetes node:

- **allOnOne** - all pods on the first available node
- **planePerNode** - round-robin orbital planes across nodes
- **planeGroupPerNode** - groups of adjacent planes per node

Ground nodes and explicit relay nodes are distributed across nodes regardless of
orbital-plane policy.

## FRR Config Delivery

FRR's stock entrypoint (`docker-start`) waits for a sentinel file before starting daemons. The Operator:
1. Creates a ConfigMap with the rendered frr.conf and daemons file
2. Mounts it at `/etc/frr-config/` in the pod
3. After pod reaches Running, execs into the container to copy files and touch the sentinel:
   ```
   cp /etc/frr-config/frr.conf /etc/frr/frr.conf
   cp /etc/frr-config/daemons /etc/frr/daemons
   touch /etc/frr/.setup_complete
   ```

## Platform Hash

`compute_platform_hash()` resolves `spec.sessionYaml` through the shared session resolver and hashes the resolved runtime model plus referenced catalog assets that affect platform services. If the hash differs between old and new session, platform services (OME, Scheduler) are restarted to pick up the new configuration.

The hash intentionally excludes only operator-owned runtime lineage such as `session.run_id`; changes to constellation, ground-site, routing, scheduling, simulation, addressing, placement, or referenced asset contents trigger a platform restart.

## Error Propagation

`compute_expected_pod_count()` raises on validation errors (invalid segment,
missing catalog asset, unsupported runtime feature). The handler catches the
exception and sets CR `status.phase = "Error"` with the error message. This
surfaces bad configs immediately instead of silently deploying zero pods.

## Session Teardown

Deleting the ConstellationSpec CR triggers Kubernetes garbage collection. All pods and ConfigMaps with ownerReference to the CR are deleted automatically.

The kopf handler on `@kopf.on.delete` performs cleanup that GC doesn't handle (like the wiring status ConfigMap).

## Key Files

| File | Content |
|------|---------|
| `main.py` | kopf handlers (create, delete, resume) |
| `handlers.py` | Reconciliation logic, error handling |
| `session_deployer.py` | Pod creation, placement, config delivery, wiring |
| `frr_renderer.py` | Jinja2 template rendering for FRR configs |
| `platform_hash.py` | Platform hash computation and restart logic |
