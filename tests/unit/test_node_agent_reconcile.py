"""Host cleanup reports what it removed, what failed and what remains; the entry point exits on it."""

from __future__ import annotations

import errno
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from node_agent import reconcile
from node_agent.reconcile import HostCleanupReport
from pyroute2.netlink.exceptions import NetlinkError

ROOT = Path(__file__).resolve().parents[2]


class _Link(dict):
    def get_attr(self, name, default=None):
        return dict(self["attrs"]).get(name, default)


def _link(name: str, index: int) -> _Link:
    return _Link(index=index, attrs=[("IFLA_IFNAME", name)])


class _FakeIpr:
    """Host links by name; deletes succeed, fail or find the device gone as scripted."""

    def __init__(self, links: dict[str, int], *, outcomes: dict[str, BaseException] | None = None):
        self.links = dict(links)
        self.outcomes = dict(outcomes or {})
        self.deleted: list[str] = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def get_links(self):
        return [_link(name, index) for name, index in self.links.items()]

    def link(self, op, *, index):
        assert op == "del"
        name = next(n for n, i in self.links.items() if i == index)
        outcome = self.outcomes.get(name)
        if outcome is not None:
            if isinstance(outcome, NetlinkError) and outcome.code == errno.ENODEV:
                self.links.pop(name)
            raise outcome
        self.links.pop(name)
        self.deleted.append(name)


def _install(monkeypatch, ipr: _FakeIpr) -> None:
    monkeypatch.setattr(reconcile, "IPRoute", lambda: ipr)
    monkeypatch.setattr(reconcile.socket, "gethostname", lambda: "node02")


MANAGED = {"vx00abcd": 3, "vh00abcd": 4, "i00-cf542f09dc": 5}
UNMANAGED = {"lo": 1, "eth0": 2}


def test_every_recognized_device_removed_is_a_clean_report(monkeypatch) -> None:
    ipr = _FakeIpr({**UNMANAGED, **MANAGED})
    _install(monkeypatch, ipr)

    report = reconcile.clean_and_verify_host_state()

    assert report.clean is True
    assert report.host == "node02"
    assert report.removed == ("vx00abcd", "vh00abcd", "i00-cf542f09dc")
    assert report.failed == () and report.remaining == ()
    assert report.verification_completed is True
    assert ipr.deleted == ["vx00abcd", "vh00abcd", "i00-cf542f09dc"]
    assert set(ipr.links) == set(UNMANAGED)


def test_a_device_gone_before_its_delete_is_absence_not_a_failure(monkeypatch) -> None:
    ipr = _FakeIpr(
        {**UNMANAGED, **MANAGED},
        outcomes={"vh00abcd": NetlinkError(errno.ENODEV, "No such device")},
    )
    _install(monkeypatch, ipr)

    report = reconcile.clean_and_verify_host_state()

    assert report.clean is True
    assert report.removed == ("vx00abcd", "i00-cf542f09dc")
    assert report.failed == () and report.remaining == ()


def test_a_failed_delete_is_reported_with_its_error_and_the_rest_still_run(monkeypatch) -> None:
    ipr = _FakeIpr(
        {**UNMANAGED, **MANAGED},
        outcomes={"vh00abcd": NetlinkError(errno.EBUSY, "Device or resource busy")},
    )
    _install(monkeypatch, ipr)

    report = reconcile.clean_and_verify_host_state()

    assert report.clean is False
    assert report.removed == ("vx00abcd", "i00-cf542f09dc")
    assert report.failed == (("vh00abcd", "NetlinkError: (16, 'Device or resource busy')"),)
    assert report.remaining == ("vh00abcd",)
    assert report.verification_completed is True


def test_a_non_netlink_delete_error_is_a_failure_with_its_text(monkeypatch) -> None:
    ipr = _FakeIpr({**MANAGED}, outcomes={"vx00abcd": PermissionError("Operation not permitted")})
    _install(monkeypatch, ipr)

    report = reconcile.clean_and_verify_host_state()

    assert report.failed == (("vx00abcd", "PermissionError: Operation not permitted"),)
    assert report.remaining == ("vx00abcd",)
    assert report.clean is False


def test_an_initial_enumeration_failure_keeps_its_diagnostic(monkeypatch) -> None:
    def _broken():
        raise OSError(errno.EMFILE, "Too many open files")

    monkeypatch.setattr(reconcile, "IPRoute", _broken)
    monkeypatch.setattr(reconcile.socket, "gethostname", lambda: "node02")

    report = reconcile.clean_and_verify_host_state()

    assert report.clean is False
    assert report.verification_completed is False
    assert report.enumeration_error == "OSError: [Errno 24] Too many open files"
    assert report.removed == () and report.failed == () and report.remaining == ()


def test_a_verification_failure_keeps_its_diagnostic(monkeypatch) -> None:
    ipr = _FakeIpr({**MANAGED})
    _install(monkeypatch, ipr)

    def _broken_enumeration():
        raise OSError(errno.ENOBUFS, "No buffer space available")

    monkeypatch.setattr(reconcile, "get_actual_nodalarc_interfaces", _broken_enumeration)

    report = reconcile.clean_and_verify_host_state()

    assert report.clean is False
    assert report.removed == ("vx00abcd", "vh00abcd", "i00-cf542f09dc")
    assert report.verification_completed is False
    assert report.verification_error == "OSError: [Errno 105] No buffer space available"
    assert report.remaining == ()


def test_entry_prints_the_report_and_exits_on_its_cleanliness(monkeypatch, capsys) -> None:
    clean = HostCleanupReport(
        host="node02", removed=("vx00abcd",), failed=(), remaining=(), verification_completed=True
    )
    monkeypatch.setattr(reconcile, "clean_and_verify_host_state", lambda: clean)
    assert reconcile.main(["--clean"]) == 0
    printed = json.loads(capsys.readouterr().out)
    assert printed == {
        "host": "node02",
        "removed": ["vx00abcd"],
        "failed": [],
        "remaining": [],
        "verification_completed": True,
        "enumeration_error": None,
        "verification_error": None,
    }

    unclean = clean.model_copy(update={"remaining": ("vh00abcd",)})
    monkeypatch.setattr(reconcile, "clean_and_verify_host_state", lambda: unclean)
    assert reconcile.main(["--clean"]) == 1
    assert json.loads(capsys.readouterr().out)["remaining"] == ["vh00abcd"]


@pytest.mark.parametrize("argv", [[], ["--purge"], ["--clean", "extra"]])
def test_entry_refuses_any_other_invocation_without_touching_the_kernel(
    monkeypatch, capsys, argv
) -> None:
    def _never():
        raise AssertionError("the cleaner must not run")

    monkeypatch.setattr(reconcile, "clean_and_verify_host_state", _never)

    assert reconcile.main(argv) == 2
    assert reconcile.USAGE in capsys.readouterr().err


def test_module_entry_point_runs_as_a_subprocess_with_the_repository_paths() -> None:
    """The workstation invocation the teardown uses resolves the module through
    explicit lib and services paths; the usage path proves the entry without
    touching any kernel state."""
    env = {**os.environ, "PYTHONPATH": f"{ROOT / 'lib'}:{ROOT / 'services'}"}
    result = subprocess.run(
        [sys.executable, "-m", "node_agent.reconcile", "--purge"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 2
    assert reconcile.USAGE in result.stderr
    assert result.stdout == ""
