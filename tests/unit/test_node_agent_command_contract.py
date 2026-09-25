import math

import pytest
from nodalarc.proto import node_agent_pb2
from node_agent.command_contract import (
    CommandContractError,
    RuntimeFence,
    validate_batch_link_up_request,
    validate_kernel_inventory_request,
    validate_set_latency_request,
    worst_error_code,
)


def test_worst_error_code_returns_highest_severity_code() -> None:
    assert (
        worst_error_code(
            [
                node_agent_pb2.NODE_AGENT_INVALID_FIELD,
                node_agent_pb2.NODE_AGENT_OK,
                node_agent_pb2.NODE_AGENT_DIRTY_KERNEL,
                node_agent_pb2.NODE_AGENT_KERNEL_MUTATION_FAILED,
            ]
        )
        == node_agent_pb2.NODE_AGENT_DIRTY_KERNEL
    )


def test_worst_error_code_returns_ok_when_all_entries_ok() -> None:
    assert (
        worst_error_code([node_agent_pb2.NODE_AGENT_OK, node_agent_pb2.NODE_AGENT_OK])
        == node_agent_pb2.NODE_AGENT_OK
    )


_FENCE = RuntimeFence(session_id="session-a", wiring_generation="gen-1")


def _envelope(kind: str) -> node_agent_pb2.CommandEnvelope:
    return node_agent_pb2.CommandEnvelope(
        operation_id="op-1",
        session_id=_FENCE.session_id,
        wiring_generation=_FENCE.wiring_generation,
        operation_kind=kind,
    )


def _rates(transmit: float, receive: float) -> node_agent_pb2.TerminalRates:
    return node_agent_pb2.TerminalRates(transmit_mbps=transmit, receive_mbps=receive)


def _isl_up(**fields) -> node_agent_pb2.InterfaceUp:
    base = {
        "node_id": "leo-sat-0-0",
        "interface_name": "isl0",
        "peer_node_id": "leo-sat-0-1",
        "peer_interface_name": "isl1",
        "link_type": node_agent_pb2.LINK_TYPE_ISL,
        "locality": node_agent_pb2.LOCALITY_LOCAL,
        "latency_ms": 4.0,
        "rates": _rates(2000.0, 2000.0),
    }
    base.update(fields)
    return node_agent_pb2.InterfaceUp(**base)


def _local_ground_up(**fields) -> node_agent_pb2.InterfaceUp:
    base = {
        "node_id": "earth-gs-a",
        "interface_name": "term0",
        "peer_node_id": "geo-tdrs-0",
        "peer_interface_name": "gnd0",
        "link_type": node_agent_pb2.LINK_TYPE_GROUND,
        "locality": node_agent_pb2.LOCALITY_LOCAL,
        "latency_ms": 120.0,
        "gs_id": "earth-gs-a",
        "sat_id": "geo-tdrs-0",
        "rates": _rates(600.0, 50.0),
        "peer_rates": _rates(50.0, 600.0),
    }
    base.update(fields)
    return node_agent_pb2.InterfaceUp(**base)


def _link_up(*interfaces: node_agent_pb2.InterfaceUp) -> node_agent_pb2.BatchLinkUpRequest:
    return node_agent_pb2.BatchLinkUpRequest(
        envelope=_envelope("BatchLinkUp"), interfaces=list(interfaces)
    )


def test_link_up_accepts_each_terminal_with_its_own_directional_rates() -> None:
    validate_batch_link_up_request(_link_up(_isl_up(), _local_ground_up()), fence=_FENCE)


def test_link_up_refuses_an_entry_without_terminal_rates() -> None:
    entry = _isl_up()
    entry.ClearField("rates")

    with pytest.raises(CommandContractError, match="requires rates") as refused:
        validate_batch_link_up_request(_link_up(entry), fence=_FENCE)
    assert refused.value.code == node_agent_pb2.NODE_AGENT_INVALID_FIELD


@pytest.mark.parametrize("bad", [0.0, -5.0, math.nan, math.inf])
@pytest.mark.parametrize("direction", ["transmit", "receive"])
def test_link_up_refuses_unusable_rates(direction: str, bad: float) -> None:
    rates = _rates(2000.0, 2000.0)
    setattr(rates, f"{direction}_mbps", bad)

    with pytest.raises(CommandContractError, match="must be finite and > 0"):
        validate_batch_link_up_request(_link_up(_isl_up(rates=rates)), fence=_FENCE)


def test_local_ground_link_up_requires_the_peer_terminal_rates() -> None:
    entry = _local_ground_up()
    entry.ClearField("peer_rates")

    with pytest.raises(CommandContractError, match="requires peer_rates"):
        validate_batch_link_up_request(_link_up(entry), fence=_FENCE)


def test_peer_rates_are_refused_outside_local_ground() -> None:
    with pytest.raises(CommandContractError, match="only to LOCAL ground"):
        validate_batch_link_up_request(
            _link_up(_isl_up(peer_rates=_rates(2000.0, 2000.0))), fence=_FENCE
        )


def _inventory_entry(**fields) -> node_agent_pb2.KernelInventoryEntry:
    base = {
        "node_id": "earth-gs-a",
        "interface_name": "term0",
        "peer_node_id": "geo-tdrs-0",
        "peer_interface_name": "gnd0",
        "link_type": node_agent_pb2.LINK_TYPE_GROUND,
        "locality": node_agent_pb2.LOCALITY_LOCAL,
        "gs_id": "earth-gs-a",
        "sat_id": "geo-tdrs-0",
    }
    base.update(fields)
    return node_agent_pb2.KernelInventoryEntry(**base)


def _inventory(*entries) -> node_agent_pb2.KernelInventoryRequest:
    return node_agent_pb2.KernelInventoryRequest(
        envelope=_envelope("KernelInventory"), gs_id="earth-gs-a", entries=list(entries)
    )


def test_expected_up_inventory_requires_rates_and_expected_down_carries_none() -> None:
    validate_kernel_inventory_request(
        _inventory(
            _inventory_entry(
                expected_admin_up=True,
                latency_ms=120.0,
                rates=_rates(600.0, 50.0),
                peer_rates=_rates(50.0, 600.0),
            ),
            _inventory_entry(expected_admin_up=False),
        ),
        fence=_FENCE,
    )

    with pytest.raises(CommandContractError, match="requires rates"):
        validate_kernel_inventory_request(
            _inventory(_inventory_entry(expected_admin_up=True, latency_ms=120.0)),
            fence=_FENCE,
        )


def _set_latency_request(rates: node_agent_pb2.TerminalRates | None):
    entry = node_agent_pb2.LatencyEntry(
        node_id="leo-sat-0-0",
        interface_name="isl0",
        latency_ms=4.0,
        link_type=node_agent_pb2.LINK_TYPE_ISL,
    )
    if rates is not None:
        entry.rates.CopyFrom(rates)
    return node_agent_pb2.SetLatencyRequest(envelope=_envelope("SetLatency"), entries=[entry])


@pytest.mark.parametrize("bad", [0.0, -1.0, math.nan, math.inf])
def test_set_latency_requires_the_interface_terminal_rates(bad: float) -> None:
    validate_set_latency_request(_set_latency_request(_rates(2000.0, 2000.0)), fence=_FENCE)
    with pytest.raises(
        CommandContractError, match="rates transmit_mbps and receive_mbps must be finite and > 0"
    ):
        validate_set_latency_request(_set_latency_request(_rates(bad, 2000.0)), fence=_FENCE)
    with pytest.raises(
        CommandContractError, match="rates transmit_mbps and receive_mbps must be finite and > 0"
    ):
        validate_set_latency_request(_set_latency_request(_rates(2000.0, bad)), fence=_FENCE)


def test_set_latency_refuses_an_entry_without_rates() -> None:
    with pytest.raises(CommandContractError, match="SetLatency entry requires rates"):
        validate_set_latency_request(_set_latency_request(None), fence=_FENCE)
