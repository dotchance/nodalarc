"""Fixtures for tests that use NodalArc the way an operator does."""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from .harness.client import Operator, vs_api_base_url
from .harness.network import Clock, read_clock

_PASSED = pytest.StashKey[bool]()


@pytest.fixture
def test_passed(request: pytest.FixtureRequest):
    """Call it after the test: whether the test body passed."""
    return lambda: request.node.stash.get(_PASSED, False)


@pytest.fixture(scope="session")
def operator() -> Operator:
    return Operator(vs_api_base_url())


@pytest.fixture
def clock(operator: Operator) -> Iterator[Clock]:
    """The clock before the test touches it. Afterward the session runs at 1x again."""
    before = read_clock(operator)
    assert before.wall_seconds_per_step, "sim time is not moving before the test starts"
    yield before
    operator.playback("resume")
    operator.playback("set_speed", factor=1.0)


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Two runs happen only when asked for with -m.

    The catalog run replaces the cluster's session a dozen times. The resilience run damages the
    running session on purpose.
    """
    selected = config.getoption("-m") or ""
    for marker in ("catalog", "resilience"):
        if marker in selected:
            continue
        for item in items:
            if item.get_closest_marker(marker):
                item.add_marker(pytest.mark.skip(reason=f"this run is selected with -m {marker}"))


@pytest.hookimpl(wrapper=True, tryfirst=True)
def pytest_runtest_makereport(item: pytest.Item, call: pytest.CallInfo) -> pytest.TestReport:
    """Let a fixture see whether its test passed."""
    report = yield
    if report.when == "call":
        item.stash[_PASSED] = report.passed
    return report
