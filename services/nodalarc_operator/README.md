# K8s Operator

Watches ConstellationSpec CRDs using [kopf](https://kopf.readthedocs.io/).
Manages session lifecycle: create satellite/GS pods, render FRR configs,
write wiring manifests, restart platform pods on session switch.

## CRD: ConstellationSpec

VS-API creates the resource after uploading the session's referenced catalog
YAML files as namespaced ConfigMaps. `sessionYaml` remains the exact root
session document; `catalogUpload` selects the uploaded ordinary-YAML closure
that contains every referenced `nodalarc:` and `user:` object. The values below
illustrate the generated CR shape.

```yaml
apiVersion: nodalarc.io/v1alpha1
kind: ConstellationSpec
metadata:
  name: current-session
spec:
  sessionYaml: |
    session:
      name: earth-leo-walker
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
          - select:
              all:
                - segment: ground
                - tag: leo
            terminal:
              all:
                - role: access
                - medium: rf
            min_elevation_deg: 25
          - select: {segment: leo}
            terminal:
              all:
                - role: access
                - medium: rf
    ...
  catalogUpload:
    upload_id: session-7f3a2c
    closure_digest: sha256:0000000000000000000000000000000000000000000000000000000000000000
    file_count: 17
```

Singleton — only `current-session` is allowed. The Operator handles the
session switch sequence. It verifies the selected uploaded YAML files and their
closure digest, resolves them with `sessionYaml` through the shared resolver,
and refuses a CR that omits `catalogUpload` or references an incomplete or
altered closure.
