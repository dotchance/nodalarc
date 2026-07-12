from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from nodalarc.catalog_upload import CatalogUploadSelection, sha256_digest
from nodalarc.models.session_sources import (
    CatalogSessionSourceId,
    CatalogSessionSwitchAccepted,
    CatalogSessionSwitchRequest,
)
from vs_api.session_deployment import (
    SessionDeploymentPreparationError,
    SessionDeploymentPreparationErrorCode,
    SessionDeploymentPreparationErrorEvidence,
)
from vs_api.transition_operations import (
    InMemoryTransitionOperationStore,
    TransitionOperationFacts,
    TransitionOperationProvenance,
    TransitionOperationReservation,
    TransitionOperationSource,
    TransitionOperationSourceKind,
    TransitionOperationState,
    TransitionRuntimePlan,
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


def test_transition_admission_is_atomic_queryable_and_releases_after_success(monkeypatch) -> None:
    import vs_api.main as main

    async def exercise() -> None:
        store = _install_store(monkeypatch, main)
        started = asyncio.Event()
        release = asyncio.Event()

        async def worker() -> None:
            started.set()
            await release.wait()

        first = await _admit(main, worker)
        assert first is not None
        await started.wait()
        assert store.get_operation(first).state is TransitionOperationState.COLLECTING

        second = await _admit(main, worker)
        assert second is None

        release.set()
        await _wait_for_operation(first)
        assert main._active_transition_operation_id is None
        assert store.get_operation(first).state is TransitionOperationState.SUCCEEDED

        public = await main.get_session_transition(first)
        assert public.operation_id == first
        assert public.state is TransitionOperationState.SUCCEEDED
        assert "provenance" not in public.model_dump(mode="json")

    asyncio.run(exercise())


def test_transition_admission_releases_after_worker_failure(monkeypatch) -> None:
    import vs_api.main as main

    async def exercise() -> None:
        store = _install_store(monkeypatch, main)
        failed = asyncio.Event()

        async def worker() -> None:
            failed.set()
            raise RuntimeError("worker failed")

        operation_id = await _admit(main, worker)
        assert operation_id is not None
        await failed.wait()

        await _wait_for_operation(operation_id)
        assert main._active_transition_operation_id is None
        record = store.get_operation(operation_id)
        assert record.state is TransitionOperationState.FAILED
        assert record.failure is not None
        assert record.failure.cause_type == "RuntimeError"

        next_id = await _admit(main, lambda: asyncio.sleep(0))
        assert next_id is not None
        await _wait_for_operation(next_id)
        assert main._active_transition_operation_id is None
        assert store.get_operation(next_id).state is TransitionOperationState.SUCCEEDED

    asyncio.run(exercise())


def test_transition_worker_persists_typed_post_admission_failure(monkeypatch) -> None:
    import vs_api.main as main

    async def exercise() -> None:
        store = _install_store(monkeypatch, main)

        async def worker() -> None:
            raise SessionDeploymentPreparationError(
                SessionDeploymentPreparationErrorEvidence(
                    code=SessionDeploymentPreparationErrorCode.STALE_REPOSITORY,
                    message="Saved dependency changed after admission",
                    session_ref="user:sessions/demo.yaml",
                )
            )

        operation_id = await _admit(main, worker)
        assert operation_id is not None
        await _wait_for_operation(operation_id)

        record = store.get_operation(operation_id)
        assert record.state is TransitionOperationState.FAILED
        assert record.failure is not None
        assert record.failure.code == "session_deployment.stale_repository"
        assert record.failure.message == "Saved dependency changed after admission"

    asyncio.run(exercise())


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


def test_transition_admission_releases_after_cancellation(monkeypatch) -> None:
    import vs_api.main as main

    async def exercise() -> None:
        store = _install_store(monkeypatch, main)
        started = asyncio.Event()

        async def worker() -> None:
            started.set()
            await asyncio.Event().wait()

        operation_id = await _admit(main, worker)
        assert operation_id is not None
        await started.wait()
        task = next(
            task
            for task in asyncio.all_tasks()
            if task.get_name() == f"session-transition-{operation_id}"
        )
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        assert main._active_transition_operation_id is None
        assert store.get_operation(operation_id).state is TransitionOperationState.CANCELLED
        assert await _admit(main, lambda: asyncio.sleep(0)) is not None
        next_operation_id = main._active_transition_operation_id
        assert next_operation_id is not None
        await _wait_for_operation(next_operation_id)

    asyncio.run(exercise())


def test_transition_query_returns_not_found_for_invalid_or_unknown_id(monkeypatch) -> None:
    import vs_api.main as main

    async def exercise() -> None:
        _install_store(monkeypatch, main)
        with pytest.raises(HTTPException) as invalid:
            await main.get_session_transition(
                "../not-an-operation",
            )
        assert invalid.value.status_code == 404

        with pytest.raises(HTTPException) as missing:
            await main.get_session_transition(
                "0123456789abcdef0123456789abcdef",
            )
        assert missing.value.status_code == 404

    asyncio.run(exercise())


def test_transition_query_returns_public_view_without_private_provenance(monkeypatch) -> None:
    import vs_api.main as main

    async def exercise() -> None:
        store = _install_store(monkeypatch, main)
        operation_id = "0123456789abcdef0123456789abcdef"
        store.reserve(
            operation_id,
            TransitionOperationReservation(
                source=TransitionOperationSource(
                    kind=TransitionOperationSourceKind.CATALOG_SESSION,
                    logical_id="user:sessions/query.yaml",
                ),
                facts=TransitionOperationFacts(release="test", build="test"),
                provenance=TransitionOperationProvenance(upload_id="catalog-query"),
            ),
        )
        visible = await main.get_session_transition(operation_id)
        assert visible.operation_id == operation_id
        assert "catalog-query" not in str(visible.model_dump(mode="json"))

    asyncio.run(exercise())


def test_catalog_switch_admission_is_revision_and_closure_bound(monkeypatch) -> None:
    import vs_api.main as main
    import vs_api.session_deployment as session_deployment

    captured: list[object] = []

    catalog_context = SimpleNamespace()

    def prepare(context, **kwargs):
        captured.append((context, kwargs))
        return "prepared"

    async def admit(worker, *, reservation):
        captured.append((worker, reservation))
        return "0123456789abcdef0123456789abcdef"

    async def inline(function, *args, **kwargs):
        return function(*args, **kwargs)

    monkeypatch.setattr(main, "_session_manager", object())
    monkeypatch.setattr(main, "_available_session_node_count", lambda: 7)
    monkeypatch.setattr(main, "_prepared_transition_reservation", lambda value: value)
    monkeypatch.setattr(main, "_admit_transition", admit)
    monkeypatch.setattr(main.asyncio, "to_thread", inline)
    monkeypatch.setattr(session_deployment, "prepare_catalog_session_deployment", prepare)

    request = CatalogSessionSwitchRequest(
        source=CatalogSessionSourceId(session_ref="user:sessions/demo.yaml"),
        expected_source_revision=DIGEST_A,
        expected_document_digest=DIGEST_B,
        expected_dependency_digest=DIGEST_C,
    )
    response = asyncio.run(main.switch_session(request, catalog_context))

    assert response == CatalogSessionSwitchAccepted(
        operation_id="0123456789abcdef0123456789abcdef",
        source=request.source,
    )
    assert captured[0] == (
        catalog_context,
        {
            "session_ref": "user:sessions/demo.yaml",
            "available_node_count": 7,
            "expected_session_revision": DIGEST_A,
            "expected_document_digest": DIGEST_B,
            "expected_closure_digest": DIGEST_C,
        },
    )


def test_startup_reconciliation_keeps_matching_wiring_operation_then_completes(
    monkeypatch,
) -> None:
    import vs_api.main as main

    async def inline(function, *args, **kwargs):
        return function(*args, **kwargs)

    monkeypatch.setattr(main.asyncio, "to_thread", inline)

    async def exercise() -> None:
        store = _install_store(monkeypatch, main)
        operation_id = "0123456789abcdef0123456789abcdef"
        store.reserve(
            operation_id,
            TransitionOperationReservation(
                source=TransitionOperationSource(
                    kind=TransitionOperationSourceKind.CATALOG_SESSION,
                    logical_id="user:sessions/recovered.yaml",
                ),
                facts=TransitionOperationFacts(
                    document_digest=DIGEST_A,
                    closure_digest=DIGEST_B,
                    resolved_semantic_digest=DIGEST_C,
                    file_count=3,
                    total_bytes=3072,
                    release="test",
                    build="test",
                ),
                provenance=TransitionOperationProvenance(
                    source_revision=DIGEST_A,
                    repository_generation=DIGEST_B,
                    upload_id="catalog-recovered",
                    runtime_plan=TransitionRuntimePlan(
                        namespace="nodalarc",
                        name="current-session",
                    ),
                ),
            ),
        )
        cr = {
            "metadata": {
                "namespace": "nodalarc",
                "name": "current-session",
                "uid": "uid-current-session",
                "resourceVersion": "41",
                "generation": 9,
                "annotations": {
                    "nodalarc.io/source-kind": "catalog_session",
                    "nodalarc.io/source-id": "user:sessions/recovered.yaml",
                    "nodalarc.io/source-revision": DIGEST_A,
                    "nodalarc.io/catalog-generation": DIGEST_B,
                    "nodalarc.io/document-digest": DIGEST_A,
                    "nodalarc.io/closure-digest": DIGEST_B,
                },
            },
            "spec": {
                "sessionYaml": ROOT_YAML,
                "catalogUpload": _catalog_selection("catalog-recovered").model_dump(mode="json"),
            },
            "status": {
                "observedGeneration": 9,
                "phase": "Wiring",
                "sessionRunId": "recovered-run",
                "podCount": 4,
                "readyPods": 2,
                "wiredPods": 1,
                "documentDigest": DIGEST_A,
                "closureDigest": DIGEST_B,
                "resolvedSemanticDigest": DIGEST_C,
                "runtimeRelease": "test",
                "runtimeBuild": "test",
            },
        }

        await main._reconcile_interrupted_transition(cr)
        recovered = store.get_operation(operation_id)
        assert recovered.state is TransitionOperationState.SWITCHING
        assert recovered.provenance.constellation_spec is not None
        assert recovered.provenance.constellation_spec.status.phase == "Wiring"
        assert main._active_transition_operation_id == operation_id

        cr["status"].update(
            {
                "phase": "Ready",
                "readyPods": 4,
                "wiredPods": 4,
            }
        )
        cr["metadata"]["resourceVersion"] = "42"
        monkeypatch.setattr(
            main,
            "_extract_cr_session",
            lambda *_args, **_kwargs: SimpleNamespace(
                session_id="recovered-run",
                generation=9,
            ),
        )
        await main._reconcile_interrupted_transition(cr)
        assert store.get_operation(operation_id).state is TransitionOperationState.SUCCEEDED
        assert store.get_operation(operation_id).runtime is not None
        assert store.get_operation(operation_id).provenance.constellation_spec is not None
        assert store.get_operation(operation_id).provenance.constellation_spec.status.phase == (
            "Ready"
        )
        assert main._active_transition_operation_id is None

    asyncio.run(exercise())


def test_live_worker_is_not_reconciled_against_old_cr(monkeypatch) -> None:
    import vs_api.main as main

    async def exercise() -> None:
        store = _install_store(monkeypatch, main)
        started = asyncio.Event()
        release = asyncio.Event()

        async def worker() -> None:
            started.set()
            await release.wait()

        operation_id = await _admit(main, worker)
        assert operation_id is not None
        await started.wait()
        await main._reconcile_interrupted_transition(None)
        assert store.get_operation(operation_id).state is TransitionOperationState.COLLECTING

        release.set()
        await _wait_for_operation(operation_id)
        assert store.get_operation(operation_id).state is TransitionOperationState.SUCCEEDED

    asyncio.run(exercise())
