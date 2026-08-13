# Operator - Session Lifecycle Manager

**Location:** `services/nodalarc_operator/`
**Deployment:** Kubernetes Deployment (1 replica)
**Entry point:** `services/nodalarc_operator/__main__.py`
**Framework:** kopf (Kubernetes Operator Pythonic Framework)

## Responsibility

The Operator watches for `ConstellationSpec` custom resources and manages the full lifecycle of session pods: workload composition, creation, configuration delivery, placement, and teardown via garbage collection.

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
4. **Compose workloads** - resolve each node's effective profile, resolve
   environment facts, and compose the primary container and sidecars
5. **Render adapter config** - a profile with an adapter (the frr adapter
   today) gets per-node native config rendered from resolved facts;
   adapter-free profiles skip this step
6. **Create ConfigMaps** - one immutable artifact ConfigMap per node,
   mounted read-only at the profile's `config_mount`
7. **Create session pods** - with ownerReference to CR (enables GC cascade)
8. **Wait for pods Running** - poll until all pods reach Running state
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

## Workload Config Delivery

The Operator never reaches into a running container. Rendered per-node files
land in an immutable ConfigMap mounted read-only at the path the profile
declares as `config_mount`. What happens next belongs to the workload: the
FRR profile's entrypoint waits for the mount, copies the config into place,
and watches it for changes. A profile with no adapter gets no rendered files
and runs exactly what it authored.

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
| `__main__.py` | kopf entry point |
| `handlers.py` | kopf handlers, reconciliation logic, error handling |
| `session_deployer.py` | Pod creation, placement, config delivery, wiring |
| `runtime_session.py` | Verified runtime loading from the uploaded catalog closure |
| `workloads/` | Profile admission, environment resolution, composition, and adapter rendering |
