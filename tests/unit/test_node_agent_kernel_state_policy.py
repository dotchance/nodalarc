"""Kernel state under a link's names: create, reuse, or refuse, at the one owner."""

from __future__ import annotations

import contextlib

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


class _IslIpr:
    """Host links by name; records every mutation `create_mediated_isl` asks for."""

    def __init__(self, present: dict[str, int]) -> None:
        self.present = dict(present)
        self.calls: list[tuple[str, dict]] = []

    def link_lookup(self, *, ifname: str):
        return [self.present[ifname]] if ifname in self.present else []

    def link(self, op: str, **kwargs):
        self.calls.append((op, kwargs))
        if op == "add":
            self.present[kwargs["ifname"]] = 100 + len(self.calls)
            self.present[kwargs["peer"]["ifname"]] = 200 + len(self.calls)
        elif op == "set":
            # A rename keeps the index under the new name; a move into a pod
            # namespace takes the device out of the host view.
            name = next((k for k, v in self.present.items() if v == kwargs.get("index")), None)
            if name is not None and "ifname" in kwargs:
                self.present[kwargs["ifname"]] = self.present.pop(name)
            elif name is not None and "net_ns_pid" in kwargs:
                self.present.pop(name)

    def close(self) -> None:
        pass


def _mediated(
    monkeypatch,
    *,
    present: dict[str, int],
    pod_ends: dict[str, kernel_verifier.PodVethEnd | None],
    peer_proven: bool = True,
    host_mtu_proven: bool = True,
):
    ipr = _IslIpr(present)
    pod_setups: list[int] = []
    redirects: list[tuple[str, str]] = []
    monkeypatch.setattr(ground_bridge, "IPRoute", lambda: ipr)
    monkeypatch.setattr(
        kernel_verifier, "pod_veth_end", lambda pid, ifname: pod_ends.get(ifname), raising=False
    )
    monkeypatch.setattr(
        kernel_verifier,
        "prove_veth_peer",
        lambda ipr_, host, *, peer_ns_fd, peer_ifindex: (
            Proof.ok(f"veth {host} peer verified")
            if peer_proven
            else Proof.fail(f"veth {host} peer namespace mismatch")
        ),
    )
    monkeypatch.setattr(
        kernel_verifier,
        "prove_link_mtu",
        lambda ipr_, host, *, mtu: (
            Proof.ok(f"{host} MTU verified")
            if host_mtu_proven
            else Proof.fail(
                f"{host} MTU mismatch", f"device={host}", f"expected={mtu}", "observed=9000"
            )
        ),
    )
    monkeypatch.setattr(
        ground_bridge, "_pod_netns_fd", lambda pid: contextlib.nullcontext(99), raising=False
    )
    monkeypatch.setattr(
        ground_bridge,
        "_temporary_veth_names",
        lambda: ("_na_hfixed", "_na_nfixed"),
        raising=False,
    )
    monkeypatch.setattr(ground_bridge, "_in_namespace", lambda pid, fn: pod_setups.append(pid))
    monkeypatch.setattr(
        ground_bridge, "install_redirect_pair", lambda a, b: redirects.append((a, b))
    )
    return ipr, pod_setups, redirects


HOST_A = ground_bridge._isl_host_name("sat-a", 0)
HOST_B = ground_bridge._isl_host_name("sat-b", 1)


def _pod_end(*, peer: int, mtu: int = 1500, kind: str = "veth") -> kernel_verifier.PodVethEnd:
    return kernel_verifier.PodVethEnd(ifindex=7, kind=kind, peer_ifindex=peer, mtu=mtu)


def _create() -> tuple[str, str]:
    return ground_bridge.create_mediated_isl(11, 22, "isl0", "isl1", "sat-a", "sat-b", mtu=1500)


def test_create_mediated_isl_creates_both_endpoints_when_absent(monkeypatch) -> None:
    ipr, pod_setups, redirects = _mediated(monkeypatch, present={}, pod_ends={})

    assert _create() == (HOST_A, HOST_B)
    assert [op for op, _ in ipr.calls if op == "add"] == ["add", "add"]
    assert pod_setups == [11, 22]
    assert redirects == [(HOST_A, HOST_B)]


def test_create_mediated_isl_reuses_a_proven_endpoint_without_touching_its_pod_side(
    monkeypatch,
) -> None:
    ipr, pod_setups, redirects = _mediated(
        monkeypatch, present={HOST_A: 5}, pod_ends={"isl0": _pod_end(peer=5)}
    )

    assert _create() == (HOST_A, HOST_B)
    assert [op for op, _ in ipr.calls if op == "add"] == ["add"]
    assert pod_setups == [22]
    assert redirects == [(HOST_A, HOST_B)]


@pytest.mark.parametrize(
    ("pod_end", "peer_proven", "host_mtu_proven", "failure"),
    [
        (_pod_end(peer=5, mtu=1200), True, True, "pod isl0 MTU mismatch"),
        (_pod_end(peer=5), True, False, f"{HOST_A} MTU mismatch"),
        (_pod_end(peer=5), False, True, f"veth {HOST_A} peer namespace mismatch"),
        (_pod_end(peer=6), True, True, "pod isl0 peer index mismatch"),
        (_pod_end(peer=5, kind="dummy"), True, True, "pod isl0 is not veth"),
    ],
)
def test_create_mediated_isl_refuses_an_unproven_endpoint_without_creating(
    monkeypatch, pod_end, peer_proven, host_mtu_proven, failure
) -> None:
    ipr, pod_setups, redirects = _mediated(
        monkeypatch,
        present={HOST_A: 5},
        pod_ends={"isl0": pod_end},
        peer_proven=peer_proven,
        host_mtu_proven=host_mtu_proven,
    )

    with pytest.raises(KernelStateConflict) as raised:
        _create()

    assert failure in raised.value.failures
    assert raised.value.subject == "ISL sat-a/isl0"
    if failure == "pod isl0 MTU mismatch":
        assert "pod isl0 MTU mismatch [device=isl0, expected=1500, observed=1200]" in str(
            raised.value
        )
    assert ipr.calls == []
    assert pod_setups == [] and redirects == []


def test_create_mediated_isl_refuses_a_partial_endpoint_and_names_what_exists(monkeypatch) -> None:
    ipr, pod_setups, redirects = _mediated(monkeypatch, present={HOST_A: 5}, pod_ends={})

    with pytest.raises(KernelStateConflict) as raised:
        _create()

    assert raised.value.failures == ("incomplete link",)
    assert raised.value.present == (HOST_A,)
    assert ipr.calls == [] and pod_setups == [] and redirects == []


def test_create_mediated_isl_refuses_an_occupied_temporary_name_and_deletes_nothing(
    monkeypatch,
) -> None:
    ipr, pod_setups, redirects = _mediated(monkeypatch, present={"_na_hfixed": 9}, pod_ends={})

    with pytest.raises(KernelStateConflict) as raised:
        _create()

    assert raised.value.failures == ("temporary name occupied",)
    assert raised.value.present == ("_na_hfixed",)
    assert ipr.calls == [] and pod_setups == [] and redirects == []
