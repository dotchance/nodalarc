# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""The host network carries every emulated packet whole inside VXLAN.

Emulated interfaces keep the platform MTU wherever their pods run, so the
hosts carry the encapsulation. Before a wiring attempt creates anything, the
Node Agent proves each host path its session traffic can take with
unfragmentable packets of the full encapsulated size, and refuses the attempt
when one is not carried.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from nodalarc.substrate.measurement_contract import RequiredSubstratePair
from nodalarc.vxlan import host_path_mtu_for
from node_agent.substrate_monitor import HostPathProof

from tests.unit.test_node_agent_wiring_mpls import LOCAL_NODE, _manifest, _phase, _Run, _support


def _pair(source: str, target: str, target_ip: str) -> RequiredSubstratePair:
    return RequiredSubstratePair.build(
        source_node=source,
        source_ip="192.0.2.2",
        target_node=target,
        target_ip=target_ip,
        reasons=["isl"],
    )


def test_the_host_path_carries_the_encapsulation_for_its_address_family() -> None:
    assert host_path_mtu_for(9000, "192.0.2.3") == 9050
    assert host_path_mtu_for(9000, "2001:db8::3") == 9070


def _manifest_with_pairs(*pairs: RequiredSubstratePair):
    return _manifest(cross_peer=True).model_copy(update={"required_substrate_pairs": list(pairs)})


def _prover(carried_to: set[str], probes: list[tuple[str, int]]):
    def prove(pair: RequiredSubstratePair, packet_bytes: int) -> HostPathProof:
        probes.append((pair.target_node, packet_bytes))
        return HostPathProof(
            target_node=pair.target_node,
            target_ip=pair.target_ip,
            packet_bytes=packet_bytes,
            carried=pair.target_node in carried_to,
            evidence="3 packets transmitted, 0 received",
        )

    return prove


def test_an_uncarried_host_path_refuses_the_attempt_before_anything_is_created(
    monkeypatch,
) -> None:
    probes: list[tuple[str, int]] = []
    manifest = _manifest_with_pairs(
        _pair(LOCAL_NODE, "node03", "192.0.2.3"), _pair(LOCAL_NODE, "node01", "192.0.2.1")
    )
    cleanups: list[object] = []
    with _Run(monkeypatch, _support(available=True)) as run:
        with (
            patch("node_agent.wiring.prove_host_path_mtu", _prover({"node01"}, probes)),
            patch(
                "node_agent.wiring._cleanup_stale_interfaces",
                lambda *args, **kwargs: cleanups.append(args),
            ),
        ):
            statuses = run.wire(manifest)

    assert sorted(probes) == [("node01", 9050), ("node03", 9050)]
    assert run.calls == []
    assert cleanups == []
    for status in statuses.values():
        refused = _phase(status, "host_path_mtu")
        assert refused.status == "failed"
        assert "does not carry 9000-byte emulated packets inside VXLAN" in refused.error_message
        assert "9050-byte packets to node03 (192.0.2.3) not carried" in refused.error_message
        assert "node01" not in refused.error_message
        assert status.dirty_kernel is False
        assert all(
            phase.status == "pending_pid"
            for phase in status.phases
            if phase.phase != "host_path_mtu"
        )


def test_proven_host_paths_let_the_attempt_wire(monkeypatch) -> None:
    probes: list[tuple[str, int]] = []
    manifest = _manifest_with_pairs(
        _pair(LOCAL_NODE, "node03", "192.0.2.3"),
        # Another host's own path is that host's to prove.
        _pair("node03", LOCAL_NODE, "192.0.2.2"),
    )
    with _Run(monkeypatch, _support(available=True)) as run:
        with patch("node_agent.wiring.prove_host_path_mtu", _prover({"node03"}, probes)):
            statuses = run.wire(manifest)

    assert probes == [("node03", 9050)]
    assert any(call[0] == "sysctl" for call in run.calls)
    assert all(_phase(status, "host_path_mtu").status == "ready" for status in statuses.values())


def test_a_session_on_one_host_probes_nothing(monkeypatch) -> None:
    probes: list[tuple[str, int]] = []
    with _Run(monkeypatch, _support(available=True)) as run:
        with patch("node_agent.wiring.prove_host_path_mtu", _prover(set(), probes)):
            run.wire(_manifest())

    assert probes == []


@pytest.mark.parametrize("mtu", [1279, 9001])
def test_the_platform_refuses_an_emulated_mtu_outside_ipv6_minimum_and_9000(mtu) -> None:
    from nodalarc.platform_config import PlatformConfig
    from pydantic import ValidationError

    from tests.unit.test_platform_config import _valid_config_dict

    with pytest.raises(ValidationError, match="veth_interface_mtu_bytes"):
        PlatformConfig.model_validate({**_valid_config_dict(), "veth_interface_mtu_bytes": mtu})
