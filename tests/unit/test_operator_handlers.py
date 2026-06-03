"""Unit tests for nodalarc_operator/handlers.py - reconciler state machine.

Tests _reconcile_session() through mocked K8s API responses that simulate
cluster state at each phase. Uses _ReconcilerHarness to encapsulate the
mocks with sane Ready-state defaults.

Uses create_autospec for K8s client mocks to catch signature drift.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, create_autospec, patch

import kubernetes.client
import nodalarc_operator.handlers as handlers_mod
import nodalarc_operator.session_deployer as deployer_mod
import pytest


@pytest.fixture(autouse=True)
def _reset_operator_module_state():
    """Clear all cached state between tests."""
    deployer_mod._v1 = None
    deployer_mod._apps_v1 = None
    handlers_mod._custom_api = None
    yield
    deployer_mod._v1 = None
    deployer_mod._apps_v1 = None
    handlers_mod._custom_api = None
    handlers_mod._compute_expected_node_ids_cached.cache_clear()


class _ReconcilerHarness:
    """Encapsulates reconciler mocks with Ready-state defaults."""

    def __init__(self, expected_count=7):
        self.expected_count = expected_count
        self.mock_v1 = create_autospec(kubernetes.client.CoreV1Api, instance=True)
        self.mock_apps = create_autospec(kubernetes.client.AppsV1Api, instance=True)
        self.mock_custom = create_autospec(kubernetes.client.CustomObjectsApi, instance=True)
        self._patches = []
        self._mocks = {}

    def expected_ids(self) -> frozenset[str]:
        return frozenset(f"p{i}" for i in range(self.expected_count))

    def _p(self, name, target, **kwargs):
        p = patch(target, **kwargs)
        self._patches.append((name, p))
        return self

    def _build(self):
        self._p("v1", "nodalarc_operator.session_deployer._get_v1", return_value=self.mock_v1)
        self._p(
            "apps", "nodalarc_operator.session_deployer._get_apps_v1", return_value=self.mock_apps
        )
        self._p(
            "custom", "nodalarc_operator.handlers._get_custom_api", return_value=self.mock_custom
        )
        self._p(
            "expected_count",
            "nodalarc_operator.handlers.compute_expected_pod_count",
            return_value=self.expected_count,
        )
        self._p(
            "check_ready",
            "nodalarc_operator.handlers.check_pods_ready",
            return_value=(self.expected_count, self.expected_count),
        )
        self._p(
            "check_all_running",
            "nodalarc_operator.handlers.check_all_pods_running",
            return_value=(True, self.expected_count, self.expected_count),
        )
        self._p(
            "check_wiring",
            "nodalarc_operator.handlers.check_wiring_complete",
            return_value=(True, self.expected_count, None),
        )
        self._p(
            "manifest_current",
            "nodalarc_operator.handlers._wiring_manifest_matches_spec",
            return_value=True,
        )
        self._p(
            "platform_hash",
            "nodalarc_operator.handlers.compute_platform_hash",
            return_value="abc123",
        )
        self._p(
            "old_terminated",
            "nodalarc_operator.handlers.check_old_pods_terminated",
            return_value=True,
        )
        self._p(
            "expected_ids",
            "nodalarc_operator.handlers._compute_expected_node_ids",
            return_value=self.expected_ids(),
        )
        self._p(
            "ensure_pod_identity",
            "nodalarc_operator.handlers.ensure_session_pod_identity",
            return_value=0,
        )
        self._p(
            "stale_pods",
            "nodalarc_operator.handlers.count_stale_session_pods",
            return_value=0,
        )
        self._p(
            "current_ids",
            "nodalarc_operator.handlers.current_session_pod_node_ids",
            return_value=self.expected_ids(),
        )
        self._p(
            "delete_obsolete",
            "nodalarc_operator.handlers._delete_obsolete_pods",
            return_value=0,
        )
        self._p("ensure_cm", "nodalarc_operator.handlers.ensure_session_configmaps")
        self._p("ensure_pods", "nodalarc_operator.handlers.ensure_session_pods")
        self._p("write_wiring", "nodalarc_operator.handlers.write_wiring_manifest")
        self._p("write_ips", "nodalarc_operator.handlers.write_pod_ips_configmap")
        self._p("restart", "nodalarc_operator.handlers.restart_platform_pods")
        self._p("nodalpath", "nodalarc_operator.handlers.set_nodalpath_mode")
        return self

    def __enter__(self):
        self._build()
        for name, p in self._patches:
            self._mocks[name] = p.start()
        return self

    def __exit__(self, *a):
        for _, p in self._patches:
            p.stop()

    def mock(self, name):
        return self._mocks[name]

    def assert_no_write_calls(self):
        for method_name in (
            "create_namespaced_pod",
            "delete_namespaced_pod",
            "create_namespaced_config_map",
            "patch_namespaced_config_map",
            "delete_namespaced_config_map",
        ):
            method = getattr(self.mock_v1, method_name)
            assert not method.called, f"Unexpected write: {method_name} called {method.call_count}x"
        assert not self.mock_custom.patch_namespaced_custom_object_status.called, (
            "Status write on healthy Ready state"
        )
        assert not self._mocks["ensure_cm"].called, "ensure_session_configmaps called"
        assert not self._mocks["ensure_pods"].called, "ensure_session_pods called"
        assert not self._mocks["write_wiring"].called, "write_wiring_manifest called"


def _run(coro):
    asyncio.run(coro)


def _last_status(h):
    """Extract the status dict from the last _update_status call."""
    mock = h.mock_custom.patch_namespaced_custom_object_status
    assert mock.called, "patch_namespaced_custom_object_status was not called"
    kwargs = mock.call_args[1]
    return kwargs["body"]["status"]


async def _reconcile(h, phase="Ready", **extra_status):
    spec = {"sessionYaml": "session:\n  name: test\n"}
    meta = {"name": "current-session", "uid": "test-uid", "generation": 1}
    status = {"phase": phase, "podCount": h.expected_count, **extra_status}
    await handlers_mod._reconcile_session(spec, "current-session", "nodalarc", meta, status)


class TestReconcileStateMachine:
    def test_pending_stale_pods_triggers_cleanup(self):
        with _ReconcilerHarness(expected_count=7) as h:
            h.mock("stale_pods").return_value = 3
            h.mock("old_terminated").return_value = False
            _run(_reconcile(h, phase="Pending"))
            h.mock("old_terminated").assert_called_once()

    def test_fewer_pods_triggers_create(self):
        with _ReconcilerHarness(expected_count=7) as h:
            h.mock("current_ids").return_value = frozenset(f"p{i}" for i in range(3))
            h.mock("check_ready").return_value = (3, 3)
            h.mock("ensure_cm").return_value = {"session_id": "t", "node_vars": {}}
            h.mock("ensure_pods").return_value = 7
            _run(_reconcile(h, phase="Creating"))
            h.mock("ensure_cm").assert_called_once()
            h.mock("ensure_pods").assert_called_once()
            h.mock("restart").assert_not_called()

    def test_more_pods_triggers_scale_down(self):
        with _ReconcilerHarness(expected_count=2) as h:
            h.mock("current_ids").return_value = frozenset({"p0", "p1", "p2"})
            h.mock("check_ready").return_value = (2, 2)
            h.mock("delete_obsolete").return_value = 1
            _run(_reconcile(h, phase="Creating"))
            h.mock("delete_obsolete").assert_called_once()

    def test_obsolete_old_session_pods_are_pruned_before_readiness(self):
        with _ReconcilerHarness(expected_count=2) as h:
            h.mock("delete_obsolete").return_value = 4
            _run(_reconcile(h, phase="Ready"))
            h.mock("ensure_pod_identity").assert_not_called()
            h.mock("check_ready").assert_not_called()
            status = _last_status(h)
            assert status["phase"] == "Creating"
            assert status["message"] == "Pruning 4 pod(s) from a previous session"

    def test_all_running_writes_wiring(self):
        with _ReconcilerHarness(expected_count=7) as h:
            h.mock("manifest_current").return_value = False
            _run(_reconcile(h, phase="Creating"))
            h.mock("ensure_cm").assert_called_once()
            assert h.mock("ensure_cm").call_args.args[5].startswith("run-")
            h.mock("write_wiring").assert_called_once()
            h.mock("write_ips").assert_called_once()
            h.mock("restart").assert_called_once()
            assert h.mock("restart").call_args.args[0] == "nodalarc"
            assert h.mock("restart").call_args.args[1] != "abc123"
            status = _last_status(h)
            assert status["platformHash"] == "abc123"
            assert status["runtimeHash"] == h.mock("restart").call_args.args[1]

    def test_stale_wiring_manifest_is_rewritten(self):
        with _ReconcilerHarness(expected_count=7) as h:
            h.mock("manifest_current").return_value = False
            _run(_reconcile(h, phase="Wiring"))
            h.mock("ensure_cm").assert_called_once()
            assert h.mock("ensure_cm").call_args.args[5].startswith("run-")
            h.mock("write_wiring").assert_called_once()
            h.mock("write_ips").assert_called_once()
            h.mock("restart").assert_called_once()
            assert h.mock("restart").call_args.args[0] == "nodalarc"
            assert h.mock("restart").call_args.args[1] != "abc123"
            status = _last_status(h)
            assert status["phase"] == "Wiring"
            assert status["observedGeneration"] == 1
            assert status["runtimeHash"] == h.mock("restart").call_args.args[1]

    def test_wiring_complete_sets_ready(self):
        with _ReconcilerHarness(expected_count=7) as h:
            _run(_reconcile(h, phase="Wiring"))
            status = _last_status(h)
            assert status["phase"] == "Ready"
            assert status["platformHash"] == "abc123"
            assert status["runtimeHash"]
            assert status["sessionName"] == "test"
            assert status["sessionRunId"].startswith("run-")

    def test_invalid_config_sets_error(self):
        with _ReconcilerHarness(expected_count=7) as h:
            h.mock("expected_count").side_effect = ValueError("Bad constellation")
            _run(_reconcile(h, phase="Pending"))
            status = _last_status(h)
            assert status["phase"] == "Error"
            assert "Bad constellation" in status["message"]

    def test_platform_hash_bootstrap_does_not_claim_observed_generation(self):
        with _ReconcilerHarness(expected_count=7) as h:
            with patch(
                "nodalarc_operator.handlers._reconcile_session", new_callable=AsyncMock
            ) as mock_reconcile:
                _run(
                    handlers_mod.on_update(
                        {"sessionYaml": "session:\n  name: test\n"},
                        "current-session",
                        "nodalarc",
                        {"name": "current-session", "uid": "test-uid", "generation": 2},
                        {"phase": "Ready"},
                    )
                )
            status = _last_status(h)
            assert status["platformHash"] == "abc123"
            assert "sessionName" not in status
            assert "sessionRunId" not in status
            assert "observedGeneration" not in status
            mock_reconcile.assert_awaited_once()

    def test_platform_hash_change_defers_restart_until_manifest_publication(self):
        with _ReconcilerHarness(expected_count=7) as h:
            with patch(
                "nodalarc_operator.handlers._reconcile_session", new_callable=AsyncMock
            ) as mock_reconcile:
                _run(
                    handlers_mod.on_update(
                        {"sessionYaml": "session:\n  name: test\n"},
                        "current-session",
                        "nodalarc",
                        {"name": "current-session", "uid": "test-uid", "generation": 2},
                        {"phase": "Ready", "platformHash": "old"},
                    )
                )
            status = _last_status(h)
            assert status["phase"] == "Creating"
            assert status["message"] == "Session config changed — reconciling session resources"
            assert "platformHash" not in status
            assert "sessionName" not in status
            assert "sessionRunId" not in status
            h.mock("restart").assert_not_called()
            mock_reconcile.assert_awaited_once()

    def test_on_update_invalid_session_identity_reaches_error_status(self):
        with _ReconcilerHarness(expected_count=7) as h:
            _run(
                handlers_mod.on_update(
                    {"sessionYaml": "session:\n  name: test\n  run_id: user-owned\n"},
                    "current-session",
                    "nodalarc",
                    {"name": "current-session", "uid": "test-uid", "generation": 2},
                    {"phase": "Ready", "platformHash": "old"},
                )
            )
            status = _last_status(h)
            assert status["phase"] == "Error"
            assert "session.run_id is operator-managed" in status["message"]

    def test_on_delete_passes_runtime_identity_from_status(self):
        with _ReconcilerHarness(expected_count=7):
            with (
                patch("nodalarc_operator.handlers.teardown_session") as teardown,
                patch("nodalarc_operator.handlers.set_nodalpath_mode") as nodalpath_mode,
            ):
                _run(
                    handlers_mod.on_delete(
                        "current-session",
                        "nodalarc",
                        spec={"sessionYaml": "session:\n  name: test\n"},
                        meta={"name": "current-session", "uid": "test-uid", "generation": 2},
                        status={"sessionRunId": "run-status-0001"},
                    )
                )

        teardown.assert_called_once_with("nodalarc", "run-status-0001")
        nodalpath_mode.assert_called_once_with("nodalarc", "console")

    def test_current_error_generation_is_terminal_until_user_changes_spec(self):
        with _ReconcilerHarness(expected_count=7):
            with patch(
                "nodalarc_operator.handlers._reconcile_session", new_callable=AsyncMock
            ) as mock_reconcile:
                _run(
                    handlers_mod.on_update(
                        {"sessionYaml": "session:\n  name: test\n"},
                        "current-session",
                        "nodalarc",
                        {"name": "current-session", "uid": "test-uid", "generation": 2},
                        {"phase": "Error", "observedGeneration": 2},
                    )
                )
            mock_reconcile.assert_not_awaited()

    def test_stale_error_generation_reconciles_new_spec(self):
        with _ReconcilerHarness(expected_count=7) as h:
            with patch(
                "nodalarc_operator.handlers._reconcile_session", new_callable=AsyncMock
            ) as mock_reconcile:
                _run(
                    handlers_mod.on_update(
                        {"sessionYaml": "session:\n  name: test\n"},
                        "current-session",
                        "nodalarc",
                        {"name": "current-session", "uid": "test-uid", "generation": 2},
                        {"phase": "Error", "observedGeneration": 1, "platformHash": "old"},
                    )
                )
            status = _last_status(h)
            assert status["phase"] == "Creating"
            assert status["message"] == "Session config changed — reconciling session resources"
            mock_reconcile.assert_awaited_once()

    def test_idempotent_on_ready_zero_writes(self):
        with _ReconcilerHarness(expected_count=7) as h:
            _run(_reconcile(h, phase="Ready"))
            for method_name in (
                "create_namespaced_pod",
                "delete_namespaced_pod",
                "create_namespaced_config_map",
                "patch_namespaced_config_map",
                "delete_namespaced_config_map",
            ):
                method = getattr(h.mock_v1, method_name)
                assert not method.called, f"Unexpected write: {method_name}"
            assert not h.mock("ensure_cm").called
            assert not h.mock("ensure_pods").called
            assert not h.mock("write_wiring").called

    def test_ready_no_status_flapping(self):
        with _ReconcilerHarness(expected_count=7) as h:
            _run(_reconcile(h, phase="Ready"))
            calls = h.mock_custom.patch_namespaced_custom_object_status.call_count
            assert calls <= 1, (
                f"Status written {calls} times on already-Ready session. "
                "Multiple writes cause kopf reconciliation loops."
            )

    def test_ready_with_missing_pod_triggers_recreate(self):
        with _ReconcilerHarness(expected_count=7) as h:
            h.mock("current_ids").return_value = frozenset(f"p{i}" for i in range(6))
            h.mock("check_ready").return_value = (6, 6)
            h.mock("ensure_cm").return_value = {"session_id": "t", "node_vars": {}}
            h.mock("ensure_pods").return_value = 7
            _run(_reconcile(h, phase="Ready"))
            h.mock("ensure_cm").assert_called_once()

    def test_error_to_pending_on_valid_resubmit(self):
        with _ReconcilerHarness(expected_count=7) as h:
            _run(_reconcile(h, phase="Pending"))
            h.mock_custom.patch_namespaced_custom_object_status.assert_called()

    def test_wiring_check_api_exception_warns_and_returns(self, caplog):
        with _ReconcilerHarness(expected_count=7) as h:
            h.mock("check_wiring").side_effect = kubernetes.client.rest.ApiException(
                status=500, reason="Internal Server Error"
            )
            _run(_reconcile(h, phase="Wiring"))

            h.mock("check_wiring").assert_called_once_with("nodalarc", 7)
            h.mock_custom.patch_namespaced_custom_object_status.assert_not_called()
            assert "wiring status check error" in caplog.text

    def test_invalid_wiring_status_sets_error_phase(self):
        with _ReconcilerHarness(expected_count=7) as h:
            h.mock("check_wiring").side_effect = ValueError("unknown node entries")
            _run(_reconcile(h, phase="Wiring"))
            status = _last_status(h)
            assert status["phase"] == "Error"
            assert "unknown node entries" in status["message"]

    def test_ensure_pipeline_failure_sets_error_phase(self):
        with _ReconcilerHarness(expected_count=7) as h:
            h.mock("current_ids").return_value = frozenset()
            h.mock("check_ready").return_value = (0, 0)
            h.mock("ensure_cm").side_effect = RuntimeError("Template rendering failed")
            _run(_reconcile(h, phase="Creating"))
            status = _last_status(h)
            assert status["phase"] == "Error"

    def test_retryable_dependency_sets_pending_phase(self):
        with _ReconcilerHarness(expected_count=7) as h:
            h.mock("current_ids").return_value = frozenset()
            h.mock("ensure_cm").side_effect = deployer_mod.RetryableSessionDependency(
                "waiting for old Secret"
            )
            _run(_reconcile(h, phase="Creating"))
            status = _last_status(h)
            assert status["phase"] == "Pending"
            assert "waiting for old Secret" in status["message"]

    def test_pending_timer_reenters_reconciler(self):
        with (
            _ReconcilerHarness(expected_count=7),
            patch(
                "nodalarc_operator.handlers._reconcile_session", new_callable=AsyncMock
            ) as mock_reconcile,
        ):
            _run(
                handlers_mod.wiring_check(
                    {"sessionYaml": "session:\n  name: test\n"},
                    "current-session",
                    "nodalarc",
                    {"name": "current-session", "uid": "test-uid", "generation": 1},
                    {"phase": "Pending"},
                )
            )
            mock_reconcile.assert_awaited_once()

    def test_runtime_refresh_failure_sets_error_phase(self):
        with _ReconcilerHarness(expected_count=7) as h:
            h.mock("manifest_current").return_value = False
            h.mock("ensure_cm").side_effect = RuntimeError("ConfigMap refresh failed")
            _run(_reconcile(h, phase="Wiring"))
            h.mock("write_wiring").assert_not_called()
            status = _last_status(h)
            assert status["phase"] == "Error"
            assert "ConfigMap refresh failed" in status["message"]

    def test_ready_timer_repairs_missing_runtime_identity_status(self):
        with _ReconcilerHarness(expected_count=7) as h:
            spec = {"sessionYaml": "session:\n  name: test\n"}
            meta = {"name": "current-session", "uid": "test-uid", "generation": 1}
            _run(
                handlers_mod.wiring_check(
                    spec,
                    "current-session",
                    "nodalarc",
                    meta,
                    {"phase": "Ready", "podCount": 7},
                )
            )
            status = _last_status(h)
            assert status["phase"] == "Ready"
            assert status["sessionName"] == "test"
            assert status["sessionRunId"].startswith("run-")
            assert status["platformHash"] == "abc123"
            assert status["runtimeHash"]

    def test_ready_timer_skips_when_runtime_identity_status_is_current(self):
        spec = {"sessionYaml": "session:\n  name: test\n"}
        meta = {"name": "current-session", "uid": "test-uid", "generation": 1}
        identity = handlers_mod._status_identity_fields(spec, meta)
        runtime_hash = deployer_mod.compute_runtime_hash("abc123", identity["sessionRunId"])
        status = {
            "phase": "Ready",
            "platformHash": "abc123",
            "runtimeHash": runtime_hash,
            **identity,
        }

        with (
            _ReconcilerHarness(expected_count=7) as h,
            patch(
                "nodalarc_operator.handlers._reconcile_session", new_callable=AsyncMock
            ) as mock_reconcile,
        ):
            _run(
                handlers_mod.wiring_check(
                    spec,
                    "current-session",
                    "nodalarc",
                    meta,
                    status,
                )
            )
            mock_reconcile.assert_not_awaited()
            h.mock_custom.patch_namespaced_custom_object_status.assert_not_called()
