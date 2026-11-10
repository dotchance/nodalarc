# Copyright 2024-2026 .chance (dotchance)
# SPDX-License-Identifier: AGPL-3.0-only
"""NodalArc workload adapters.

Each subpackage is one technology's adapter: it translates a resolved node into
the native configuration its image consumes, conforming to the core contract in
``nodalarc.workloads.adapter``. Adapters are ours, but they are modules the core
calls — never code the core contains. The explicit registry in
``adapters.registry`` names every adapter the platform knows.
"""
