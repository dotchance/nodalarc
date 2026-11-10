# Copyright 2024-2026 .chance (dotchance)
# SPDX-License-Identifier: AGPL-3.0-only
# Additional terms under AGPL-3.0 Section 7 are in the NOTICE file.
"""Runtime topology aggregation over resolver-produced neighbor facts."""

from nodalarc.models.addressing import (
    NeighborAssignment,
    neighbors_by_node,
)


def _resolved_assignments() -> frozenset[tuple[str, NeighborAssignment]]:
    return frozenset(
        {
            (
                "shell-a-sat-1",
                NeighborAssignment(
                    interface="isl1",
                    peer_node_id="shell-a-sat-2",
                    link_type="intra_plane_isl",
                    priority=1,
                    long_delay=False,
                ),
            ),
            (
                "shell-a-sat-1",
                NeighborAssignment(
                    interface="isl0",
                    peer_node_id="shell-b-sat-1",
                    link_type="cross_plane_isl",
                    priority=0,
                    long_delay=False,
                ),
            ),
            (
                "shell-a-sat-2",
                NeighborAssignment(
                    interface="isl0",
                    peer_node_id="shell-a-sat-1",
                    link_type="intra_plane_isl",
                    priority=0,
                    long_delay=False,
                ),
            ),
            (
                "shell-b-sat-1",
                NeighborAssignment(
                    interface="isl0",
                    peer_node_id="shell-a-sat-1",
                    link_type="cross_plane_isl",
                    priority=0,
                    long_delay=False,
                ),
            ),
        }
    )


def test_neighbors_by_node_is_deterministic_for_resolved_facts() -> None:
    by_node = neighbors_by_node(_resolved_assignments())

    assert [assignment.peer_node_id for assignment in by_node["shell-a-sat-1"]] == [
        "shell-b-sat-1",
        "shell-a-sat-2",
    ]
