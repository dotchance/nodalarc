# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""Per-interface MPLS input: written by the creating operation and read back, or
verified on reuse and refused on mismatch. No log-and-return anywhere."""

from __future__ import annotations

import pytest
from node_agent import mpls
from node_agent.kernel_verifier import KernelStateConflict, Proof
from node_agent.mpls import MplsInputError, configure_mpls_input

OK = Proof.ok(
    "mpls input enabled on isl0", "device=isl0", "key=net.mpls.conf.isl0.input", "observed=1"
)
ZERO = Proof.fail(
    "mpls input disabled on isl0",
    "device=isl0",
    "key=net.mpls.conf.isl0.input",
    "expected=1",
    "observed=0",
)
UNREADABLE = Proof.fail(
    "mpls input unreadable on isl0",
    "device=isl0",
    "key=net.mpls.conf.isl0.input",
    "error=No such file or directory",
)


def _kernel(monkeypatch, *, write_error: str | None, read: Proof) -> list[tuple]:
    calls: list[tuple] = []

    def write(pid, key, value, already_in_ns=False):
        calls.append(("write", pid, key, value))
        return write_error

    def verify(pid, ifname):
        calls.append(("read", pid, ifname))
        return read

    monkeypatch.setattr(mpls, "_write_sysctl_in_netns", write)
    monkeypatch.setattr(mpls.kernel_verifier, "verify_mpls_input", verify)
    return calls


def test_created_interface_is_written_then_read_back(monkeypatch) -> None:
    calls = _kernel(monkeypatch, write_error=None, read=OK)

    proof = configure_mpls_input(4000, "isl0", created=True, subject="ISL sat-a/isl0")

    assert proof is OK
    assert calls == [("write", 4000, "net.mpls.conf.isl0.input", "1"), ("read", 4000, "isl0")]


def test_created_interface_write_failure_is_an_error_with_the_kernel_answer(monkeypatch) -> None:
    calls = _kernel(monkeypatch, write_error="[Errno 2] No such file or directory", read=OK)

    with pytest.raises(MplsInputError) as raised:
        configure_mpls_input(4000, "isl0", created=True, subject="ISL sat-a/isl0")

    assert "ISL sat-a/isl0: MPLS input on isl0: write failed: [Errno 2] No such file" in str(
        raised.value
    )
    assert calls == [("write", 4000, "net.mpls.conf.isl0.input", "1")]


@pytest.mark.parametrize("read", [ZERO, UNREADABLE], ids=["zero", "unreadable"])
def test_successful_write_that_does_not_read_back_is_an_error(monkeypatch, read) -> None:
    """The write returned success; the kernel does not show the value. Never ready."""
    calls = _kernel(monkeypatch, write_error=None, read=read)

    with pytest.raises(MplsInputError) as raised:
        configure_mpls_input(4000, "isl0", created=True, subject="ISL sat-a/isl0")

    assert "written but not read back: " + read.summary in str(raised.value)
    for item in read.evidence:
        assert item in str(raised.value)
    assert raised.value.evidence == read.evidence
    assert [c[0] for c in calls] == ["write", "read"]


def test_reused_interface_is_only_read(monkeypatch) -> None:
    calls = _kernel(monkeypatch, write_error=None, read=OK)

    proof = configure_mpls_input(4000, "isl0", created=False, subject="VNI 1001 sat-a/isl0")

    assert proof is OK
    assert calls == [("read", 4000, "isl0")]


@pytest.mark.parametrize("read", [ZERO, UNREADABLE], ids=["zero", "unreadable"])
def test_reused_interface_mismatch_is_refused_not_repaired(monkeypatch, read) -> None:
    calls = _kernel(monkeypatch, write_error=None, read=read)

    with pytest.raises(KernelStateConflict) as raised:
        configure_mpls_input(4000, "isl0", created=False, subject="VNI 1001 sat-a/isl0")

    assert raised.value.subject == "VNI 1001 sat-a/isl0"
    assert raised.value.present == ("isl0@pod",)
    assert raised.value.failures == (read.summary,)
    assert raised.value.failure_evidence == (read.evidence,)
    assert calls == [("read", 4000, "isl0")]
