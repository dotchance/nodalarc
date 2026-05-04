# Helm Chart Files

Files in this directory are copies of `configs/` files that the Helm chart
mounts into pods at deploy time. Helm cannot reference paths outside its chart
directory, so these copies are required.

## Keeping in Sync

When the source files change, the copies here must be updated:

| Source | Copy |
|--------|------|
| `configs/platform.yaml` | `deploy/helm/files/platform.yaml` |
| `configs/nodalpath.yaml` | `deploy/helm/files/nodalpath.yaml` |

Future work: generate these from a single source of truth at build time.
