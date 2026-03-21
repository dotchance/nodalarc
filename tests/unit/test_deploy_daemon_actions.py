"""Tests for deploy daemon action validation — tracepath, read_netem_delay, read_link_delays."""

from tools.legacy.deploy_daemon import (
    _handle_read_link_delays,
    _handle_read_netem_delay,
    _handle_tracepath,
)

# --- tracepath ---


def test_tracepath_validates_pod_name():
    resp = _handle_tracepath({"pod": "bad pod!", "target": "10.0.0.1"})
    assert resp["ok"] is False
    assert "Invalid pod name" in resp["error"]


def test_tracepath_validates_target_ip():
    resp = _handle_tracepath({"pod": "gs-fairbanks", "target": "not-an-ip"})
    assert resp["ok"] is False
    assert "Invalid IPv4 target" in resp["error"]


def test_tracepath_requires_pod_and_target():
    resp = _handle_tracepath({"pod": "", "target": ""})
    assert resp["ok"] is False
    assert "pod and target required" in resp["error"]


# --- read_netem_delay ---


def test_read_netem_delay_validates_pid():
    resp = _handle_read_netem_delay({"pid": -1, "ifname": "isl0"})
    assert resp["ok"] is False
    assert "Invalid pid" in resp["error"]


def test_read_netem_delay_validates_pid_type():
    resp = _handle_read_netem_delay({"pid": "abc", "ifname": "isl0"})
    assert resp["ok"] is False
    assert "Invalid pid" in resp["error"]


def test_read_netem_delay_validates_ifname():
    resp = _handle_read_netem_delay({"pid": 1234, "ifname": "BAD NAME!"})
    assert resp["ok"] is False
    assert "Invalid ifname" in resp["error"]


def test_read_netem_delay_validates_ifname_empty():
    resp = _handle_read_netem_delay({"pid": 1234, "ifname": ""})
    assert resp["ok"] is False
    assert "Invalid ifname" in resp["error"]


# --- read_link_delays ---


def test_read_link_delays_requires_list():
    resp = _handle_read_link_delays({"queries": "not a list"})
    assert resp["ok"] is False
    assert "queries must be a list" in resp["error"]


def test_read_link_delays_empty_returns_ok():
    resp = _handle_read_link_delays({"queries": []})
    assert resp["ok"] is True
    assert resp["delays"] == []


def test_read_link_delays_skips_invalid_entries():
    resp = _handle_read_link_delays(
        {
            "queries": [
                {"pid": -1, "ifname": "isl0"},  # invalid pid
                {"pid": 1234, "ifname": "BAD!"},  # invalid ifname
            ],
        }
    )
    assert resp["ok"] is True
    assert len(resp["delays"]) == 2
    # Both should have delay_ms=None since they were skipped
    assert resp["delays"][0]["delay_ms"] is None
    assert resp["delays"][1]["delay_ms"] is None
