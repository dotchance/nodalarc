"""Kernel state under a link's names: create, reuse, or refuse, at the one owner."""

from __future__ import annotations

import pytest
from node_agent import ground_bridge, kernel_verifier
from node_agent.kernel_verifier import KernelStateConflict, Proof, reuse_or_refuse

OK = Proof.ok("fine")
BAD = Proof.fail("tunnel endpoint mismatch", "expected=x", "actual=y")


def test_absent_creates() -> None:
    assert (
        reuse_or_refuse(subject="VNI 7", absent=True, complete=False, proofs=(), evidence=())
        is False
    )


def test_complete_and_proven_reuses() -> None:
    assert (
        reuse_or_refuse(
            subject="VNI 7",
            absent=False,
            complete=True,
            proofs=(OK, OK),
            evidence=("vx000007", "vh000007"),
        )
        is True
    )


def test_complete_but_unproven_refuses() -> None:
    with pytest.raises(KernelStateConflict) as raised:
        reuse_or_refuse(
            subject="VNI 7",
            absent=False,
            complete=True,
            proofs=(OK, BAD),
            evidence=("vx000007", "vh000007"),
        )

    assert raised.value.subject == "VNI 7"
    assert raised.value.failures == ("tunnel endpoint mismatch",)
    assert "not the requested link" in str(raised.value)


def test_partial_refuses_and_names_what_exists() -> None:
    with pytest.raises(KernelStateConflict) as raised:
        reuse_or_refuse(
            subject="VNI 7", absent=False, complete=False, proofs=(), evidence=("vx000007",)
        )

    assert raised.value.present == ("vx000007",)
    assert raised.value.failures == ("incomplete link",)


def test_evidence_wording_decides_nothing() -> None:
    """The decision follows the facts; the description is free to change."""
    assert (
        reuse_or_refuse(
            subject="VNI 7", absent=True, complete=False, proofs=(), evidence=("anything",)
        )
        is False
    )
    with pytest.raises(KernelStateConflict):
        reuse_or_refuse(subject="VNI 7", absent=False, complete=False, proofs=(), evidence=())


class _Ipr:
    def __init__(self) -> None:
        self.closed = False

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.closed = True

    def link_lookup(self, *, ifname: str):
        return {"a0": [1], "b0": [2]}.get(ifname, [])


def _pair(monkeypatch, *, occupied: dict[int, str], proven: bool):
    """Drive `install_redirect_pair` over faked kernel reads; record what it would create."""
    created: list[tuple[str, str]] = []
    monkeypatch.setattr(ground_bridge, "IPRoute", _Ipr)
    monkeypatch.setattr(
        kernel_verifier, "ingress_qdisc_kind", lambda ipr, ifindex: occupied.get(ifindex)
    )
    monkeypatch.setattr(
        kernel_verifier,
        "prove_mirred_redirect",
        lambda ipr, src, dst: (
            Proof.ok(f"mirred verified {src}->{dst}")
            if proven
            else Proof.fail(f"mirred path contested {src}->{dst}")
        ),
    )
    monkeypatch.setattr(
        ground_bridge, "_tc_mirred_redirect", lambda src, dst: created.append((src, dst))
    )
    return created


def test_install_redirect_pair_creates_both_directions_when_neither_side_is_occupied(monkeypatch):
    created = _pair(monkeypatch, occupied={}, proven=False)

    ground_bridge.install_redirect_pair("a0", "b0")

    assert created == [("a0", "b0"), ("b0", "a0")]


def test_install_redirect_pair_reuses_the_proven_pair(monkeypatch):
    created = _pair(monkeypatch, occupied={1: "ingress", 2: "ingress"}, proven=True)

    ground_bridge.install_redirect_pair("a0", "b0")

    assert created == []


@pytest.mark.parametrize(
    ("occupied", "proven", "failure"),
    [
        ({1: "ingress"}, True, "incomplete link"),
        ({2: "clsact"}, True, "incomplete link"),
        ({1: "ingress", 2: "ingress"}, False, "mirred path contested a0->b0"),
    ],
)
def test_install_redirect_pair_refuses_without_creating(monkeypatch, occupied, proven, failure):
    created = _pair(monkeypatch, occupied=occupied, proven=proven)

    with pytest.raises(KernelStateConflict) as raised:
        ground_bridge.install_redirect_pair("a0", "b0")

    assert failure in raised.value.failures
    assert created == []


def test_install_redirect_pair_requires_both_interfaces(monkeypatch):
    _pair(monkeypatch, occupied={}, proven=False)

    with pytest.raises(FileNotFoundError):
        ground_bridge.install_redirect_pair("a0", "missing0")
