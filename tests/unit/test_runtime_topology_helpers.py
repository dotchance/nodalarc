"""Runtime topology aggregation over resolver-produced neighbor facts."""

from nodalarc.models.addressing import (
    NeighborAssignment,
    neighbors_by_node,
    topology_summary,
    unique_isl_pairs,
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
                    bandwidth_mbps=1000.0,
                ),
            ),
            (
                "shell-a-sat-1",
                NeighborAssignment(
                    interface="isl0",
                    peer_node_id="shell-b-sat-1",
                    link_type="cross_plane_isl",
                    priority=0,
                    bandwidth_mbps=500.0,
                ),
            ),
            (
                "shell-a-sat-2",
                NeighborAssignment(
                    interface="isl0",
                    peer_node_id="shell-a-sat-1",
                    link_type="intra_plane_isl",
                    priority=0,
                    bandwidth_mbps=1000.0,
                ),
            ),
            (
                "shell-b-sat-1",
                NeighborAssignment(
                    interface="isl0",
                    peer_node_id="shell-a-sat-1",
                    link_type="cross_plane_isl",
                    priority=0,
                    bandwidth_mbps=500.0,
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


def test_unique_pairs_do_not_regenerate_node_identity() -> None:
    assert unique_isl_pairs(_resolved_assignments()) == {
        ("shell-a-sat-1", "shell-a-sat-2"),
        ("shell-a-sat-1", "shell-b-sat-1"),
    }


def test_topology_summary_aggregates_resolved_neighbor_types() -> None:
    assert topology_summary(_resolved_assignments()) == {
        "intra_per_sat": 1,
        "cross_per_sat": 1,
        "max_cross_per_sat": 1,
        "has_cross_plane": True,
        "total_unique_pairs": 2,
    }
