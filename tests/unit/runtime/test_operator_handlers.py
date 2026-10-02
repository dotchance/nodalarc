"""Unit tests for nodalarc_operator/handlers.py - reconciler state machine.

Tests _reconcile_session() through mocked K8s API responses that simulate
cluster state at each phase. Uses _ReconcilerHarness to encapsulate the
mocks with sane Ready-state defaults.

Uses create_autospec for K8s client mocks to catch signature drift.
"""

from __future__ import annotations

import asyncio

import nodalarc_operator.handlers as handlers_mod
import nodalarc_operator.session_deployer as deployer_mod
import pytest


@pytest.fixture(autouse=True)
def _reset_operator_module_state(monkeypatch: pytest.MonkeyPatch):
    """Clear all cached state between tests."""
    deployer_mod._v1 = None
    deployer_mod._apps_v1 = None
    handlers_mod._custom_api = None
    handlers_mod._selection_schema_verified = False
    monkeypatch.setenv("NODALARC_RELEASE", "nodalarc-test")
    monkeypatch.setenv("NODAL_BUILD", "test-build")
    yield
    deployer_mod._v1 = None
    deployer_mod._apps_v1 = None
    handlers_mod._custom_api = None
    handlers_mod._selection_schema_verified = False


# ---------------------------------------------------------------------------
# The session driver
# ---------------------------------------------------------------------------


def test_requeue_backs_off_and_a_trigger_ends_the_backoff() -> None:
    async def _scenario():
        driver = handlers_mod._SessionDriver(uid="test-uid", namespace="nodalarc")
        loop = asyncio.get_running_loop()
        started = loop.time()
        await driver.next_pass(requeue=True)
        first = loop.time() - started
        started = loop.time()
        loop.call_later(0.1, driver.wakeup.set)
        await driver.next_pass(requeue=True)
        woken = loop.time() - started
        return first, woken

    first, woken = asyncio.run(_scenario())
    assert 0.9 <= first < 1.5
    assert woken < 0.5


def test_session_pods_and_services_wake_the_driver_and_nothing_else_does() -> None:
    assert handlers_mod._moves_a_session({"nodalarc.io/node-id": "sat-a"})
    assert handlers_mod._moves_a_session({"app": "nodalarc-ome"})
    assert handlers_mod._moves_a_session({"app": "nodalarc-scheduler"})
    assert not handlers_mod._moves_a_session({"app": "nodalarc-vs-api"})
    assert not handlers_mod._moves_a_session({})
