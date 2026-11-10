# Copyright 2024-2026 .chance (dotchance)
# SPDX-License-Identifier: AGPL-3.0-only
# Additional terms under AGPL-3.0 Section 7 are in the NOTICE file.
"""Provider-neutral node workloads.

One cohesive component owns the workload document models, their admission
policy, package loading, and the mapping from a resolved session to exactly
one workload profile per node. Nothing here renders provider configuration
or touches deployment state.
"""
