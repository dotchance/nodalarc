"""Tests for fail-loud prepared catalog session switching."""

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml
from vs_api.session_manager import SessionManager

CATALOG_SESSION = Path("catalog/nodalarc/sessions/earth-leo-heo-geo-luna-reachability.yaml")


def _segment_session_yaml(name: str, data_dir: Path) -> str:
    raw = yaml.safe_load(CATALOG_SESSION.read_text(encoding="utf-8"))
    raw["session"]["name"] = name
    return yaml.dump(raw, sort_keys=False)


@pytest.fixture
def tmp_sessions(tmp_path, monkeypatch):
    """Create a temporary sessions directory with a valid session config."""
    sessions_dir = tmp_path / "sessions"
    sessions_dir.mkdir()

    data_dir = tmp_path / "data"
    data_dir.mkdir()

    # Write a minimal valid session YAML
    session_yaml = sessions_dir / "test-session.yaml"
    session_yaml.write_text(_segment_session_yaml("test-session", data_dir))

    return {
        "sessions_dir": sessions_dir,
        "data_dir": data_dir,
        "session_yaml": session_yaml,
    }


class _SwitchApi:
    def __init__(
        self,
        *,
        old_cr_gets_before_404: int | None = 0,
        post_create_statuses: list[dict] | None = None,
    ) -> None:
        self.old_cr_gets_before_404 = old_cr_gets_before_404
        self.old_cr_get_count = 0
        self.post_create_get_count = 0
        self.post_create_statuses = post_create_statuses or [
            {
                "metadata": {"generation": 1},
                "status": {
                    "phase": "Ready",
                    "message": "ready",
                    "observedGeneration": 1,
                },
            }
        ]
        self.created = False
        self.created_body: dict | None = None

    def delete_namespaced_custom_object(self, **_kwargs):
        return {}

    def get_namespaced_custom_object(self, **_kwargs):
        from kubernetes.client.rest import ApiException

        if not self.created:
            self.old_cr_get_count += 1
            if self.old_cr_gets_before_404 is None:
                return {"metadata": {"name": "current-session"}}
            if self.old_cr_get_count <= self.old_cr_gets_before_404:
                return {"metadata": {"name": "current-session"}}
            raise ApiException(status=404, reason="Not Found")

        idx = min(self.post_create_get_count, len(self.post_create_statuses) - 1)
        self.post_create_get_count += 1
        observed = dict(self.post_create_statuses[idx])
        observed["apiVersion"] = self.created_body["apiVersion"]
        observed["kind"] = self.created_body["kind"]
        observed["spec"] = self.created_body["spec"]
        observed["metadata"] = {
            **observed.get("metadata", {}),
            "name": "current-session",
            "uid": "uid-current-session",
        }
        return observed

    def create_namespaced_custom_object(self, **kwargs):
        self.created = True
        self.created_body = kwargs["body"]
        return {}


class _SwitchCoreV1:
    def __init__(self, pod_counts: list[int] | None = None) -> None:
        self.pod_counts = pod_counts or [0]
        self.calls = 0

    def list_namespaced_pod(self, *_args, **_kwargs):
        idx = min(self.calls, len(self.pod_counts) - 1)
        self.calls += 1
        return SimpleNamespace(items=[object() for _ in range(self.pod_counts[idx])])


async def _no_sleep(_seconds: float) -> None:
    return None


def _run_in_executor_inline(loop, _executor, operation, *args):
    future = loop.create_future()
    try:
        future.set_result(operation(*args))
    except BaseException as error:
        future.set_exception(error)
    return future


def _patch_switch_waits(monkeypatch) -> None:
    monkeypatch.setattr("vs_api.session_manager.asyncio.sleep", _no_sleep)
    monkeypatch.setattr(asyncio.BaseEventLoop, "run_in_executor", _run_in_executor_inline)


def _switch_body() -> dict:
    return {
        "apiVersion": "nodalarc.io/v1alpha1",
        "kind": "ConstellationSpec",
        "metadata": {"name": "current-session", "namespace": "nodalarc"},
        "spec": {
            "sessionYaml": "session:\n  name: switch-test\n",
            "catalogUpload": {
                "upload_id": "catalog-switch-test",
                "closure_digest": f"sha256:{'0' * 64}",
                "file_count": 1,
            },
            "recordHistory": False,
        },
    }


async def _switch_progress(manager: SessionManager, detail: str) -> None:
    manager.status_detail = detail


class TestSwitchFailLoud:
    def test_switch_fails_if_old_cr_does_not_finalize(self, tmp_sessions, monkeypatch):
        api = _SwitchApi(old_cr_gets_before_404=None)
        core = _SwitchCoreV1([0])
        _patch_switch_waits(monkeypatch)
        mgr = SessionManager()
        source_id = "nodalarc:sessions/test-session.yaml"

        with pytest.raises(TimeoutError, match="Old ConstellationSpec did not finalize"):
            asyncio.run(
                mgr._switch_constellation_spec(
                    source_id=source_id,
                    cr_body=_switch_body(),
                    custom_objects_api=api,
                    core_v1_api=core,
                    namespace="nodalarc",
                    progress=lambda detail: _switch_progress(mgr, detail),
                )
            )

        assert api.created is False

    def test_switch_fails_if_old_session_pods_remain(self, tmp_sessions, monkeypatch):
        api = _SwitchApi(old_cr_gets_before_404=0)
        core = _SwitchCoreV1([2])
        _patch_switch_waits(monkeypatch)
        mgr = SessionManager()
        source_id = "nodalarc:sessions/test-session.yaml"

        with pytest.raises(TimeoutError, match="old session pod"):
            asyncio.run(
                mgr._switch_constellation_spec(
                    source_id=source_id,
                    cr_body=_switch_body(),
                    custom_objects_api=api,
                    core_v1_api=core,
                    namespace="nodalarc",
                    progress=lambda detail: _switch_progress(mgr, detail),
                )
            )

        assert api.created is False

    def test_switch_ignores_stale_error_until_operator_observes_generation(
        self, tmp_sessions, monkeypatch
    ):
        api = _SwitchApi(
            old_cr_gets_before_404=0,
            post_create_statuses=[
                {
                    "metadata": {"generation": 2},
                    "status": {
                        "phase": "Error",
                        "message": "old wiring failure",
                        "observedGeneration": 1,
                    },
                },
                {
                    "metadata": {"generation": 2},
                    "status": {
                        "phase": "Ready",
                        "message": "ready",
                        "observedGeneration": 2,
                    },
                },
            ],
        )
        core = _SwitchCoreV1([0])
        _patch_switch_waits(monkeypatch)
        mgr = SessionManager()
        source_id = "nodalarc:sessions/test-session.yaml"

        asyncio.run(
            mgr._switch_constellation_spec(
                source_id=source_id,
                cr_body=_switch_body(),
                custom_objects_api=api,
                core_v1_api=core,
                namespace="nodalarc",
                progress=lambda detail: _switch_progress(mgr, detail),
            )
        )

        assert mgr.status_detail == "ready"
        assert mgr.active_source_id == source_id
