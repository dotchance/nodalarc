#!/bin/bash
# Thin bash wrapper — PRD 13.1
set -euo pipefail
exec python3 -m tools.na_deploy "$@"
