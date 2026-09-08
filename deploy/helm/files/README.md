# Helm Chart Files

Nothing in this directory is hand-maintained. `scripts/na-render-helm-chart.sh`
renders every file here into the assembled chart on each install, upgrade and
single-service deploy:

| Rendered file | Source |
|---------------|--------|
| `platform.yaml` | `configs/platform.yaml`, with `kubernetes_namespace` templated to `.Values.namespace` |
| `nats-messaging.yaml` | the NATS registry, `lib/nodalarc/nats_channels.py` |

The templates read them with `.Files.Get`; the source chart contains only this file.
