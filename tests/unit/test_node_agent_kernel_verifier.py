from node_agent import kernel_verifier
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
