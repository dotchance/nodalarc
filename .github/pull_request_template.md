## Summary

Describe what changed and why.

## Scope

- [ ] Backend service
- [ ] Frontend
- [ ] Helm or Kubernetes deployment
- [ ] Build, load, install, teardown, or registry tooling
- [ ] Session configuration or deployment
- [ ] Documentation
- [ ] Tests only

## Design Notes

Call out ownership boundaries, state transitions, public contracts, or compatibility concerns. If this changes lifecycle behavior, NATS subjects, Kubernetes resources, image handling, or session deployment, describe the new contract explicitly.

## Validation

Paste the exact commands run and their results.

```text

```

## Operational Impact

Describe any impact on existing clusters, images, registries, sessions, kernel state, data persistence, or required upgrade steps.

## Checklist

- [ ] I have read the [NodalArc Contributor License Agreement](../CLA.md) and will sign it if the CLA check asks me to.
- [ ] I ran the relevant tests and included the output above.
- [ ] I updated documentation for user-visible behavior or workflow changes.
- [ ] I kept public docs/source free of local scratch notes, tool signatures, and generated footer lines.
- [ ] I did not add hardcoded deployment values that belong in config, Helm values, or shared helper modules.
- [ ] I considered single-node and multi-node behavior where applicable.
