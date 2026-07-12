from __future__ import annotations

import asyncio
from collections.abc import Callable
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import pytest
import yaml
from nodalarc.catalog_repository import CatalogScope
from nodalarc.filesystem_catalog_repository import FilesystemCatalogRepository
from nodalarc.models.builder_api import BuilderDraftEnvelope, BuilderSessionSaveRequest
from vs_api.builder_session_service import save_builder_session
from vs_api.catalog_context import CatalogContext
from vs_api.catalog_upload_store import (
    CatalogUploadResourceEvidence,
    CatalogUploadStoreReceipt,
)
from vs_api.session_deployment import (
    persist_catalog_session_upload,
    prepare_catalog_session_deployment,
)
from vs_api.session_manager import SessionManager

from tests.builder_world_fixtures import builder_world_preview

ROOT = Path(__file__).resolve().parents[2]
SHIPPED_ROOT = ROOT / "catalog" / "nodalarc"
NAMESPACE = "nodalarc"


class _ApiError(RuntimeError):
    def __init__(self, status: int) -> None:
        super().__init__(f"Kubernetes API status {status}")
        self.status = status


class _CustomObjectsApi:
    def __init__(
        self,
        *,
        fail_create: bool = False,
        persist_then_fail_create: bool = False,
    ) -> None:
        self.fail_create = fail_create
        self.persist_then_fail_create = persist_then_fail_create
        self.delete_calls = 0
        self.created_body: dict[str, Any] | None = None

    def delete_namespaced_custom_object(self, **_kwargs: Any) -> dict[str, Any]:
        self.delete_calls += 1
        return {}

    def get_namespaced_custom_object(self, **_kwargs: Any) -> dict[str, Any]:
        if self.created_body is None:
            raise _ApiError(404)
        return {
            "apiVersion": self.created_body["apiVersion"],
            "kind": self.created_body["kind"],
            "metadata": {
                "name": "current-session",
                "namespace": NAMESPACE,
                "uid": "uid-current-session",
                "generation": 1,
            },
            "spec": self.created_body["spec"],
            "status": {
                "phase": "Ready",
                "message": "ready",
                "observedGeneration": 1,
            },
        }

    def create_namespaced_custom_object(self, **kwargs: Any) -> dict[str, Any]:
        if self.fail_create:
            raise RuntimeError("create failed")
        self.created_body = kwargs["body"]
        if self.persist_then_fail_create:
            raise RuntimeError("create response lost")
        return {}


class _CoreV1Api:
    def __init__(self) -> None:
        self.pod_list_calls = 0

    def list_namespaced_pod(self, *_args: Any, **_kwargs: Any) -> SimpleNamespace:
        self.pod_list_calls += 1
        return SimpleNamespace(items=[])


class _UploadStore:
    def __init__(
        self,
        *,
        fail_put: bool = False,
        on_put: Callable[[], None] | None = None,
    ) -> None:
        self.fail_put = fail_put
        self.on_put = on_put
        self.put_calls = 0
        self.delete_calls: list[Any] = []

    def put(self, upload, *, resource_observer=None) -> CatalogUploadStoreReceipt:
        self.put_calls += 1
        if self.fail_put:
            raise RuntimeError("upload failed")
        if self.on_put is not None:
            self.on_put()
        resources = tuple(
            CatalogUploadResourceEvidence(
                name=f"catalog-upload-{index}",
                ref=entry.ref,
                uid=f"uid-{index}",
            )
            for index, entry in enumerate(upload.catalog_files)
        )
        if resource_observer is not None:
            for resource in resources:
                resource_observer(resource)
        return CatalogUploadStoreReceipt(
            selection=upload.selection,
            resources=resources,
        )

    def delete(self, upload) -> None:
        self.delete_calls.append(upload)


def _context(tmp_path: Path) -> CatalogContext:
    scope = CatalogScope()
    return CatalogContext(
        repository=FilesystemCatalogRepository(
            shipped_root=SHIPPED_ROOT,
            scope_roots={scope: tmp_path / "user-catalog"},
        ),
        scope=scope,
    )


def _save_user_session(context: CatalogContext):
    raw = yaml.safe_load((SHIPPED_ROOT / "sessions/earth-leo-simple.yaml").read_bytes())
    raw = deepcopy(raw)
    raw["session"]["name"] = "switch-user-session"
    return save_builder_session(
        BuilderSessionSaveRequest(
            draft=BuilderDraftEnvelope(draft_revision=1, state={"session": raw}),
            target_ref="user:sessions/switch-user-session.yaml",
        ),
        context,
        available_node_count=1_000_000,
        preview_factory=lambda *_: builder_world_preview(),
    )


def _prepared(context: CatalogContext, saved):
    return prepare_catalog_session_deployment(
        context,
        session_ref=str(saved.session.ref),
        expected_session_revision=saved.session.revision,
        expected_document_digest=saved.digests.document,
        expected_closure_digest=saved.digests.dependency,
        available_node_count=1_000_000,
    )


def _manager(tmp_path: Path) -> SessionManager:
    return SessionManager()


async def _no_sleep(_seconds: float) -> None:
    return None


def _run_in_executor_inline(loop, _executor, operation, *args):
    future = loop.create_future()
    try:
        future.set_result(operation(*args))
    except BaseException as error:
        future.set_exception(error)
    return future


def _switch(
    manager,
    deployment,
    context,
    store,
    api,
    core,
    *,
    transition_started=None,
    upload_resource_observed=None,
    constellation_spec_observed=None,
):
    with patch.object(asyncio.BaseEventLoop, "run_in_executor", _run_in_executor_inline):
        return asyncio.run(
            manager.switch_catalog(
                deployment,
                context=context,
                upload_store=store,
                custom_objects_api=api,
                core_v1_api=core,
                namespace=NAMESPACE,
                transition_started=transition_started,
                upload_resource_observed=upload_resource_observed,
                constellation_spec_observed=constellation_spec_observed,
            )
        )


def test_catalog_switch_uploads_exact_closure_and_selects_small_cr_reference(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("vs_api.session_manager.asyncio.sleep", _no_sleep)
    context = _context(tmp_path)
    saved = _save_user_session(context)
    deployment = _prepared(context, saved)
    manager = _manager(tmp_path)
    store = _UploadStore()
    api = _CustomObjectsApi()
    core = _CoreV1Api()

    ready = _switch(manager, deployment, context, store, api, core)

    assert ready["status"]["phase"] == "Ready"
    assert store.put_calls == 1
    assert store.delete_calls == []
    assert api.delete_calls == 1
    assert api.created_body is not None
    assert api.created_body["spec"]["sessionYaml"].encode() == deployment.prepared.root_yaml
    assert api.created_body["spec"]["catalogUpload"] == (
        deployment.upload.selection.model_dump(mode="json")
    )
    assert api.created_body["metadata"]["annotations"]["nodalarc.io/source-id"] == str(
        saved.session.ref
    )
    assert manager.active_source_id == str(saved.session.ref)


def test_catalog_switch_reports_incremental_upload_and_runtime_observations(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("vs_api.session_manager.asyncio.sleep", _no_sleep)
    context = _context(tmp_path)
    saved = _save_user_session(context)
    deployment = _prepared(context, saved)
    manager = _manager(tmp_path)
    store = _UploadStore()
    api = _CustomObjectsApi()
    core = _CoreV1Api()
    upload_observations: list[CatalogUploadResourceEvidence] = []
    runtime_observations: list[dict[str, Any]] = []

    async def observe_runtime(cr: dict[str, Any]) -> None:
        runtime_observations.append(cr)

    _switch(
        manager,
        deployment,
        context,
        store,
        api,
        core,
        upload_resource_observed=upload_observations.append,
        constellation_spec_observed=observe_runtime,
    )

    assert [item.name for item in upload_observations] == [
        f"catalog-upload-{index}" for index, _ in enumerate(deployment.upload.catalog_files)
    ]
    assert [item.ref for item in upload_observations] == [
        entry.ref for entry in deployment.upload.catalog_files
    ]
    assert len(runtime_observations) == 2
    assert all(item["metadata"]["uid"] == "uid-current-session" for item in runtime_observations)


def test_catalog_switch_final_staleness_refusal_preserves_old_cr_and_cleans_created_upload(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("vs_api.session_manager.asyncio.sleep", _no_sleep)
    context = _context(tmp_path)
    saved = _save_user_session(context)
    deployment = _prepared(context, saved)
    manager = _manager(tmp_path)
    api = _CustomObjectsApi()
    core = _CoreV1Api()

    def _change_reviewed_session() -> None:
        raw = yaml.safe_load(saved.session.canonical_yaml)
        raw["session"]["description"] = "Changed while the upload was being persisted"
        save_builder_session(
            BuilderSessionSaveRequest(
                draft=BuilderDraftEnvelope(draft_revision=2, state={"session": raw}),
                target_ref=saved.session.ref,
                expected_session_revision=saved.session.revision,
            ),
            context,
            available_node_count=1_000_000,
            preview_factory=lambda *_: builder_world_preview(),
        )

    store = _UploadStore(on_put=_change_reviewed_session)
    transition_calls = 0

    async def _transition_started() -> None:
        nonlocal transition_calls
        transition_calls += 1

    with pytest.raises(ValueError, match="changed after preparation"):
        _switch(
            manager,
            deployment,
            context,
            store,
            api,
            core,
            transition_started=_transition_started,
        )

    assert store.put_calls == 1
    assert transition_calls == 0
    assert api.delete_calls == 0
    assert api.created_body is None
    assert core.pod_list_calls == 0
    assert len(store.delete_calls) == 1
    assert store.delete_calls[0] == deployment.upload.selection
    assert manager.status == "error"


def test_catalog_switch_rechecks_freshness_immediately_before_cr_deletion(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("vs_api.session_manager.asyncio.sleep", _no_sleep)
    context = _context(tmp_path)
    saved = _save_user_session(context)
    deployment = _prepared(context, saved)
    manager = _manager(tmp_path)
    store = _UploadStore()
    api = _CustomObjectsApi()
    core = _CoreV1Api()

    async def _change_after_initial_verification() -> None:
        raw = yaml.safe_load(saved.session.canonical_yaml)
        raw["session"]["description"] = "Changed immediately before teardown"
        save_builder_session(
            BuilderSessionSaveRequest(
                draft=BuilderDraftEnvelope(draft_revision=2, state={"session": raw}),
                target_ref=saved.session.ref,
                expected_session_revision=saved.session.revision,
            ),
            context,
            available_node_count=1_000_000,
            preview_factory=lambda *_: builder_world_preview(),
        )

    with pytest.raises(ValueError, match="changed after preparation"):
        _switch(
            manager,
            deployment,
            context,
            store,
            api,
            core,
            transition_started=_change_after_initial_verification,
        )

    assert api.delete_calls == 0
    assert api.created_body is None
    assert store.delete_calls == [deployment.upload.selection]


def test_catalog_switch_upload_failure_preserves_old_cr(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("vs_api.session_manager.asyncio.sleep", _no_sleep)
    context = _context(tmp_path)
    saved = _save_user_session(context)
    deployment = _prepared(context, saved)
    manager = _manager(tmp_path)
    store = _UploadStore(fail_put=True)
    api = _CustomObjectsApi()
    core = _CoreV1Api()

    with pytest.raises(RuntimeError, match="upload failed"):
        _switch(manager, deployment, context, store, api, core)

    assert store.put_calls == 1
    assert store.delete_calls == []
    assert api.delete_calls == 0
    assert api.created_body is None
    assert core.pod_list_calls == 0
    assert manager.status == "error"


def test_catalog_switch_rejects_uploads_persisted_outside_its_transaction(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("vs_api.session_manager.asyncio.sleep", _no_sleep)
    context = _context(tmp_path)
    saved = _save_user_session(context)
    store = _UploadStore()
    persisted = persist_catalog_session_upload(_prepared(context, saved), store)  # type: ignore[arg-type]
    manager = _manager(tmp_path)
    api = _CustomObjectsApi()
    core = _CoreV1Api()

    with pytest.raises(ValueError, match="unpersisted"):
        _switch(manager, persisted, context, store, api, core)

    assert store.put_calls == 1
    assert store.delete_calls == []
    assert api.delete_calls == 0
    assert core.pod_list_calls == 0


def test_catalog_switch_create_failure_cleans_only_unselected_upload(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("vs_api.session_manager.asyncio.sleep", _no_sleep)
    context = _context(tmp_path)
    saved = _save_user_session(context)
    deployment = _prepared(context, saved)
    manager = _manager(tmp_path)
    store = _UploadStore()
    api = _CustomObjectsApi(fail_create=True)
    core = _CoreV1Api()

    with pytest.raises(RuntimeError, match="create failed"):
        _switch(manager, deployment, context, store, api, core)

    assert api.delete_calls == 1
    assert len(store.delete_calls) == 1
    assert store.delete_calls[0] == deployment.upload.selection


def test_catalog_switch_does_not_delete_upload_when_create_result_is_ambiguous_but_selected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("vs_api.session_manager.asyncio.sleep", _no_sleep)
    context = _context(tmp_path)
    saved = _save_user_session(context)
    deployment = _prepared(context, saved)
    manager = _manager(tmp_path)
    store = _UploadStore()
    api = _CustomObjectsApi(persist_then_fail_create=True)
    core = _CoreV1Api()

    ready = _switch(manager, deployment, context, store, api, core)

    assert ready["status"]["phase"] == "Ready"
    assert api.created_body is not None
    assert store.delete_calls == []
