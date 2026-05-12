# Copyright 2024-2026 .chance (dotchance)
# Licensed under the NodalArc Source Available License 1.0. See LICENSE file.
"""Tests for Node Agent substrate peer reference tracking."""

from __future__ import annotations

import pytest
from node_agent import substrate_monitor

SESSION_ID = "test-session"
WIRING_GENERATION = "sha256:" + "a" * 64


def setup_function() -> None:
    substrate_monitor._reset_for_tests()
    substrate_monitor.set_identity(SESSION_ID, WIRING_GENERATION)


def _ref(vni: int, local_ifname: str) -> substrate_monitor.PeerRef:
    return substrate_monitor.PeerRef(
        session_id=SESSION_ID,
        wiring_generation=WIRING_GENERATION,
        remote_ip="10.0.0.2",
        vni=vni,
        local_ifname=local_ifname,
    )


def test_exact_peer_refs_keep_peer_active_until_last_ref_removed() -> None:
    ref_a = _ref(1001, "isl0")
    ref_b = _ref(1002, "isl1")

    substrate_monitor.add_peer_ref(ref_a)
    substrate_monitor.add_peer_ref(ref_b)

    assert substrate_monitor.get_active_peers() == ["10.0.0.2"]
    assert substrate_monitor.get_active_refs() == [ref_a, ref_b]

    assert substrate_monitor.remove_peer_ref(ref_a) is True
    assert substrate_monitor.get_active_peers() == ["10.0.0.2"]
    assert substrate_monitor.get_active_refs() == [ref_b]

    assert substrate_monitor.remove_peer_ref(ref_b) is True
    assert substrate_monitor.get_active_peers() == []
    assert substrate_monitor.get_active_refs() == []


def test_peer_ref_rejects_wrong_generation() -> None:
    ref = substrate_monitor.PeerRef(
        session_id=SESSION_ID,
        wiring_generation="sha256:" + "b" * 64,
        remote_ip="10.0.0.2",
        vni=1001,
        local_ifname="isl0",
    )

    with pytest.raises(ValueError, match="identity does not match"):
        substrate_monitor.add_peer_ref(ref)


def test_peer_ref_requires_exact_identity_fields() -> None:
    ref = substrate_monitor.PeerRef(
        session_id=SESSION_ID,
        wiring_generation=WIRING_GENERATION,
        remote_ip="",
        vni=1001,
        local_ifname="isl0",
    )

    with pytest.raises(ValueError, match="remote_ip"):
        substrate_monitor.add_peer_ref(ref)
