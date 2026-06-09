# K8s Operator

Watches ConstellationSpec CRDs using [kopf](https://kopf.readthedocs.io/).
Manages session lifecycle: create satellite/GS pods, render FRR configs,
write wiring manifests, restart platform pods on session switch.

## CRD: ConstellationSpec

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
```

Singleton — only `current-session` is allowed. The Operator handles the
11-step session switch sequence.
