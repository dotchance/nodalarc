# Copyright 2024-2026 .chance (dotchance)
# SPDX-License-Identifier: AGPL-3.0-only
"""Session workload materialization.

One shared Pod assembly turns an authored container composition into a
Kubernetes pod. Provider composition (which containers, which images, which
volumes) is produced elsewhere; nothing in this package knows any provider.
"""
