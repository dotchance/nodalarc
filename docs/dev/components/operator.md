# Operator - Session Lifecycle Manager

**Location:** `services/nodalarc_operator/`
**Deployment:** Kubernetes Deployment (1 replica)
**Entry point:** `services/nodalarc_operator/main.py`
**Framework:** kopf (Kubernetes Operator Pythonic Framework)

## Responsibility

The Operator watches for `ConstellationSpec` custom resources and manages the full lifecycle of session pods: creation, configuration delivery, placement, and teardown via garbage collection.

## ConstellationSpec CRD

```yaml
apiVersion: nodalarc.io/v1alpha1
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
      start_time: '2026-06-08T00:00:00Z'
      step_seconds: 10
      compression: 1
    ...
  catalogUpload:
    upload_id: session-7f3a2c
    closure_digest: sha256:0000000000000000000000000000000000000000000000000000000000000000
    file_count: 17
status:
  phase: Ready
  sessionName: earth-leo-simple
  sessionRunId: run-0123456789abcdef0123
  readyPods: 41
  wiredPods: 41
  podCount: 41
  platformHash: abc123
```

VS-API writes this CR only after it uploads every catalog YAML file reachable
from the root session. `sessionYaml` contains that exact root document, while
`catalogUpload` selects the namespaced ConfigMaps containing the referenced
`nodalarc:` and `user:` YAML files. The upload ID, closure digest, and file count
are generated deployment data rather than session-grammar fields.

## Session Creation Sequence

When a ConstellationSpec CR is created:

1. **Load runtime inputs** - fetch the catalog YAML files selected by
   `spec.catalogUpload`, verify their identities and closure digest, and pair
   them with the exact `spec.sessionYaml` root
2. **Resolve and validate** - resolve the verified files through the shared
   resolver and reject invalid grammar or unsupported runtime features before
   pods are valid
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

`compute_platform_hash()` hashes the verified resolved runtime model together
with proof of the exact `sessionYaml` and uploaded catalog closure. If the hash
differs between the old and new session, platform services (OME, Scheduler) are
restarted to pick up the new configuration.

The hash intentionally excludes only operator-owned runtime lineage such as `session.run_id`; changes to constellation, ground-site, routing, scheduling, simulation, addressing, placement, or referenced asset contents trigger a platform restart.

## Error Propagation

Runtime loading and `compute_expected_pod_count()` raise on validation errors
(missing or altered upload files, invalid segment, missing catalog asset,
unsupported runtime feature). The handler catches the
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
