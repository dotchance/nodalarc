from node_agent import kernel_verifier
from node_agent.kernel_constants import TC_H_INGRESS
from node_agent.tc_units import delay_ms_to_netem_us, netem_us_to_ticks
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


def _mirred_filter(ifindex: int):
    return {
        "attrs": [
            ("TCA_KIND", "u32"),
            (
                "TCA_OPTIONS",
                {
                    "attrs": [
                        (
                            "TCA_U32_ACT",
                            {
                                "attrs": [
                                    (
                                        "TCA_ACT_PRIO_1",
                                        {
                                            "attrs": [
                                                ("TCA_ACT_KIND", "mirred"),
                                                (
                                                    "TCA_ACT_OPTIONS",
                                                    {
                                                        "attrs": [
                                                            (
                                                                "TCA_MIRRED_PARMS",
                                                                {"ifindex": ifindex},
                                                            )
                                                        ]
                                                    },
                                                ),
                                            ]
                                        },
                                    )
                                ]
                            },
                        )
                    ]
                },
            ),
        ]
    }


class _MirredIpr:
    def __init__(self, *, redirect_ifindex: int) -> None:
        self.redirect_ifindex = redirect_ifindex

    def link_lookup(self, *, ifname: str):
        return {"src0": [10], "dst0": [20]}.get(ifname, [])

    def get_filters(self, *, index: int, parent: int):
        assert index == 10
        assert parent == TC_H_INGRESS
        return [_mirred_filter(self.redirect_ifindex)]


def test_verify_mirred_requires_exact_destination_ifindex(monkeypatch):
    def _fake_run_in_host_namespace(fn):
        return fn(_MirredIpr(redirect_ifindex=20))

    monkeypatch.setattr(kernel_verifier, "run_in_host_namespace", _fake_run_in_host_namespace)

    proof = kernel_verifier.verify_mirred("src0", "dst0")

    assert proof.verified is True
    assert "dst_ifindex=20" in proof.evidence


def test_verify_mirred_rejects_stale_redirect_to_wrong_destination(monkeypatch):
    def _fake_run_in_host_namespace(fn):
        return fn(_MirredIpr(redirect_ifindex=99))

    monkeypatch.setattr(kernel_verifier, "run_in_host_namespace", _fake_run_in_host_namespace)

    proof = kernel_verifier.verify_mirred("src0", "dst0")

    assert proof.verified is False
    assert proof.summary == "mirred destination mismatch src0->dst0"
    assert "expected_ifindex=20" in proof.evidence
    assert "actual_ifindexes=[99]" in proof.evidence


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
