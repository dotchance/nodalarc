# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""Probe flow destination resolution from resolved catalog sessions."""

from __future__ import annotations

import pytest
from measurement.flow_manager import resolve_dst_ip
from nodalarc.resolve_session import resolve_session

from tests.conftest import build_segment_session_dict


def _resolved(stations: list[str] | None = None):
    return resolve_session(
        build_segment_session_dict(
            name="probe-ip-resolution",
            constellation={"planes": {"count": 1, "sats_per_plane": 2}},
            ground_stations={"stations": stations or ["a", "b"]},
        )
    )


def _ground_ids(resolved):
    return [node.node_id for node in resolved.nodes if node.kind == "ground_station"]


def test_resolves_first_non_default_originated_ipv4_prefix() -> None:
    resolved = _resolved(["a", "b"])
    node_id = _ground_ids(resolved)[1]

    ip = resolve_dst_ip(node_id, resolved)

    assert ip == "172.16.1.1"


def test_default_route_originated_prefix_is_not_probe_destination() -> None:
    resolved = _resolved(["a"])
    nodes = []
    for node in resolved.nodes:
        if node.kind == "ground_station" and node.originated_prefixes is not None:
            nodes.append(
                node.model_copy(
                    update={
                        "originated_prefixes": node.originated_prefixes.model_copy(
                            update={"ipv4": ("0.0.0.0/0", "192.168.50.0/24")}
                        )
                    }
                )
            )
        else:
            nodes.append(node)
    updated = resolved.model_copy(update={"nodes": tuple(nodes)})
    node_id = _ground_ids(updated)[0]

    ip = resolve_dst_ip(node_id, updated)

    assert ip == "192.168.50.1"


def test_falls_back_to_resolved_terr0_when_no_originated_prefix_exists() -> None:
    resolved = _resolved(["a"])
    nodes = [
        node.model_copy(update={"originated_prefixes": None})
        if node.kind == "ground_station"
        else node
        for node in resolved.nodes
    ]
    updated = resolved.model_copy(update={"nodes": tuple(nodes)})
    node_id = _ground_ids(updated)[0]

    ip = resolve_dst_ip(node_id, updated)

    assert ip == "172.16.0.1"


def test_unknown_or_unqualified_destination_raises() -> None:
    resolved = _resolved(["a"])

    with pytest.raises(ValueError, match="unknown node"):
        resolve_dst_ip("ground-gs-unknown", resolved)

    with pytest.raises(ValueError, match="unknown node"):
        resolve_dst_ip("a", resolved)


def test_satellite_destination_is_rejected() -> None:
    resolved = _resolved(["a"])
    sat_id = next(node.node_id for node in resolved.nodes if node.kind == "satellite")

    with pytest.raises(ValueError, match="not a ground node"):
        resolve_dst_ip(sat_id, resolved)
