"""Unit tests for nodalarc_operator/handlers.py - reconciler state machine.

Tests _reconcile_session() through mocked K8s API responses that simulate
cluster state at each phase. Uses _ReconcilerHarness to encapsulate the
mocks with sane Ready-state defaults.

Uses create_autospec for K8s client mocks to catch signature drift.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, create_autospec, patch

import kubernetes.client
import nodalarc_operator.handlers as handlers_mod
import nodalarc_operator.session_deployer as deployer_mod
import pytest
from nodalarc.catalog_upload import CatalogUploadSelection
from nodalarc.nats_channels import sanitize_session_id
from nodalarc.runtime_config import ResolvedRuntimeConfig, RuntimeConfigProof
from nodalarc.substrate.manifest_contract import (
    WIRING_MANIFEST_CONFIGMAP,
    WIRING_MANIFEST_PAYLOAD_KEY,
    encode_wiring_manifest_payload,
)
from nodalarc_operator.workloads.preparation import WorkloadPreparationError

from tests.unit.test_operator_session_pods import _pod

_SESSION_YAML = (
    Path(__file__).parents[2] / "catalog" / "nodalarc" / "sessions" / "earth-leo-simple.yaml"
).read_text(encoding="utf-8")
_INVALID_SESSION_YAML = _SESSION_YAML.replace(
    "  name: earth-leo-simple\n",
    "  name: earth-leo-simple\n  run_id: user-owned\n",
    1,
)
_SELECTION = {
    "upload_id": "operator-test-upload",
    "closure_digest": "sha256:" + "a" * 64,
    "file_count": 0,
}
_SPEC = {"sessionYaml": _SESSION_YAML, "catalogUpload": _SELECTION}
_META = {"name": "current-session", "uid": "test-uid", "generation": 1}
_PREPARED_IDENTITY = "profiles@sha256:" + "f" * 64
_INVALID_SPEC = {"sessionYaml": _INVALID_SESSION_YAML, "catalogUpload": _SELECTION}


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
        self.run_label = sanitize_session_id(handlers_mod._runtime_identity(_SPEC, _META)[1])
        # The session pods the fake API lists: by default one current, running
        # pod per expected node. ``pod_lists`` queues one-shot answers first.
        self.pods = [self.pod(node_id) for node_id in sorted(self.expected_ids())]
        self.pod_lists: list[list] = []
        self.mock_v1.list_namespaced_pod.side_effect = self._list_pods

    def expected_ids(self) -> frozenset[str]:
        return frozenset(f"p{i}" for i in range(self.expected_count))

    def pod(self, node_id: str, **overrides):
        """A session pod of this CR, current for the desired run and selection."""
        fields = {
            "owner_uid": "test-uid",
            "owner_uid_label": "test-uid",
            "run": self.run_label,
            "selection": _PREPARED_IDENTITY,
            **overrides,
        }
        return _pod(node_id, **fields)

    def _list_pods(self, namespace, label_selector=None):
        items = self.pod_lists.pop(0) if self.pod_lists else self.pods
        return kubernetes.client.V1PodList(items=list(items))

    def deleted(self) -> list[tuple[str, str]]:
        """(pod name, UID precondition) for every delete request."""
        return [
            (call.args[0], call.kwargs["body"].preconditions.uid)
            for call in self.mock_v1.delete_namespaced_pod.call_args_list
        ]

    def active_session(self, spec, _namespace, run_id) -> ResolvedRuntimeConfig:
        digest = "sha256:" + "a" * 64
        selection = CatalogUploadSelection(
            upload_id="operator-test-upload",
            closure_digest=digest,
            file_count=0,
        )
        resolution = MagicMock()
        resolution.resolved.node_ids.return_value = sorted(self.expected_ids())
        return ResolvedRuntimeConfig(
            resolution=resolution,
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
            root_yaml=spec["sessionYaml"].encode("utf-8"),
            selection=selection,
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
            "resolve_active",
            "nodalarc_operator.handlers._resolve_active_session",
            side_effect=self.active_session,
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
            "prepare_workloads",
            "nodalarc_operator.handlers.prepare_session_workloads",
            return_value=MagicMock(identity=_PREPARED_IDENTITY),
        )
        self._p(
            "cr_current",
            "nodalarc_operator.handlers._cr_generation_is_current",
            return_value=True,
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
    spec = _SPEC
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
        spec = _SPEC
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

    def test_prepared_identity_flows_to_pod_classification(self):
        """Pods stamped with another selection are replaced, never counted."""
        with _ReconcilerHarness(expected_count=7) as h:
            spec, meta, active_session = self._session(h)
            prepared = MagicMock(identity="profiles@sha256:" + "e" * 64)
            h.mock("prepare_workloads").return_value = prepared
            self._run_reconcile(spec, meta, active_session)
            h.mock("prepare_workloads").assert_called_once()
            assert h.mock("prepare_workloads").call_args[0][0] is active_session.resolution
            assert sorted(name for name, _uid in h.deleted()) == sorted(h.expected_ids())
            assert all(uid == f"uid-{name}" for name, uid in h.deleted())
            status = _last_status(h)
            assert status["phase"] == "Creating"
            assert status["message"].startswith("Replacing 7 session pod(s)")

    def test_missing_pods_are_created_with_the_prepared_identity(self):
        with _ReconcilerHarness(expected_count=7) as h:
            h.pods = []
            h.mock("ensure_cm").return_value = {"session_id": "t", "pod_inventory": {}}
            _run(_reconcile(h, phase="Creating"))
            pod_identity = h.mock("ensure_pods").call_args.args[3]
            assert pod_identity.selection_identity == _PREPARED_IDENTITY
            assert pod_identity.run_label == h.run_label
            assert pod_identity.expected_node_ids == h.expected_ids()

    def test_preparation_failure_drains_then_errors_only_at_zero(self):
        with _ReconcilerHarness(expected_count=7) as h:
            h.mock("prepare_workloads").side_effect = WorkloadPreparationError(
                "profile was not admitted"
            )
            spec, meta, active_session = self._session(h)

            # First pass: pods still exist — deletion requested, phase stays
            # Creating, and nothing else mutates.
            h.pods = [h.pod(f"p{i}") for i in range(3)]
            self._run_reconcile(spec, meta, active_session)
            assert h.deleted() == [("p0", "uid-p0"), ("p1", "uid-p1"), ("p2", "uid-p2")]
            h.mock_v1.create_namespaced_pod.assert_not_called()
            assert not h.mock("ensure_cm").called
            assert not h.mock("ensure_pods").called
            status = _last_status(h)
            assert status["phase"] == "Creating"
            assert "removing 3 session pod(s)" in status["message"]

            # Second pass: zero owned pods observed — NOW the phase is Error.
            h.pods = []
            self._run_reconcile(spec, meta, active_session)
            status = _last_status(h)
            assert status["phase"] == "Error"
            assert "Workload selection failed" in status["message"]

    def test_deterministic_failure_inside_deploy_drains(self):
        with _ReconcilerHarness(expected_count=7) as h:
            # The reconcile observation finds no pods; by the time the drain
            # observes, five owned pods exist.
            h.pod_lists = [[], [h.pod(f"p{i}") for i in range(5)]]
            h.mock("ensure_cm").side_effect = WorkloadPreparationError(
                "workload artifact ConfigMap exists with different contents"
            )
            _run(_reconcile(h, phase="Creating"))
            assert len(h.deleted()) == 5
            status = _last_status(h)
            assert status["phase"] == "Creating"
            assert "removing 5 session pod(s)" in status["message"]

    def test_stale_generation_never_deletes(self):
        with _ReconcilerHarness(expected_count=7) as h:
            h.mock("prepare_workloads").side_effect = WorkloadPreparationError("not admitted")
            h.mock("cr_current").return_value = False
            spec, meta, active_session = self._session(h)
            self._run_reconcile(spec, meta, active_session)
            h.mock_v1.delete_namespaced_pod.assert_not_called()
            assert not h.mock_custom.patch_namespaced_custom_object_status.called

    def test_transient_api_failure_stays_creating(self):
        with _ReconcilerHarness(expected_count=7) as h:
            h.pods = []
            h.mock("ensure_cm").side_effect = kubernetes.client.rest.ApiException(status=500)
            _run(_reconcile(h, phase="Creating"))
            h.mock_v1.delete_namespaced_pod.assert_not_called()
            status = _last_status(h)
            assert status["phase"] == "Creating"
            assert "Transient Kubernetes API failure" in status["message"]


class TestReconcileStateMachine:
    def test_reconcile_resolves_once_and_reuses_the_verified_session(self):
        spec = _SPEC
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
            assert h.mock("prepare_workloads").call_args.args[0] is active_session.resolution
            assert h.mock("platform_ready").call_args.args[2] is active_session.proof
            assert _last_status(h)["podCount"] == 7

    def test_terminating_pods_hold_the_session_pending(self):
        with _ReconcilerHarness(expected_count=7) as h:
            h.pods = [h.pod(f"p{i}", terminating=True) for i in range(3)]
            _run(_reconcile(h, phase="Pending"))
            h.mock_v1.delete_namespaced_pod.assert_not_called()
            assert not h.mock("ensure_pods").called
            status = _last_status(h)
            assert status["phase"] == "Pending"
            assert status["message"] == "Waiting for 3 old session pods to terminate"

    def test_fewer_pods_triggers_create(self):
        with _ReconcilerHarness(expected_count=7) as h:
            h.pods = [h.pod(f"p{i}") for i in range(3)]
            h.mock("ensure_cm").return_value = {"session_id": "t", "pod_inventory": {}}
            h.mock("ensure_pods").return_value = 7
            _run(_reconcile(h, phase="Creating"))
            h.mock("ensure_cm").assert_called_once()
            h.mock("ensure_pods").assert_called_once()
            h.mock("restart").assert_not_called()

    def test_more_pods_triggers_scale_down(self):
        with _ReconcilerHarness(expected_count=2) as h:
            h.pods = [h.pod("p0"), h.pod("p1"), h.pod("p2")]
            _run(_reconcile(h, phase="Creating"))
            assert h.deleted() == [("p2", "uid-p2")]

    def test_obsolete_old_session_pods_are_pruned_before_readiness(self):
        with _ReconcilerHarness(expected_count=2) as h:
            h.pods = [
                h.pod("p0"),
                h.pod("p1"),
                *(h.pod(f"old{i}", run="run-previous") for i in range(4)),
            ]
            _run(_reconcile(h, phase="Ready"))
            assert sorted(h.deleted()) == [(f"old{i}", f"uid-old{i}") for i in range(4)]
            h.mock("platform_ready").assert_not_called()
            status = _last_status(h)
            assert status["phase"] == "Creating"
            assert status["message"].startswith("Replacing 4 session pod(s)")

    def test_foreign_session_pod_is_reported_and_never_touched(self):
        with _ReconcilerHarness(expected_count=2) as h:
            h.pods = [
                h.pod("p0"),
                h.pod("p1"),
                h.pod("p1", name="stray", owner_uid="other-uid"),
            ]
            _run(_reconcile(h, phase="Ready"))
            h.mock_v1.delete_namespaced_pod.assert_not_called()
            assert not h.mock("ensure_pods").called
            h.mock("platform_ready").assert_not_called()
            status = _last_status(h)
            assert status["phase"] == "Pending"
            assert (
                "1 session pod(s) not owned by ConstellationSpec current-session/test-uid"
                in (status["message"])
            )
            assert "stray (owner: current-session/other-uid)" in status["message"]

    def test_a_delete_conflict_is_reported_and_reobserved(self):
        with _ReconcilerHarness(expected_count=2) as h:
            h.pods = [h.pod("p0"), h.pod("p1"), h.pod("p2"), h.pod("p3")]
            h.mock_v1.delete_namespaced_pod.side_effect = kubernetes.client.rest.ApiException(
                status=409, reason="Precondition failed"
            )
            _run(_reconcile(h, phase="Creating"))
            assert h.deleted() == [("p2", "uid-p2")]
            status = _last_status(h)
            assert (
                "deletion of p2 conflicted (Precondition failed), reobserving"
                in (status["message"])
            )

    def test_a_failed_delete_propagates(self):
        with _ReconcilerHarness(expected_count=2) as h:
            h.pods = [h.pod("p0"), h.pod("p1"), h.pod("p2")]
            h.mock_v1.delete_namespaced_pod.side_effect = kubernetes.client.rest.ApiException(
                status=500
            )
            with pytest.raises(kubernetes.client.rest.ApiException):
                _run(_reconcile(h, phase="Creating"))

    def test_duplicate_current_pods_for_one_node_are_refused(self):
        with _ReconcilerHarness(expected_count=2) as h:
            h.pods = [h.pod("p0"), h.pod("p1"), h.pod("p1", name="p1-copy", uid="uid-copy")]
            _run(_reconcile(h, phase="Creating"))
            h.mock_v1.delete_namespaced_pod.assert_not_called()
            status = _last_status(h)
            assert status["phase"] == "Error"
            assert "more than one current session pod for node: p1" in status["message"]

    def test_an_empty_resolved_node_set_is_refused(self):
        with _ReconcilerHarness(expected_count=0) as h:
            _run(_reconcile(h, phase="Pending"))
            h.mock_v1.list_namespaced_pod.assert_not_called()
            status = _last_status(h)
            assert status["phase"] == "Error"
            assert "0 nodes" in status["message"]

    def test_wiring_publishes_placement_and_addresses_of_current_pods_only(self):
        with _ReconcilerHarness(expected_count=2) as h:
            h.mock("manifest_current").return_value = False
            h.pods = [
                h.pod("p0", k8s_node="node02", pod_ip="10.42.2.1"),
                h.pod("p1", k8s_node="node03", pod_ip="10.42.3.1"),
            ]
            _run(_reconcile(h, phase="Creating"))
            assert h.mock("write_ips").call_args.args == (
                "nodalarc",
                {"p0": "10.42.2.1", "p1": "10.42.3.1"},
            )
            assert h.mock("write_wiring").call_args.args[6] == {"p0": "node02", "p1": "node03"}

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
            h.pods = [h.pod(f"p{i}", phase="Pending", running=0) for i in range(7)]
            _run(_reconcile(h, phase="Creating"))
            h.mock("write_wiring").assert_called_once()
            h.mock("write_ips").assert_called_once()

    def test_unprovisioned_pod_networks_block_wiring_publication(self):
        with _ReconcilerHarness(expected_count=7) as h:
            h.mock("manifest_current").return_value = False
            h.pods = [
                h.pod(f"p{i}", pod_ip=None if i >= 3 else "10.42.0.5", phase="Pending", running=0)
                for i in range(7)
            ]
            _run(_reconcile(h, phase="Creating"))
            h.mock("write_wiring").assert_not_called()
            h.mock("write_ips").assert_not_called()
            status = _last_status(h)
            assert status["phase"] == "Creating"
            assert "networked" in status["message"]

    def test_wired_session_waits_for_running_before_ready(self):
        with _ReconcilerHarness(expected_count=7) as h:
            h.pods = [h.pod(f"p{i}", running=1 if i < 5 else 0) for i in range(7)]
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
            h.mock("platform_hash").side_effect = ValueError("Bad constellation")
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
                        _SPEC,
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
                        _SPEC,
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
                    _INVALID_SPEC,
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

    def test_on_update_spec_without_a_selection_reaches_error_status(self):
        with _ReconcilerHarness(expected_count=7) as h:
            _run(
                handlers_mod.on_update(
                    {"sessionYaml": _SESSION_YAML},
                    "current-session",
                    "nodalarc",
                    {"name": "current-session", "uid": "test-uid", "generation": 2},
                    {"phase": "Ready", "platformHash": "old"},
                )
            )
            status = _last_status(h)
            assert status["phase"] == "Error"
            assert "catalogUpload" in status["message"]
            assert "Field required" in status["message"]

    def test_on_delete_with_no_owned_session_objects_sweeps_only(self):
        """Absence of every owned record proves nothing was deployed: the
        status is not read, no purge runs, and the ConfigMap sweep still runs."""
        with _ReconcilerHarness(expected_count=7) as harness:
            teardown, nodalpath_mode = _run_on_delete(harness, pods=[], configmaps={})

        teardown.assert_called_once_with("nodalarc", ())
        nodalpath_mode.assert_called_once_with("nodalarc", "console")

    def test_current_error_generation_is_terminal_until_user_changes_spec(self):
        with _ReconcilerHarness(expected_count=7):
            with patch(
                "nodalarc_operator.handlers._reconcile_session", new_callable=AsyncMock
            ) as mock_reconcile:
                _run(
                    handlers_mod.on_update(
                        _SPEC,
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
                        _SPEC,
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
            h.pods = [h.pod(f"p{i}") for i in range(6)]
            h.mock("ensure_cm").return_value = {"session_id": "t", "pod_inventory": {}}
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
            h.pods = []
            h.mock("ensure_cm").side_effect = RuntimeError("Template rendering failed")
            _run(_reconcile(h, phase="Creating"))
            status = _last_status(h)
            assert status["phase"] == "Error"

    def test_retryable_dependency_sets_pending_phase(self):
        with _ReconcilerHarness(expected_count=7) as h:
            h.pods = []
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
                    _SPEC,
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
            h.pods = [h.pod(f"p{i}", running=1 if i < 6 else 0) for i in range(7)]
            _run(
                handlers_mod.wiring_check(
                    _SPEC,
                    "current-session",
                    "nodalarc",
                    {"name": "current-session", "uid": "test-uid", "generation": 1},
                    {"phase": "Ready", "podCount": 7},
                )
            )
            mock_reconcile.assert_awaited_once()
            h.mock("platform_ready").assert_not_called()

    @pytest.mark.parametrize(
        "degrade",
        ("replaceable_selection", "replaceable_owner_label", "foreign", "terminating", "surplus"),
    )
    def test_ready_timer_judges_pods_with_the_reconciler_classification(self, degrade):
        """A pod the reconciler would replace, refuse or wait on also ends a Ready claim."""
        with (
            _ReconcilerHarness(expected_count=7) as h,
            patch(
                "nodalarc_operator.handlers._reconcile_session", new_callable=AsyncMock
            ) as mock_reconcile,
        ):
            extra = {
                "replaceable_selection": [h.pod("p0", selection="profiles@sha256:" + "0" * 64)],
                "replaceable_owner_label": [h.pod("p0", owner_uid_label=None)],
                "foreign": [h.pod("p0"), h.pod("p0", name="stray", owner_uid="other-uid")],
                "terminating": [h.pod("p0"), h.pod("gone", terminating=True)],
                "surplus": [h.pod("p0"), h.pod("p9")],
            }[degrade]
            h.pods = [*extra, *(h.pod(f"p{i}") for i in range(1, 7))]
            _run(
                handlers_mod.wiring_check(
                    _SPEC,
                    "current-session",
                    "nodalarc",
                    dict(_META),
                    {"phase": "Ready", "podCount": 7},
                )
            )
            mock_reconcile.assert_awaited_once()
            h.mock("platform_ready").assert_not_called()
            h.mock_v1.delete_namespaced_pod.assert_not_called()
            h.mock_v1.list_namespaced_pod.assert_called_once()

    @pytest.mark.parametrize("degrade", ("foreign", "replaceable_selection"))
    def test_invalid_membership_reconciles_even_when_the_wiring_read_fails(self, degrade):
        """Known-invalid pods end the Ready claim before any wiring-proof query."""
        with (
            _ReconcilerHarness(expected_count=7) as h,
            patch(
                "nodalarc_operator.handlers._reconcile_session", new_callable=AsyncMock
            ) as mock_reconcile,
        ):
            first = {
                "foreign": [h.pod("p0"), h.pod("p0", name="stray", owner_uid="other-uid")],
                "replaceable_selection": [h.pod("p0", selection="profiles@sha256:" + "0" * 64)],
            }[degrade]
            h.pods = [*first, *(h.pod(f"p{i}") for i in range(1, 7))]
            h.mock("check_wiring").side_effect = kubernetes.client.rest.ApiException(
                status=503, reason="Service Unavailable"
            )
            _run(
                handlers_mod.wiring_check(
                    _SPEC,
                    "current-session",
                    "nodalarc",
                    dict(_META),
                    {"phase": "Ready", "podCount": 7},
                )
            )
            mock_reconcile.assert_awaited_once()
            h.mock("check_wiring").assert_not_called()

    def test_wiring_read_failure_with_current_pods_keeps_existing_handling(self):
        with (
            _ReconcilerHarness(expected_count=7) as h,
            patch(
                "nodalarc_operator.handlers._reconcile_session", new_callable=AsyncMock
            ) as mock_reconcile,
        ):
            h.mock("check_wiring").side_effect = kubernetes.client.rest.ApiException(status=503)
            _run(
                handlers_mod.wiring_check(
                    _SPEC,
                    "current-session",
                    "nodalarc",
                    dict(_META),
                    {"phase": "Ready", "podCount": 7},
                )
            )
            h.mock("check_wiring").assert_called_once()
            mock_reconcile.assert_not_awaited()
            h.mock_custom.patch_namespaced_custom_object_status.assert_not_called()

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
                    _SPEC,
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
            spec = _SPEC
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
        spec = _SPEC
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
                **handlers_mod._runtime_proof_status(
                    active_session,
                    deployment_context,
                ).to_patch(),
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
        spec = _SPEC
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
                **handlers_mod._runtime_proof_status(
                    active_session,
                    deployment_context,
                ).to_patch(),
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
        spec = _SPEC
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
                **handlers_mod._runtime_proof_status(
                    active_session,
                    deployment_context,
                ).to_patch(),
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


_OWNER_UID = "test-uid"


def _owner_references(uid: str) -> list[SimpleNamespace]:
    return [SimpleNamespace(uid=uid, name="current-session")]


def _session_pod(
    run_id: str | None,
    *,
    owner_uid: str = _OWNER_UID,
    terminating: bool = False,
) -> SimpleNamespace:
    labels = {"nodalarc.io/node-id": f"node-{run_id or 'unlabelled'}"}
    if run_id is not None:
        labels["nodalarc.io/session-run-id"] = run_id
    return SimpleNamespace(
        metadata=SimpleNamespace(
            name=f"pod-{run_id or 'unlabelled'}",
            labels=labels,
            owner_references=_owner_references(owner_uid),
            deletion_timestamp="2026-09-13T00:00:00Z" if terminating else None,
        )
    )


def _session_configmap(
    name: str,
    data: dict[str, str],
    *,
    owner_uid: str = _OWNER_UID,
) -> SimpleNamespace:
    return SimpleNamespace(
        metadata=SimpleNamespace(name=name, owner_references=_owner_references(owner_uid)),
        data=data,
    )


def _run_on_delete(
    harness: _ReconcilerHarness,
    *,
    pods: list[SimpleNamespace],
    configmaps: dict[str, SimpleNamespace],
) -> tuple[MagicMock, MagicMock]:
    """Run on_delete against fake owned resources; the CR status is deliberately
    one the strict model refuses, proving the handler never reads it."""
    harness.pods = list(pods)

    def _read(name: str, namespace: str) -> SimpleNamespace:
        if name in configmaps:
            return configmaps[name]
        raise kubernetes.client.rest.ApiException(status=404)

    harness.mock_v1.read_namespaced_config_map.side_effect = _read
    with (
        patch("nodalarc_operator.handlers.teardown_session") as teardown,
        patch("nodalarc_operator.handlers.set_nodalpath_mode") as nodalpath_mode,
    ):
        _run(
            handlers_mod.on_delete(
                "current-session",
                "nodalarc",
                spec=_SPEC,
                meta={"name": "current-session", "uid": _OWNER_UID, "generation": 2},
                status={"phase": "not-a-phase"},
            )
        )
    return teardown, nodalpath_mode


def test_on_delete_purges_the_one_owned_run_id_then_sweeps() -> None:
    with _ReconcilerHarness(expected_count=7) as harness:
        teardown, _ = _run_on_delete(harness, pods=[_session_pod("run-a")], configmaps={})

    teardown.assert_called_once_with("nodalarc", ("run-a",))


def test_on_delete_purges_every_distinct_owned_run_id() -> None:
    with _ReconcilerHarness(expected_count=7) as harness:
        teardown, _ = _run_on_delete(
            harness,
            pods=[_session_pod("run-a"), _session_pod("run-b"), _session_pod("run-a")],
            configmaps={},
        )

    teardown.assert_called_once_with("nodalarc", ("run-a", "run-b"))


def test_on_delete_reads_a_superseded_run_id_from_a_terminating_pod() -> None:
    """After a generation change the old run id survives only on a terminating
    owned pod while the ConfigMaps name the new run: both are purged."""
    with _ReconcilerHarness(expected_count=7) as harness:
        teardown, _ = _run_on_delete(
            harness,
            pods=[_session_pod("run-a", terminating=True)],
            configmaps={
                "nodalarc-session": _session_configmap(
                    "nodalarc-session", {"session_run_id": "run-b"}
                ),
                WIRING_MANIFEST_CONFIGMAP: _session_configmap(
                    WIRING_MANIFEST_CONFIGMAP, {"session_id": "run-b"}
                ),
            },
        )

    teardown.assert_called_once_with("nodalarc", ("run-a", "run-b"))


def test_on_delete_refuses_a_session_pod_with_a_foreign_owner() -> None:
    with _ReconcilerHarness(expected_count=7) as harness:
        with pytest.raises(ValueError, match="not owned by ConstellationSpec"):
            _run_on_delete(
                harness, pods=[_session_pod("run-a", owner_uid="other-uid")], configmaps={}
            )
        harness.mock_v1.delete_namespaced_config_map.assert_not_called()


def _run_on_delete_with_real_teardown(
    harness: _ReconcilerHarness,
    *,
    delete_side_effect,
) -> tuple[MagicMock, MagicMock]:
    """Run on_delete with the real teardown_session against the fake API: one
    owned pod naming run-a, no run-id ConfigMaps, no FRR ConfigMaps."""
    harness.pods = [_session_pod("run-a")]
    harness.mock_v1.read_namespaced_config_map.side_effect = kubernetes.client.rest.ApiException(
        status=404
    )
    harness.mock_v1.list_namespaced_config_map.return_value = SimpleNamespace(items=[])
    harness.mock_v1.delete_namespaced_config_map.side_effect = delete_side_effect
    with (
        patch("nodalarc_operator.session_deployer.purge_session_runtime_state") as purge,
        patch("nodalarc_operator.handlers.set_nodalpath_mode") as nodalpath_mode,
    ):
        _run(
            handlers_mod.on_delete(
                "current-session",
                "nodalarc",
                spec=_SPEC,
                meta={"name": "current-session", "uid": _OWNER_UID, "generation": 2},
                status=None,
            )
        )
    return purge, nodalpath_mode


def test_on_delete_propagates_a_failed_configmap_delete() -> None:
    """A non-404 delete failure in the sweep must reach kopf so the finalizer
    stays; cleanup is never reported complete over a ConfigMap that remains."""

    def _delete(name: str, namespace: str) -> None:
        if name == "nodalarc-constellation":
            raise kubernetes.client.rest.ApiException(status=500)

    with _ReconcilerHarness(expected_count=7) as harness:
        with pytest.raises(kubernetes.client.rest.ApiException):
            _run_on_delete_with_real_teardown(harness, delete_side_effect=_delete)
        harness.mock_v1.delete_namespaced_config_map.assert_any_call(
            "nodalarc-constellation", "nodalarc"
        )


def test_on_delete_tolerates_absent_configmaps_in_the_sweep() -> None:
    def _absent(name: str, namespace: str) -> None:
        raise kubernetes.client.rest.ApiException(status=404)

    with _ReconcilerHarness(expected_count=7) as harness:
        purge, nodalpath_mode = _run_on_delete_with_real_teardown(
            harness, delete_side_effect=_absent
        )

    purge.assert_called_once_with("nodalarc", "run-a")
    nodalpath_mode.assert_called_once_with("nodalarc", "console")


def test_on_delete_refuses_an_owned_record_without_its_run_id() -> None:
    with _ReconcilerHarness(expected_count=7) as harness:
        with pytest.raises(ValueError, match="carries no"):
            _run_on_delete(
                harness,
                pods=[],
                configmaps={"nodalarc-session": _session_configmap("nodalarc-session", {})},
            )
        harness.mock_v1.delete_namespaced_config_map.assert_not_called()


class TestWiringManifestCurrency:
    """The reconciler rewrites any payload it cannot read as the current manifest."""

    RUN_ID = "run-currency-0001"
    PLATFORM_HASH = "platform-hash"

    def _data(self, payload: str | None, **overrides) -> dict[str, str]:
        from nodalarc.nats_channels import sanitize_session_id

        data = {
            "session_id": sanitize_session_id(self.RUN_ID),
            "platform_hash": self.PLATFORM_HASH,
            "node_count": "2",
            "wiring_generation": "sha256:" + "a" * 64,
            **overrides,
        }
        if payload is not None:
            data[WIRING_MANIFEST_PAYLOAD_KEY] = payload
        return data

    def _current(self, data: dict[str, str]) -> bool:
        v1 = create_autospec(kubernetes.client.CoreV1Api, instance=True)
        v1.read_namespaced_config_map.return_value = SimpleNamespace(data=data)
        with patch("nodalarc_operator.session_deployer._get_v1", return_value=v1):
            current = handlers_mod._wiring_manifest_matches_spec(
                "nodalarc", 2, self.RUN_ID, self.PLATFORM_HASH
            )
        v1.read_namespaced_config_map.assert_called_once_with(WIRING_MANIFEST_CONFIGMAP, "nodalarc")
        return current

    def _manifest_payload(self, **overrides) -> str:
        from nodalarc.nats_channels import sanitize_session_id

        return encode_wiring_manifest_payload(
            {
                "session_id": sanitize_session_id(self.RUN_ID),
                "wiring_generation": "sha256:" + "a" * 64,
                "nodes": {"a": {}, "b": {}},
                **overrides,
            }
        )

    def test_a_current_manifest_is_kept_without_full_model_validation(self):
        # The payload's node specs would fail WiringManifest validation; the
        # currency check compares identity fields only.
        assert self._current(self._data(self._manifest_payload())) is True

    def test_a_missing_payload_is_rewritten(self, caplog):
        with caplog.at_level("INFO", logger="nodalarc_operator.handlers"):
            assert self._current(self._data(None)) is False
        assert "wiring manifest payload missing" in caplog.text

    @pytest.mark.parametrize(
        ("raw", "stage"),
        [
            (b"{not json", "json"),
            (b'{"n": ' + b"9" * 4301 + b"}", "json"),
            (b"[1, 2]", "shape"),
            (b"\xef\xbb\xbf{}", "json"),
            ("{}".encode("utf-16"), "utf-8"),
        ],
        ids=["json-syntax", "json-digit-limit", "not-an-object", "utf-8-bom", "utf-16"],
    )
    def test_an_unreadable_payload_is_rewritten(self, caplog, raw, stage):
        import base64
        import gzip

        payload = base64.b64encode(gzip.compress(raw)).decode()
        with caplog.at_level("WARNING", logger="nodalarc_operator.handlers"):
            assert self._current(self._data(payload)) is False
        assert f"wiring manifest payload refused at {stage}" in caplog.text
