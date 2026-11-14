# Copyright 2024-2026 .chance (dotchance)
# SPDX-License-Identifier: AGPL-3.0-only
# Additional terms under AGPL-3.0 Section 7 are in the NOTICE file.
"""Typed kernel mutation facade for Node Agent operation plans."""

from __future__ import annotations

from node_agent import namespace_ops


def update_terminal_delay(pid: int, ifname: str, delay_ms: float, transmit_mbps: float) -> None:
    namespace_ops.update_delay(pid, ifname, delay_ms, transmit_mbps)
