#!/bin/bash
# Legacy: na_deploy replaced by K8s Operator (M7). Use ConstellationSpec CRD instead.
set -euo pipefail
exec python3 -m tools.legacy.na_deploy "$@"
