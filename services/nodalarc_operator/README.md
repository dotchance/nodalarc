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
    identity:
      mode: segment_namespaced
    segments:
      - id: space
        kind: constellation
        source: configs/constellations/starlink-176.yaml
        namespace: space
        central_body: earth
      - id: ground
        kind: ground_set
        source: configs/ground-stations/sets/starlink-176.yaml
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
```

Singleton — only `current-session` is allowed. The Operator handles the
11-step session switch sequence.
