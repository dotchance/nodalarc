# Copyright 2024-2026 .chance (dotchance)
# SPDX-License-Identifier: AGPL-3.0-only
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
def created(operator: Operator) -> Iterator[list[str]]:
    """User catalog refs the test creates. Afterward the found session runs and they are deleted."""
    found = next(session for session in operator.sessions() if session.get("active"))
    refs: list[str] = []
    yield refs
    if operator.state()["constellation_name"] != found["name"]:
        operator.run_session(found)
    for ref in reversed(refs):  # a session before the objects it refers to
        operator.delete_user_object(ref)


@pytest.fixture
def clock(operator: Operator) -> Iterator[Clock]:
    """The clock before the test touches it. Afterward the session runs at 1x again."""
    before = read_clock(operator)
    assert before.wall_seconds_per_step, "sim time is not moving before the test starts"
    yield before
    operator.playback("resume")
    operator.playback("set_speed", factor=1.0)


@pytest.hookimpl(wrapper=True, tryfirst=True)
def pytest_runtest_makereport(item: pytest.Item, call: pytest.CallInfo) -> pytest.TestReport:
    """Let a fixture see whether its test passed."""
    report = yield
    if report.when == "call":
        item.stash[_PASSED] = report.passed
    return report
