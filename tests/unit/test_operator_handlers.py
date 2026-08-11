"""Unit tests for nodalarc_operator/handlers.py - reconciler state machine.

Tests _reconcile_session() through mocked K8s API responses that simulate
cluster state at each phase. Uses _ReconcilerHarness to encapsulate the
mocks with sane Ready-state defaults.

Uses create_autospec for K8s client mocks to catch signature drift.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, create_autospec, patch

import kubernetes.client
import nodalarc_operator.handlers as handlers_mod
import nodalarc_operator.session_deployer as deployer_mod
import pytest
from nodalarc.catalog_upload import CatalogUploadSelection
from nodalarc.runtime_config import RuntimeConfigProof
from nodalarc_operator.runtime_session import OperatorSessionConfig
from nodalarc_operator.workloads.preparation import WorkloadPreparationError

_SESSION_YAML = (
    Path(__file__).parents[2] / "catalog" / "nodalarc" / "sessions" / "earth-leo-simple.yaml"
).read_text(encoding="utf-8")
_INVALID_SESSION_YAML = _SESSION_YAML.replace(
    "  name: earth-leo-simple\n",
    "  name: earth-leo-simple\n  run_id: user-owned\n",
    1,
)


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

    def active_session(self, spec, _namespace, run_id) -> OperatorSessionConfig:
        digest = "sha256:" + "a" * 64
        selection = CatalogUploadSelection(
            upload_id="operator-test-upload",
            closure_digest=digest,
            file_count=0,
        )
        return OperatorSessionConfig(
            resolution=MagicMock(),
            proof=RuntimeConfigProof(
                source_origin="test.operator_handlers",
                run_id=run_id,
                upload_id=selection.upload_id,
                document_digest=digest,
                closure_digest=digest,
                resolved_semantic_digest=digest,
                file_count=selection.file_count,
                total_bytes=1,
                resolved_node_count=self.expected_count,
            ),
            root_yaml=spec["sessionYaml"],
            catalog_upload=selection,
        )

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
            "resolve_active",
            "nodalarc_operator.handlers._resolve_active_session",
            side_effect=self.active_session,
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
            "check_all_provisioned",
            "nodalarc_operator.handlers.check_all_pods_provisioned",
            return_value=(True, self.expected_count, self.expected_count),
        )
        self._p(
            "check_wiring",
            "nodalarc_operator.handlers.check_wiring_complete",
            return_value=(True, self.expected_count, None),
        )
        self._p(
            "platform_ready",
            "nodalarc_operator.handlers.check_platform_runtime_ready",
            return_value=(True, "runtime verified"),
        )
        self._p(
            "manifest_current",
            "nodalarc_operator.handlers._wiring_manifest_matches_spec",
            return_value=True,
        )
        self._p(
            "runtime_config_current",
            "nodalarc_operator.handlers._runtime_session_config_matches",
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
            "delete_owned",
            "nodalarc_operator.handlers.delete_owned_session_pods",
            return_value=(0, 0),
        )
        self._p(
            "prepare_workloads",
            "nodalarc_operator.handlers.prepare_session_workloads",
            return_value=MagicMock(identity="profiles@sha256:" + "f" * 64),
        )
        self._p(
            "cr_current",
            "nodalarc_operator.handlers._cr_generation_is_current",
            return_value=True,
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
    async def _without_thread_scheduling():
        loop = asyncio.get_running_loop()

        async def _immediate(_executor, function, *args):
            return function(*args)

        with patch.object(loop, "run_in_executor", new=_immediate):
            return await coro

    asyncio.run(_without_thread_scheduling())


def _last_status(h):
    """Extract the status dict from the last _update_status call."""
    mock = h.mock_custom.patch_namespaced_custom_object_status
    assert mock.called, "patch_namespaced_custom_object_status was not called"
    kwargs = mock.call_args[1]
    return kwargs["body"]["status"]


async def _reconcile(h, phase="Ready", **extra_status):
    spec = {"sessionYaml": _SESSION_YAML}
    meta = {"name": "current-session", "uid": "test-uid", "generation": 1}
    status = {"phase": phase, "podCount": h.expected_count, **extra_status}
    run_id = handlers_mod._runtime_identity(spec, meta)[1]
    active_session = h.active_session(spec, "nodalarc", run_id)
    await handlers_mod._reconcile_session(
        spec,
        "current-session",
        "nodalarc",
        meta,
        status,
        active_session,
    )


class TestWorkloadPreparationReconciliation:
    """The real reconciliation entry path preparing session workloads."""

    def _session(self, h):
        spec = {"sessionYaml": _SESSION_YAML}
        meta = {"name": "current-session", "uid": "test-uid", "generation": 1}
        run_id = handlers_mod._runtime_identity(spec, meta)[1]
        return spec, meta, h.active_session(spec, "nodalarc", run_id)

    def _run_reconcile(self, spec, meta, active_session, phase="Ready"):
        _run(
            handlers_mod._reconcile_session(
                spec,
                "current-session",
                "nodalarc",
                meta,
                {"phase": phase, "podCount": 7},
                active_session,
            )
        )

    def test_prepared_identity_flows_to_pod_stamping(self):
        with _ReconcilerHarness(expected_count=7) as h:
            spec, meta, active_session = self._session(h)
            prepared = MagicMock()
            h.mock("prepare_workloads").return_value = prepared
            self._run_reconcile(spec, meta, active_session)
            h.mock("prepare_workloads").assert_called_once()
            assert h.mock("prepare_workloads").call_args[0][0] is active_session.resolution
            identity_arg = h.mock("ensure_pod_identity").call_args[0][4]
            assert identity_arg is prepared.identity

    def test_preparation_failure_drains_then_errors_only_at_zero(self):
        with _ReconcilerHarness(expected_count=7) as h:
            h.mock("prepare_workloads").side_effect = WorkloadPreparationError(
                "profile was not admitted"
            )
            spec, meta, active_session = self._session(h)

            # First pass: pods still exist — deletion requested, phase stays
            # Creating, and nothing else mutates.
            h.mock("delete_owned").return_value = (3, 3)
            self._run_reconcile(spec, meta, active_session)
            h.mock("delete_owned").assert_called_once()
            h.mock("ensure_pod_identity").assert_not_called()
            h.mock("delete_obsolete").assert_not_called()
            assert not h.mock("ensure_cm").called
            assert not h.mock("ensure_pods").called
            status = _last_status(h)
            assert status["phase"] == "Creating"
            assert "removing 3 session pod(s)" in status["message"]

            # Second pass: zero owned pods observed — NOW the phase is Error.
            h.mock("delete_owned").return_value = (0, 0)
            self._run_reconcile(spec, meta, active_session)
            status = _last_status(h)
            assert status["phase"] == "Error"
            assert "Workload selection failed" in status["message"]

    def test_deterministic_failure_inside_deploy_drains(self):
        with _ReconcilerHarness(expected_count=7) as h:
            h.mock("current_ids").return_value = frozenset()
            h.mock("check_ready").return_value = (0, 0)
            h.mock("ensure_cm").side_effect = WorkloadPreparationError(
                "workload artifact ConfigMap exists with different contents"
            )
            h.mock("delete_owned").return_value = (5, 5)
            _run(_reconcile(h, phase="Creating"))
            h.mock("delete_owned").assert_called_once()
            status = _last_status(h)
            assert status["phase"] == "Creating"
            assert "removing 5 session pod(s)" in status["message"]

    def test_stale_generation_never_deletes(self):
        with _ReconcilerHarness(expected_count=7) as h:
            h.mock("prepare_workloads").side_effect = WorkloadPreparationError("not admitted")
            h.mock("cr_current").return_value = False
            spec, meta, active_session = self._session(h)
            self._run_reconcile(spec, meta, active_session)
            h.mock("delete_owned").assert_not_called()
            assert not h.mock_custom.patch_namespaced_custom_object_status.called

    def test_transient_api_failure_stays_creating(self):
        with _ReconcilerHarness(expected_count=7) as h:
            h.mock("current_ids").return_value = frozenset()
            h.mock("check_ready").return_value = (0, 0)
            h.mock("ensure_cm").side_effect = kubernetes.client.rest.ApiException(status=500)
            _run(_reconcile(h, phase="Creating"))
            h.mock("delete_owned").assert_not_called()
            status = _last_status(h)
            assert status["phase"] == "Creating"
            assert "Transient Kubernetes API failure" in status["message"]


class TestReconcileStateMachine:
    def test_reconcile_resolves_once_and_reuses_the_verified_session(self):
        spec = {
            "sessionYaml": _SESSION_YAML,
            "catalogUpload": {
                "upload_id": "operator-test-upload",
                "closure_digest": "sha256:" + "a" * 64,
                "file_count": 0,
            },
        }
        meta = {"name": "current-session", "uid": "test-uid", "generation": 1}

        with _ReconcilerHarness(expected_count=7) as h:
            _run(
                handlers_mod._reconcile_session(
                    spec,
                    "current-session",
                    "nodalarc",
                    meta,
                    {"phase": "Wiring", "podCount": 7},
                )
            )

            h.mock("resolve_active").assert_called_once()
            active_session = h.mock("platform_hash").call_args.kwargs["active_session"]
            assert h.mock("expected_count").call_args.kwargs["active_session"] is active_session
            assert h.mock("platform_ready").call_args.args[2] is active_session.proof

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

    def test_provisioned_pod_networks_write_wiring(self):
        with _ReconcilerHarness(expected_count=7) as h:
            h.mock("manifest_current").return_value = False
            _run(_reconcile(h, phase="Creating"))
            h.mock("ensure_cm").assert_called_once()
            assert h.mock("ensure_cm").call_args.args[5].startswith("run-")
            h.mock("write_wiring").assert_called_once()
            h.mock("write_ips").assert_called_once()
            # Platform services are NOT restarted at publication: they roll
            # only after wiring completes and all workloads run.
            h.mock("restart").assert_not_called()
            status = _last_status(h)
            assert status["platformHash"] == "abc123"
            assert status["runtimeHash"]

    def test_wiring_is_written_before_any_pod_runs(self):
        with _ReconcilerHarness(expected_count=7) as h:
            h.mock("manifest_current").return_value = False
            h.mock("check_all_provisioned").return_value = (True, 7, 0)
            _run(_reconcile(h, phase="Creating"))
            h.mock("write_wiring").assert_called_once()
            h.mock("write_ips").assert_called_once()

    def test_unprovisioned_pod_networks_block_wiring_publication(self):
        with _ReconcilerHarness(expected_count=7) as h:
            h.mock("manifest_current").return_value = False
            h.mock("check_all_provisioned").return_value = (False, 3, 0)
            _run(_reconcile(h, phase="Creating"))
            h.mock("write_wiring").assert_not_called()
            h.mock("write_ips").assert_not_called()
            status = _last_status(h)
            assert status["phase"] == "Creating"
            assert "networked" in status["message"]

    def test_wired_session_waits_for_running_before_ready(self):
        with _ReconcilerHarness(expected_count=7) as h:
            h.mock("check_all_running").return_value = (False, 7, 5)
            _run(_reconcile(h, phase="Wiring"))
            h.mock("platform_ready").assert_not_called()
            # Platform services must not start consuming a session whose
            # workloads have not begun.
            h.mock("restart").assert_not_called()
            status = _last_status(h)
            assert status["phase"] == "Wiring"
            assert status["readyPods"] == 5
            assert "pods running: 5/7" in status["message"]

    def test_platform_services_roll_only_after_workloads_run(self):
        with _ReconcilerHarness(expected_count=7) as h:
            _run(_reconcile(h, phase="Wiring"))
            h.mock("restart").assert_called_once()
            assert h.mock("restart").call_args.args[0] == "nodalarc"
            status = _last_status(h)
            assert status["phase"] == "Ready"

    def test_stale_wiring_manifest_is_rewritten(self):
        with _ReconcilerHarness(expected_count=7) as h:
            h.mock("manifest_current").return_value = False
            _run(_reconcile(h, phase="Wiring"))
            h.mock("ensure_cm").assert_called_once()
            assert h.mock("ensure_cm").call_args.args[5].startswith("run-")
            h.mock("write_wiring").assert_called_once()
            h.mock("write_ips").assert_called_once()
            h.mock("restart").assert_not_called()
            status = _last_status(h)
            assert status["phase"] == "Wiring"
            assert status["observedGeneration"] == 1
            assert status["runtimeHash"]

    def test_stale_runtime_mount_is_refreshed_without_rewiring(self):
        with _ReconcilerHarness(expected_count=7) as h:
            h.mock("runtime_config_current").return_value = False
            _run(_reconcile(h, phase="Ready"))

            h.mock("ensure_cm").assert_called_once()
            h.mock("write_wiring").assert_not_called()
            h.mock("write_ips").assert_not_called()
            h.mock("restart").assert_not_called()
            status = _last_status(h)
            assert status["phase"] == "Wiring"
            assert "Runtime configuration refreshed" in status["message"]

    def test_wiring_complete_sets_ready(self):
        with _ReconcilerHarness(expected_count=7) as h:
            _run(_reconcile(h, phase="Wiring"))
            status = _last_status(h)
            assert status["phase"] == "Ready"
            assert status["platformHash"] == "abc123"
            assert status["runtimeHash"]
            assert status["sessionName"] == "earth-leo-simple"
            assert status["sessionRunId"].startswith("run-")
            assert status["documentDigest"].startswith("sha256:")
            assert status["closureDigest"].startswith("sha256:")
            assert status["resolvedSemanticDigest"].startswith("sha256:")
            assert status["runtimeRelease"] == "nodalarc-test"
            assert status["runtimeBuild"] == "test-build"
            assert "catalogUploadId" not in status
            assert "catalogManifestUid" not in status

    def test_ready_waits_for_platform_runtime_proof(self):
        with _ReconcilerHarness(expected_count=7) as h:
            h.mock("platform_ready").return_value = (
                False,
                "Waiting for OME proof-gated readiness (0/1)",
            )
            _run(_reconcile(h, phase="Wiring"))
            status = _last_status(h)
            assert status["phase"] == "Wiring"
            assert status["message"] == "Waiting for OME proof-gated readiness (0/1)"

    def test_invalid_config_sets_error(self):
        with _ReconcilerHarness(expected_count=7) as h:
            h.mock("expected_count").side_effect = ValueError("Bad constellation")
            _run(_reconcile(h, phase="Pending"))
            status = _last_status(h)
            assert status["phase"] == "Error"
            assert "Bad constellation" in status["message"]

    def test_update_defers_all_status_to_the_single_reconciliation(self):
        with _ReconcilerHarness(expected_count=7) as h:
            with patch(
                "nodalarc_operator.handlers._reconcile_session", new_callable=AsyncMock
            ) as mock_reconcile:
                _run(
                    handlers_mod.on_update(
                        {"sessionYaml": _SESSION_YAML},
                        "current-session",
                        "nodalarc",
                        {"name": "current-session", "uid": "test-uid", "generation": 2},
                        {"phase": "Ready"},
                    )
                )
            h.mock_custom.patch_namespaced_custom_object_status.assert_not_called()
            mock_reconcile.assert_awaited_once()

    def test_platform_hash_change_is_resolved_inside_reconciliation(self):
        with _ReconcilerHarness(expected_count=7) as h:
            with patch(
                "nodalarc_operator.handlers._reconcile_session", new_callable=AsyncMock
            ) as mock_reconcile:
                _run(
                    handlers_mod.on_update(
                        {"sessionYaml": _SESSION_YAML},
                        "current-session",
                        "nodalarc",
                        {"name": "current-session", "uid": "test-uid", "generation": 2},
                        {"phase": "Ready", "platformHash": "old"},
                    )
                )
            h.mock_custom.patch_namespaced_custom_object_status.assert_not_called()
            h.mock("restart").assert_not_called()
            mock_reconcile.assert_awaited_once()

    def test_on_update_invalid_session_identity_reaches_error_status(self):
        with _ReconcilerHarness(expected_count=7) as h:
            _run(
                handlers_mod.on_update(
                    {"sessionYaml": _INVALID_SESSION_YAML},
                    "current-session",
                    "nodalarc",
                    {"name": "current-session", "uid": "test-uid", "generation": 2},
                    {"phase": "Ready", "platformHash": "old"},
                )
            )
            status = _last_status(h)
            assert status["phase"] == "Error"
            assert "session.run_id" in status["message"]
            assert "Extra inputs are not permitted" in status["message"]

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
                        spec={"sessionYaml": _SESSION_YAML},
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
                        {"sessionYaml": _SESSION_YAML},
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
                        {"sessionYaml": _SESSION_YAML},
                        "current-session",
                        "nodalarc",
                        {"name": "current-session", "uid": "test-uid", "generation": 2},
                        {"phase": "Error", "observedGeneration": 1, "platformHash": "old"},
                    )
                )
            h.mock_custom.patch_namespaced_custom_object_status.assert_not_called()
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
                    {"sessionYaml": _SESSION_YAML},
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

    def test_ready_timer_reenters_reconciliation_when_a_pod_is_missing(self):
        """A missing/replaced/non-running pod must take Ready back through
        normal reconciliation, before any platform-proof fast path."""
        with (
            _ReconcilerHarness(expected_count=7) as h,
            patch(
                "nodalarc_operator.handlers._reconcile_session", new_callable=AsyncMock
            ) as mock_reconcile,
        ):
            h.mock("check_all_running").return_value = (False, 7, 6)
            _run(
                handlers_mod.wiring_check(
                    {"sessionYaml": _SESSION_YAML},
                    "current-session",
                    "nodalarc",
                    {"name": "current-session", "uid": "test-uid", "generation": 1},
                    {"phase": "Ready", "podCount": 7},
                )
            )
            mock_reconcile.assert_awaited_once()
            h.mock("platform_ready").assert_not_called()

    def test_ready_timer_reenters_reconciliation_when_wiring_proof_stale(self):
        with (
            _ReconcilerHarness(expected_count=7) as h,
            patch(
                "nodalarc_operator.handlers._reconcile_session", new_callable=AsyncMock
            ) as mock_reconcile,
        ):
            h.mock("check_wiring").return_value = (False, 3, "rewiring in progress")
            _run(
                handlers_mod.wiring_check(
                    {"sessionYaml": _SESSION_YAML},
                    "current-session",
                    "nodalarc",
                    {"name": "current-session", "uid": "test-uid", "generation": 1},
                    {"phase": "Ready", "podCount": 7},
                )
            )
            mock_reconcile.assert_awaited_once()
            h.mock("platform_ready").assert_not_called()

    def test_ready_timer_repairs_missing_runtime_identity_status(self):
        with _ReconcilerHarness(expected_count=7) as h:
            spec = {"sessionYaml": _SESSION_YAML}
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
            assert status["sessionName"] == "earth-leo-simple"
            assert status["sessionRunId"].startswith("run-")
            assert status["platformHash"] == "abc123"
            assert status["runtimeHash"]

    def test_ready_timer_skips_when_runtime_identity_status_is_current(self):
        spec = {"sessionYaml": _SESSION_YAML}
        meta = {"name": "current-session", "uid": "test-uid", "generation": 1}
        identity = handlers_mod._status_identity_fields(spec, meta)

        with (
            _ReconcilerHarness(expected_count=7) as h,
            patch(
                "nodalarc_operator.handlers._reconcile_session", new_callable=AsyncMock
            ) as mock_reconcile,
        ):
            active_session = h.active_session(spec, "nodalarc", identity["sessionRunId"])
            deployment_context = handlers_mod._runtime_deployment_context(
                active_session,
                meta,
                identity["sessionRunId"],
            )
            runtime_hash = deployer_mod.compute_runtime_hash(
                "abc123",
                identity["sessionRunId"],
                active_session.proof,
                deployment_context,
            )
            status = {
                "phase": "Ready",
                "platformHash": "abc123",
                "runtimeHash": runtime_hash,
                **identity,
                **handlers_mod._runtime_proof_status_fields(
                    active_session,
                    deployment_context,
                ),
            }
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

    def test_ready_timer_reconciles_when_current_platform_proof_disappears(self):
        spec = {"sessionYaml": _SESSION_YAML}
        meta = {"name": "current-session", "uid": "test-uid", "generation": 1}
        identity = handlers_mod._status_identity_fields(spec, meta)

        with (
            _ReconcilerHarness(expected_count=7) as h,
            patch(
                "nodalarc_operator.handlers._reconcile_session", new_callable=AsyncMock
            ) as mock_reconcile,
        ):
            h.mock("platform_ready").return_value = (False, "stale pod proof")
            active_session = h.active_session(spec, "nodalarc", identity["sessionRunId"])
            deployment_context = handlers_mod._runtime_deployment_context(
                active_session,
                meta,
                identity["sessionRunId"],
            )
            runtime_hash = deployer_mod.compute_runtime_hash(
                "abc123",
                identity["sessionRunId"],
                active_session.proof,
                deployment_context,
            )
            status = {
                "phase": "Ready",
                "platformHash": "abc123",
                "runtimeHash": runtime_hash,
                **identity,
                **handlers_mod._runtime_proof_status_fields(
                    active_session,
                    deployment_context,
                ),
            }

            _run(
                handlers_mod.wiring_check(
                    spec,
                    "current-session",
                    "nodalarc",
                    meta,
                    status,
                )
            )

            mock_reconcile.assert_awaited_once()

    @pytest.mark.parametrize(
        "field",
        ("documentDigest", "runtimeRelease", "runtimeBuild"),
    )
    def test_ready_timer_reconciles_when_runtime_proof_status_is_stale(self, field: str):
        spec = {"sessionYaml": _SESSION_YAML}
        meta = {"name": "current-session", "uid": "test-uid", "generation": 1}
        identity = handlers_mod._status_identity_fields(spec, meta)

        with (
            _ReconcilerHarness(expected_count=7) as h,
            patch(
                "nodalarc_operator.handlers._reconcile_session", new_callable=AsyncMock
            ) as mock_reconcile,
        ):
            active_session = h.active_session(spec, "nodalarc", identity["sessionRunId"])
            deployment_context = handlers_mod._runtime_deployment_context(
                active_session,
                meta,
                identity["sessionRunId"],
            )
            runtime_hash = deployer_mod.compute_runtime_hash(
                "abc123",
                identity["sessionRunId"],
                active_session.proof,
                deployment_context,
            )
            status = {
                "phase": "Ready",
                "platformHash": "abc123",
                "runtimeHash": runtime_hash,
                **identity,
                **handlers_mod._runtime_proof_status_fields(
                    active_session,
                    deployment_context,
                ),
            }
            status[field] = "stale"

            _run(
                handlers_mod.wiring_check(
                    spec,
                    "current-session",
                    "nodalarc",
                    meta,
                    status,
                )
            )

            mock_reconcile.assert_awaited_once()
