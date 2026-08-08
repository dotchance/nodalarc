# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""Session workload materialization.

One shared Pod assembly turns an authored container composition into a
Kubernetes pod. Provider composition (which containers, which images, which
volumes) is produced elsewhere; nothing in this package knows any provider.
"""
