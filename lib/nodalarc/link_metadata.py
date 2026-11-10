# Copyright 2024-2026 .chance (dotchance)
# SPDX-License-Identifier: AGPL-3.0-only
# Additional terms under AGPL-3.0 Section 7 are in the NOTICE file.
"""Authoritative link declaration metadata carried on link-state snapshots."""

from __future__ import annotations

from dataclasses import dataclass

from nodalarc.models.link_rules import TopologyMode


@dataclass(frozen=True)
class LinkRuleMetadata:
    """Declaration metadata for one wireable link pair."""

    link_rule_id: str
    topology_mode: TopologyMode
    endpoint_segments: tuple[str, str]
