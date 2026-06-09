#!/usr/bin/env python3
# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""Render the topology/link summary used by ``make status``."""

from __future__ import annotations

import json
import sys
from collections import Counter
from collections.abc import Mapping
from typing import Any


def _plural(count: int, singular: str, plural: str | None = None) -> str:
    word = singular if count == 1 else (plural or f"{singular}s")
    return f"{count} {word}"


def _node_segment(node: Mapping[str, Any]) -> str:
    return str(node.get("segment_id") or node.get("namespace") or "unknown")


def _link_segments(
    link: Mapping[str, Any], node_segment_by_id: Mapping[str, str]
) -> tuple[str | None, str | None]:
    endpoint_segments = link.get("endpoint_segments")
    if isinstance(endpoint_segments, list | tuple) and len(endpoint_segments) == 2:
        return str(endpoint_segments[0]), str(endpoint_segments[1])
    return node_segment_by_id.get(str(link.get("node_a"))), node_segment_by_id.get(
        str(link.get("node_b"))
    )


def summarize_state(state: Mapping[str, Any]) -> str:
    """Return a human-facing topology summary from the VS-API state payload."""
    nodes = tuple(state.get("nodes") or ())
    links = tuple(state.get("links") or ())
    node_segment_by_id = {
        str(node.get("node_id")): _node_segment(node)
        for node in nodes
        if node.get("node_id") is not None
    }

    satellite_nodes_by_segment: Counter[str] = Counter()
    ground_nodes_by_segment: Counter[str] = Counter()
    for node in nodes:
        node_type = node.get("node_type")
        segment = _node_segment(node)
        if node_type == "satellite":
            satellite_nodes_by_segment[segment] += 1
        elif node_type == "ground_station":
            ground_nodes_by_segment[segment] += 1

    isl_by_segment: Counter[str] = Counter()
    inter_segment_links: Counter[tuple[str, str]] = Counter()
    ground_links = 0
    other_links = 0

    for link in links:
        link_type = link.get("link_type")
        if link_type == "ground":
            ground_links += 1
            continue
        if link_type in {"isl", "intra_plane_isl", "cross_plane_isl"}:
            segment_a, segment_b = _link_segments(link, node_segment_by_id)
            if segment_a is not None and segment_a == segment_b:
                isl_by_segment[segment_a] += 1
            elif segment_a is not None and segment_b is not None:
                inter_segment_links[tuple(sorted((segment_a, segment_b)))] += 1
            else:
                other_links += 1
            continue
        if link_type in {"inter_constellation", "inter_body_relay"}:
            segment_a, segment_b = _link_segments(link, node_segment_by_id)
            if segment_a is not None and segment_b is not None:
                inter_segment_links[tuple(sorted((segment_a, segment_b)))] += 1
            else:
                other_links += 1
            continue
        other_links += 1

    lines: list[str] = []
    lines.append("Constellations:")
    if satellite_nodes_by_segment:
        for segment in sorted(satellite_nodes_by_segment):
            node_count = satellite_nodes_by_segment[segment]
            isl_count = isl_by_segment.get(segment, 0)
            lines.append(
                f"  {segment}: {_plural(node_count, 'satellite node')}, {_plural(isl_count, 'ISL')}"
            )
    else:
        lines.append("  none")

    if ground_nodes_by_segment:
        lines.append("Ground segments:")
        for segment in sorted(ground_nodes_by_segment):
            lines.append(f"  {segment}: {_plural(ground_nodes_by_segment[segment], 'ground node')}")

    if inter_segment_links:
        lines.append("Inter-constellation links:")
        for (segment_a, segment_b), count in sorted(inter_segment_links.items()):
            lines.append(f"  {segment_a} <-> {segment_b}: {_plural(count, 'link')}")

    total_isl = sum(isl_by_segment.values())
    total_inter = sum(inter_segment_links.values())
    lines.append(f"ISLs: {total_isl}")
    lines.append(f"Inter-constellation links: {total_inter}")
    lines.append(f"Ground links: {ground_links}")
    if other_links:
        lines.append(f"Other links: {other_links}")
    lines.append(f"Total active: {len(links)}")
    return "\n".join(lines)


def main() -> int:
    state = json.load(sys.stdin)
    print(summarize_state(state))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
