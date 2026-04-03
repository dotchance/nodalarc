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
      name: my-session
    constellation: configs/constellations/starlink-176.yaml
    ...
```

Singleton — only `current-session` is allowed. The Operator handles the
11-step session switch sequence.
