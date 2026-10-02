"""Fixtures for tests that use NodalArc the way an operator does."""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from .harness.client import Operator, vs_api_base_url
from .harness.network import Clock, read_clock


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
    """The catalog run replaces the cluster's session a dozen times; it runs only when asked for."""
    if "catalog" in (config.getoption("-m") or ""):
        return
    for item in items:
        if item.get_closest_marker("catalog"):
            item.add_marker(pytest.mark.skip(reason="the catalog run is selected with -m catalog"))
