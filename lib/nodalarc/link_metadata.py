# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""Authoritative link declaration metadata carried on link-state snapshots."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class LinkRuleMetadata:
    """Declaration metadata for one wireable link pair."""

    link_rule_id: str
    topology_mode: str
    endpoint_segments: tuple[str, str]
