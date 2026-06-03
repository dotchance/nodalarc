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
    identity:
      mode: segment_namespaced
    segments:
      - id: space
        kind: constellation
        source: configs/constellations/demo-36.yaml
        namespace: space
        central_body: earth
      - id: ground
        kind: ground_set
        source: configs/ground-stations/sets/demo.yaml
        namespace: ground
        reference_body: earth
    link_rules:
      - id: ground-access
        kind: access
        endpoints:
          - selector: {segment: ground}
            terminal_role: ground
          - selector: {segment: space}
            terminal_role: ground
        topology: {mode: visible_candidates}
    ...
status:
  phase: Ready       # Creating | Wiring | Ready | Error
  readyPods: 42
  podCount: 42
  platformHash: abc123
```

## Session Creation Sequence

When a ConstellationSpec CR is created:

1. **Parse session config** from `spec.sessionYaml`
2. **Expand constellation** - resolve satellite type, compute orbital elements
3. **Load ground stations** - resolve station set, compute terrestrial prefixes
4. **Compute pod placement** - assign pods to nodes using the configured policy
5. **Render FRR configs** - Jinja2 templates → per-node frr.conf + daemons file
6. **Create ConfigMaps** - one per node with rendered FRR config
7. **Create session pods** - with ownerReference to CR (enables GC cascade)
8. **Wait for pods Running** - poll until all pods reach Running state
9. **Deliver FRR config** - exec into each pod, copy configs, touch startup sentinel
10. **Write wiring manifest** - `nodalarc-topology-wiring` ConfigMap
11. **Wait for wiring complete** - Node Agent signals via `nodalarc-wiring-status`
12. **Advance phase to Ready**

## Pod Placement

`compute_pod_placement(constellation, ground_stations, policy, nodes)` assigns each pod to a K8s node:

- **allOnOne** - all pods on the first available node
- **planePerNode** - round-robin orbital planes across nodes
- **planeGroupPerNode** - groups of adjacent planes per node

Ground stations are distributed across nodes regardless of policy.

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

The hash intentionally excludes only operator-owned runtime lineage such as `session.run_id`; changes to constellation, ground station, routing, scheduling, simulation, addressing, placement, or referenced asset contents trigger a platform restart.

## Error Propagation

`compute_expected_pod_count()` raises on validation errors (invalid constellation, missing ground stations). The handler catches the exception and sets CR `status.phase = "Error"` with the error message. This surfaces bad configs immediately instead of silently deploying zero pods.

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
