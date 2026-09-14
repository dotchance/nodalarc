import socket
import struct

import pytest
from node_agent import kernel_verifier
from node_agent.tc_units import delay_ms_to_netem_us, netem_us_to_ticks
from pyroute2.netlink.rtnl import TC_H_INGRESS
from pyroute2.netlink.rtnl.tcmsg import common as tc_common


def _qdisc_rows(*, delay_ticks: int, rate_bps: int = 1_000_000_000):
    return [
        {
            "kind": "tbf",
            "options": {"attrs": [("TCA_TBF_PARMS", {"rate": rate_bps})]},
            "raw": "tbf raw",
        },
        {
            "kind": "netem",
            "options": {"delay": delay_ticks},
            "raw": "netem raw",
        },
    ]


def test_verify_qdisc_compares_netem_delay_in_tc_scheduler_ticks(monkeypatch):
    expected_ticks = int(tc_common.time2tick(6000))

    def _fake_run_in_pod_namespace(pid, fn):
        assert pid == 1234
        return _qdisc_rows(delay_ticks=expected_ticks)

    monkeypatch.setattr(kernel_verifier, "run_in_pod_namespace", _fake_run_in_pod_namespace)

    proof = kernel_verifier.verify_qdisc(
        1234,
        "isl0",
        delay_ms=6.0,
        rate_mbps=1000.0,
    )

    assert proof.verified is True
    assert f"delay_ticks={expected_ticks}" in proof.evidence


def test_verify_qdisc_uses_shared_fractional_delay_normalization(monkeypatch):
    delay_ms = 3.2466744768292792
    expected_us = delay_ms_to_netem_us(delay_ms)
    expected_ticks = netem_us_to_ticks(expected_us)

    def _fake_run_in_pod_namespace(pid, fn):
        assert pid == 1234
        return _qdisc_rows(delay_ticks=expected_ticks)

    monkeypatch.setattr(kernel_verifier, "run_in_pod_namespace", _fake_run_in_pod_namespace)

    proof = kernel_verifier.verify_qdisc(
        1234,
        "term0",
        delay_ms=delay_ms,
        rate_mbps=1000.0,
    )

    assert proof.verified is True
    assert f"delay_us={expected_us}" in proof.evidence


def test_verify_qdisc_rejects_unconverted_microsecond_delay(monkeypatch):
    def _fake_run_in_pod_namespace(pid, fn):
        assert pid == 1234
        return _qdisc_rows(delay_ticks=6000)

    monkeypatch.setattr(kernel_verifier, "run_in_pod_namespace", _fake_run_in_pod_namespace)

    proof = kernel_verifier.verify_qdisc(
        1234,
        "isl0",
        delay_ms=6.0,
        rate_mbps=1000.0,
    )

    assert proof.verified is False
    assert proof.summary == "netem delay mismatch on isl0"
    assert any(evidence.startswith("expected_ticks=") for evidence in proof.evidence)
    assert "actual_ticks=6000" in proof.evidence


_ETH_P_ALL_INFO = socket.htons(0x0003)
_INFO_ALL = 0xC0000000 | _ETH_P_ALL_INFO  # kernel-assigned priority, protocol all
_INFO_IP = 0xC0000000 | socket.htons(0x0800)


class _Nl(dict):
    """A netlink message as pyroute2 hands it over: fields by key, attributes by name."""

    def get_attr(self, name):
        return dict(self["attrs"]).get(name)


class _Raw(dict):
    """An attribute pyroute2 left undecoded: its header, and the raw bytes it sits in."""

    def __init__(self, type_: int, payload: bytes = b"") -> None:
        super().__init__(header={"length": 4 + len(payload), "type": type_})
        self.data = struct.pack("=HH", 4 + len(payload), type_) + payload
        self.offset = 0
        self.length = len(self.data)


def _unknown(type_: int):
    return ("UNKNOWN", _Raw(type_))


def _chain(index: int):
    return ("UNKNOWN", _Raw(11, struct.pack("=I", index)))


def _u32_root(chain: int = 0):
    return _Nl(attrs=[("TCA_KIND", "u32"), _chain(chain)], handle=0, info=_INFO_ALL)


def _u32_table(handle: int = 0x80000000, chain: int = 0):
    return _Nl(
        attrs=[
            ("TCA_KIND", "u32"),
            _chain(chain),
            ("TCA_OPTIONS", {"attrs": [("TCA_U32_DIVISOR", 1)]}),
        ],
        handle=handle,
        info=_INFO_ALL,
    )


def _without(mapping: dict, *fields: str) -> dict:
    """A copy of one selector or key mapping with the named fields absent, as a partial dump."""
    return {key: value for key, value in mapping.items() if key not in fields}


def _sel_with_key(key: dict):
    return {**_sel(), "keys": [key]}


def _sel(*, nkeys: int = 1, mask: int = 0, val: int = 0, flags: int = 1):
    return {
        "flags": flags,
        "offshift": 0,
        "nkeys": nkeys,
        "offmask": 0,
        "off": 0,
        "offoff": 0,
        "hoff": 0,
        "hmask": 0,
        "keys": [{"key_mask": mask, "key_val": val, "key_off": 0, "key_offmask": 0}] * nkeys,
    }


def _mirred_action(ifindex: int, eaction: int = 1):
    return (
        "TCA_U32_ACT",
        {
            "attrs": [
                (
                    "TCA_ACT_PRIO_1",
                    {
                        "attrs": [
                            ("TCA_ACT_KIND", "mirred"),
                            ("TCA_ACT_STATS", {"attrs": []}),
                            _unknown(10),
                            (
                                "TCA_ACT_OPTIONS",
                                {
                                    "attrs": [
                                        (
                                            "TCA_MIRRED_PARMS",
                                            {
                                                "index": 1,
                                                "capab": 0,
                                                "action": 4,
                                                "refcnt": 1,
                                                "bindcnt": 1,
                                                "eaction": eaction,
                                                "ifindex": ifindex,
                                            },
                                        ),
                                        ("TCA_MIRRED_TM", None),
                                    ]
                                },
                            ),
                        ]
                    },
                )
            ]
        },
    )


def _redirect_rule(
    ifindex: int,
    *,
    eaction: int = 1,
    info: int = _INFO_ALL,
    sel=None,
    extra=(),
    action: bool = True,
    handle: int = 0x80000800,
    chain: int | None = 0,
):
    """The one rule NodalArc installs, as the kernel reports it, with optional deviations."""
    attrs = [
        ("TCA_U32_SEL", sel if sel is not None else _sel()),
        ("TCA_U32_HASH", 0x80000000),
        ("TCA_U32_CLASSID", 0x10000),
        _unknown(11),
    ]
    if action:
        attrs.append(_mirred_action(ifindex, eaction))
    attrs.extend(extra)
    head = [("TCA_KIND", "u32")] + ([_chain(chain)] if chain is not None else [])
    return _Nl(attrs=head + [("TCA_OPTIONS", {"attrs": attrs})], handle=handle, info=info)


def _police_rule():
    return _redirect_rule(0, action=False, extra=[("TCA_U32_POLICE", {"attrs": []})])


def _bpf_direct_action_filter():
    """A direct-action BPF filter acts without any TC action record."""
    return _Nl(
        attrs=[
            ("TCA_KIND", "bpf"),
            _chain(0),
            ("TCA_OPTIONS", {"attrs": [("TCA_BPF_FLAGS", 1), ("TCA_BPF_NAME", "drop.o")]}),
        ],
        handle=1,
        info=_INFO_ALL,
    )


def _shape(*rules):
    return [_u32_root(), _u32_table(), *rules]


class _MirredIpr:
    def __init__(self, filters, *, qdisc: str | None = "ingress") -> None:
        self.filters = filters
        self.qdisc = qdisc

    def link_lookup(self, *, ifname: str):
        return {"src0": [10], "dst0": [20]}.get(ifname, [])

    def get_qdiscs(self, index: int):
        assert index == 10
        rows = [_Nl(attrs=[("TCA_KIND", "noqueue")], parent=0xFFFFFFFF, handle=0)]
        if self.qdisc is not None:
            rows.append(
                _Nl(attrs=[("TCA_KIND", self.qdisc)], parent=TC_H_INGRESS, handle=0xFFFF0000)
            )
        return rows

    def get_filters(self, *, index: int, parent: int):
        assert index == 10
        assert parent == TC_H_INGRESS
        return self.filters


def _mirred_proof(monkeypatch, filters, *, qdisc: str | None = "ingress"):
    monkeypatch.setattr(
        kernel_verifier,
        "run_in_host_namespace",
        lambda fn: fn(_MirredIpr(filters, qdisc=qdisc)),
    )
    return kernel_verifier.verify_mirred("src0", "dst0")


def test_verify_mirred_accepts_exactly_the_configuration_nodalarc_installs(monkeypatch):
    proof = _mirred_proof(monkeypatch, _shape(_redirect_rule(20)))

    assert proof.verified is True
    assert proof.summary == "mirred verified src0->dst0"
    assert "dst_ifindex=20" in proof.evidence


def test_verify_mirred_rejects_stale_redirect_to_wrong_destination(monkeypatch):
    proof = _mirred_proof(monkeypatch, _shape(_redirect_rule(99)))

    assert proof.verified is False
    assert proof.summary == "mirred path contested src0->dst0"
    assert "expected_ifindex=20" in proof.evidence
    assert "observed=['u32-root', 'u32-rule:action', 'u32-table']" in proof.evidence


def test_verify_mirred_names_the_chain_of_an_entry_outside_chain_zero(monkeypatch):
    proof = _mirred_proof(
        monkeypatch, [_u32_root(chain=7), _u32_table(chain=7), _redirect_rule(20, chain=7)]
    )

    assert proof.verified is False
    assert "observed=['chain-7', 'chain-7', 'chain-7']" in proof.evidence


@pytest.mark.parametrize(
    ("filters", "qdisc", "label"),
    [
        (
            _shape(_redirect_rule(20), _redirect_rule(99, handle=0x80000801)),
            "ingress",
            "a second redirect elsewhere",
        ),
        (_shape(_redirect_rule(20, eaction=2)), "ingress", "a mirror instead of a redirect"),
        (
            _shape(_redirect_rule(20, sel=_sel(mask=0xFFFFFFFF, val=0x0A000001))),
            "ingress",
            "a redirect for selected packets only",
        ),
        (_shape(_redirect_rule(20, sel=_sel(nkeys=2))), "ingress", "a two-key selector"),
        (_shape(_redirect_rule(20, sel=_sel(flags=0))), "ingress", "a non-terminal selector"),
        (
            _shape(_redirect_rule(20, sel=_without(_sel(), "off"))),
            "ingress",
            "a selector the dump returned without its offset field",
        ),
        (
            _shape(_redirect_rule(20, sel=_without(_sel(), "flags"))),
            "ingress",
            "a selector the dump returned without its flags",
        ),
        (
            _shape(_redirect_rule(20, sel=_sel_with_key(_without(_sel()["keys"][0], "key_mask")))),
            "ingress",
            "a key the dump returned without its mask",
        ),
        (
            _shape(_redirect_rule(20, sel=_without(_sel(), "keys"))),
            "ingress",
            "a selector the dump returned without its keys",
        ),
        (_shape(_redirect_rule(20, info=_INFO_IP)), "ingress", "a redirect at protocol ip only"),
        (
            _shape(_redirect_rule(20), _bpf_direct_action_filter()),
            "ingress",
            "a direct-action BPF filter beside the redirect",
        ),
        (
            _shape(_redirect_rule(20), _police_rule()),
            "ingress",
            "a policed rule beside the redirect",
        ),
        (
            _shape(_redirect_rule(20, extra=[("TCA_U32_LINK", 0x80100000)])),
            "ingress",
            "a rule linked to another table",
        ),
        (_shape(_redirect_rule(20), _u32_table(0x80100000)), "ingress", "a second hash table"),
        ([_bpf_direct_action_filter()], "ingress", "a direct-action BPF filter alone"),
        (_shape(_police_rule()), "ingress", "a policed rule alone"),
        (_shape(_redirect_rule(20)), "clsact", "a clsact qdisc"),
        (
            [_u32_root(chain=7), _u32_table(chain=7), _redirect_rule(20, chain=7)],
            "ingress",
            "the whole configuration in chain 7, which ingress traffic never enters",
        ),
        (
            _shape(
                _redirect_rule(20),
                _u32_table(0x80100000, chain=7),
                _redirect_rule(20, handle=0x80100800, chain=7),
            ),
            "ingress",
            "a second copy in chain 7 beside the chain-0 configuration",
        ),
        (
            _shape(_redirect_rule(20, chain=None)),
            "ingress",
            "a rule whose chain the kernel did not report",
        ),
    ],
)
def test_verify_mirred_refuses_every_configuration_nodalarc_did_not_install(
    monkeypatch, filters, qdisc, label
):
    proof = _mirred_proof(monkeypatch, filters, qdisc=qdisc)

    assert proof.verified is False, label
    assert proof.summary == "mirred path contested src0->dst0", label


def test_verify_mirred_names_a_missing_qdisc_and_a_missing_rule(monkeypatch):
    assert _mirred_proof(monkeypatch, [], qdisc=None).summary == "missing ingress qdisc src0"
    assert _mirred_proof(monkeypatch, _shape()).summary == "missing mirred redirect src0->dst0"
    assert _mirred_proof(monkeypatch, []).summary == "missing mirred redirect src0->dst0"


def test_ingress_qdisc_kind_reads_the_ingress_parent_only():
    assert kernel_verifier.ingress_qdisc_kind(_MirredIpr([], qdisc=None), 10) is None
    assert kernel_verifier.ingress_qdisc_kind(_MirredIpr([], qdisc="ingress"), 10) == "ingress"
    assert kernel_verifier.ingress_qdisc_kind(_MirredIpr([], qdisc="clsact"), 10) == "clsact"


class _FakeLink(dict):
    def __init__(self, index: int, attrs: dict) -> None:
        super().__init__(index=index)
        self._attrs = attrs

    def get_attr(self, name: str):
        return self._attrs.get(name)


class _VethIpr:
    def __init__(self, link: _FakeLink | None) -> None:
        self.link = link

    def link_lookup(self, *, ifname: str):
        return [self.link["index"]] if self.link is not None else []

    def get_links(self, index: int):
        return [self.link]


def _veth_link(*, kind: str = "veth", peer_nsid=3, peer_index=7) -> _FakeLink:
    attrs = {"IFLA_LINKINFO": {"attrs": [("IFLA_INFO_KIND", kind)]}, "IFLA_LINK": peer_index}
    if peer_nsid is not None:
        attrs["IFLA_LINK_NETNSID"] = peer_nsid
    return _FakeLink(11, attrs)


def test_prove_veth_peer_requires_the_kernel_named_namespace_and_index(monkeypatch):
    monkeypatch.setattr(kernel_verifier, "netnsid_of", lambda ipr, ns_fd: 3)

    ok = kernel_verifier.prove_veth_peer(
        _VethIpr(_veth_link()), "vh000001", peer_ns_fd=5, peer_ifindex=7
    )
    assert ok.verified is True
    assert "peer_nsid=3" in ok.evidence

    cases = {
        "same namespace": _veth_link(peer_nsid=None),
        "other namespace": _veth_link(peer_nsid=4),
        "other index": _veth_link(peer_index=8),
        "not veth": _veth_link(kind="vxlan"),
    }
    for label, link in cases.items():
        proof = kernel_verifier.prove_veth_peer(
            _VethIpr(link), "vh000001", peer_ns_fd=5, peer_ifindex=7
        )
        assert proof.verified is False, label
    assert not kernel_verifier.prove_veth_peer(
        _VethIpr(None), "vh000001", peer_ns_fd=5, peer_ifindex=7
    ).verified


def test_prove_veth_peer_refuses_when_the_kernel_assigned_no_namespace_id(monkeypatch):
    monkeypatch.setattr(kernel_verifier, "netnsid_of", lambda ipr, ns_fd: None)

    proof = kernel_verifier.prove_veth_peer(
        _VethIpr(_veth_link()), "vh000001", peer_ns_fd=5, peer_ifindex=7
    )

    assert proof.verified is False
    assert proof.summary == "veth vh000001 peer namespace mismatch"


def test_verify_qdisc_sentinel_skips_delay_but_still_proves_presence_and_rate(monkeypatch):
    """delay_ms < 0 is the explicit do-not-assert sentinel: the prover has no
    commanded netem value, so the delay must not be compared against an
    invented expectation - but shaping presence and rate stay proven."""

    def _fake_run_in_pod_namespace(pid, fn):
        return _qdisc_rows(delay_ticks=12345)

    monkeypatch.setattr(kernel_verifier, "run_in_pod_namespace", _fake_run_in_pod_namespace)

    proof = kernel_verifier.verify_qdisc(1234, "term0", delay_ms=-1.0, rate_mbps=1000.0)
    assert proof.verified is True
    assert "not asserted" in proof.summary

    # Rate is still asserted under the sentinel.
    wrong_rate = kernel_verifier.verify_qdisc(1234, "term0", delay_ms=-1.0, rate_mbps=4.0)
    assert wrong_rate.verified is False
    assert "rate mismatch" in wrong_rate.summary

    # Missing shaping is still a failure under the sentinel.
    def _no_netem(pid, fn):
        rows = [r for r in _qdisc_rows(delay_ticks=1) if r["kind"] != "netem"]
        return rows

    monkeypatch.setattr(kernel_verifier, "run_in_pod_namespace", _no_netem)
    missing = kernel_verifier.verify_qdisc(1234, "term0", delay_ms=-1.0, rate_mbps=1000.0)
    assert missing.verified is False
