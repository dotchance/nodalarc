from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest
from kubernetes.client.rest import ApiException
from nodalarc.catalog_closure import CatalogClosureEntry
from nodalarc.catalog_refs import CatalogRef
from nodalarc.catalog_upload import CatalogUpload, CatalogUploadSelection
from nodalarc.content_identity import sha256_digest
from nodalarc.kubernetes_runtime_config import (
    KubernetesRuntimeConfigError,
    KubernetesRuntimeConfigErrorCode,
    encode_catalog_upload_config_map,
    read_catalog_upload,
)
from nodalarc.runtime_config import (
    RuntimeConfigError,
    RuntimeConfigErrorCode,
    _assert_shipped_assets,
)
from vs_api.catalog_upload_store import (
    CatalogUploadStoreError,
    CatalogUploadStoreErrorCode,
    CatalogUploadStoreErrorEvidence,
)
from vs_api.transition_operations import (
    InMemoryTransitionOperationStore,
    TransitionOperationFacts,
    TransitionOperationReservation,
    TransitionOperationSource,
    TransitionOperationSourceKind,
    TransitionOperationState,
)

ROOT_YAML = "session:\n  name: transition-admission-test\n"
DIGEST_A = sha256_digest(ROOT_YAML.encode())
DIGEST_B = f"sha256:{'b' * 64}"
DIGEST_C = f"sha256:{'c' * 64}"


def _catalog_selection(upload_id: str) -> CatalogUploadSelection:
    return CatalogUploadSelection(
        upload_id=upload_id,
        closure_digest=DIGEST_B,
        file_count=2,
    )


def _install_store(monkeypatch, main):
    store = InMemoryTransitionOperationStore()
    monkeypatch.setattr(main, "_transition_operation_store", store)
    monkeypatch.setattr(main, "_active_transition_operation_id", None)
    monkeypatch.setattr(main, "_local_transition_operation_id", None)
    return store


async def _wait_for_operation(operation_id: str) -> None:
    tasks = [
        task
        for task in asyncio.all_tasks()
        if task.get_name() == f"session-transition-{operation_id}"
    ]
    if tasks:
        await tasks[0]


async def _admit(main, worker):
    return await main._admit_transition(
        worker,
        reservation=TransitionOperationReservation(
            source=TransitionOperationSource(
                kind=TransitionOperationSourceKind.CATALOG_SESSION,
                logical_id="user:sessions/test.yaml",
            ),
            facts=TransitionOperationFacts(release="test", build="test"),
        ),
    )


def test_prepared_transition_reservation_keeps_reviewed_facts_and_upload_id_only(
    monkeypatch,
) -> None:
    import vs_api.main as main

    monkeypatch.setenv("NODALARC_RELEASE", "release-override")
    monkeypatch.setenv("NODAL_BUILD", "build-override")
    deployment = SimpleNamespace(
        prepared=SimpleNamespace(
            source=SimpleNamespace(
                logical_id="user:sessions/demo.yaml",
            ),
            document_digest=DIGEST_A,
            closure_digest=DIGEST_B,
            resolved_semantic_digest=DIGEST_C,
            file_count=3,
            total_bytes=3072,
            source_revision=DIGEST_A,
        ),
        repository_generation=DIGEST_B,
        upload=SimpleNamespace(
            selection=_catalog_selection("catalog-test"),
        ),
    )

    prepared_reservation = main._prepared_transition_reservation(deployment)
    assert prepared_reservation.facts.document_digest == DIGEST_A
    assert prepared_reservation.facts.closure_digest == DIGEST_B
    assert prepared_reservation.facts.file_count == 3
    assert prepared_reservation.facts.release == "release-override"
    assert prepared_reservation.facts.build == "build-override"
    assert prepared_reservation.provenance.upload_id == "catalog-test"
    assert prepared_reservation.provenance.upload_resource_names == ()
    dumped = str(prepared_reservation.model_dump(mode="json"))
    assert "descriptor" not in dumped
    assert "manifest" not in dumped
    assert "scope_binding" not in dumped
    assert prepared_reservation.provenance.runtime_plan is not None
    assert prepared_reservation.provenance.runtime_plan.name == "current-session"


def test_transition_admission_releases_after_scheduling_failure(monkeypatch) -> None:
    import vs_api.main as main

    async def exercise() -> None:
        store = _install_store(monkeypatch, main)
        original_create_task = asyncio.create_task

        def fail_create_task(*_args, **_kwargs):
            raise RuntimeError("scheduler unavailable")

        monkeypatch.setattr(main.asyncio, "create_task", fail_create_task)
        with pytest.raises(RuntimeError, match="scheduler unavailable"):
            await _admit(main, lambda: asyncio.sleep(0))
        monkeypatch.setattr(main.asyncio, "create_task", original_create_task)

        assert main._active_transition_operation_id is None
        assert main._local_transition_operation_id is None
        record = next(iter(store._records.values()))
        assert record.state is TransitionOperationState.FAILED
        assert record.failure is not None
        assert record.failure.code == "transition.scheduling.failed"

        next_id = await _admit(main, lambda: asyncio.sleep(0))
        assert next_id is not None
        await _wait_for_operation(next_id)
        assert store.get_operation(next_id).state is TransitionOperationState.SUCCEEDED

    asyncio.run(exercise())


# --- the restart poll: transport failures keep polling, everything else is terminal ---


def _closure_entry(yaml_bytes: bytes) -> CatalogClosureEntry:
    return CatalogClosureEntry(
        ref=CatalogRef("nodalarc:bodies/earth.yaml"),
        family="bodies",
        preserved_path="bodies/earth.yaml",
        yaml_bytes=yaml_bytes,
        document_digest=sha256_digest(yaml_bytes),
        size_bytes=len(yaml_bytes),
    )


def test_poll_rule_descends_through_the_upload_fetch_wrapper_to_a_transport_root() -> None:
    import vs_api.main as main

    class _RefusingReader:
        def list_namespaced_config_map(self, namespace: str, *, label_selector: str):
            raise ApiException(status=503, reason="unavailable")

    with pytest.raises(KubernetesRuntimeConfigError) as raised:
        read_catalog_upload(
            _RefusingReader(),
            namespace="nodalarc",
            root_yaml=ROOT_YAML.encode(),
            selection=_catalog_selection("catalog-live"),
        )

    assert raised.value.code is KubernetesRuntimeConfigErrorCode.CONFIG_MAP_FETCH_FAILED
    assert main._poll_failure_is_transport(raised.value) is True


def test_poll_rule_treats_an_upload_content_refusal_as_terminal() -> None:
    import vs_api.main as main

    with pytest.raises(KubernetesRuntimeConfigError) as raised:
        encode_catalog_upload_config_map(
            namespace="nodalarc",
            upload_id="catalog-live",
            order=0,
            entry=_closure_entry(b"\xff\xfe"),
        )

    assert raised.value.code is KubernetesRuntimeConfigErrorCode.INVALID_UPLOAD
    assert main._poll_failure_is_transport(raised.value) is False


def test_poll_rule_treats_a_shipped_asset_refusal_over_a_missing_file_as_terminal(
    tmp_path: Path,
) -> None:
    import vs_api.main as main

    upload = CatalogUpload(
        selection=_catalog_selection("catalog-live"),
        root_yaml=ROOT_YAML.encode(),
        catalog_files=(_closure_entry(b"body: {}\n"),),
    )

    with pytest.raises(RuntimeConfigError) as raised:
        _assert_shipped_assets(upload, tmp_path)

    assert raised.value.code is RuntimeConfigErrorCode.SHIPPED_ASSET_MISMATCH
    assert isinstance(raised.value.__cause__, FileNotFoundError)
    assert main._poll_failure_is_transport(raised.value) is False


def test_poll_rule_requires_a_retained_cause_behind_a_transport_wrapper() -> None:
    import vs_api.main as main

    orphan = CatalogUploadStoreError(
        CatalogUploadStoreErrorEvidence(
            code=CatalogUploadStoreErrorCode.LIST_FAILED,
            message="no cause retained",
        )
    )

    assert main._poll_failure_is_transport(orphan) is False
