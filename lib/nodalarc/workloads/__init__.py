# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""Provider-neutral node workloads.

One cohesive component owns the workload document models, their admission
policy, package loading, and the mapping from a resolved session to exactly
one workload profile per node. Nothing here renders provider configuration
or touches deployment state.
"""
