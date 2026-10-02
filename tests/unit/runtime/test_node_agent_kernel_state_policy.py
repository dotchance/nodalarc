"""Kernel state under a link's names: create, reuse, or refuse, at the one owner."""

from __future__ import annotations

import pytest
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
    assert raised.value.failure_evidence == (("expected=x", "actual=y"),)
    assert "not the requested link" in str(raised.value)
    assert "tunnel endpoint mismatch [expected=x, actual=y]" in str(raised.value)


def test_partial_refuses_and_names_what_exists() -> None:
    with pytest.raises(KernelStateConflict) as raised:
        reuse_or_refuse(
            subject="VNI 7", absent=False, complete=False, proofs=(), evidence=("vx000007",)
        )

    assert raised.value.present == ("vx000007",)
    assert raised.value.failures == ("incomplete link",)
    assert raised.value.failure_evidence == ((),)
    assert str(raised.value).endswith("failed: incomplete link)")


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
