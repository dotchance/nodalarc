"""Transactional contracts for Builder session persistence."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
import yaml
from nodalarc.catalog_refs import CatalogRef
from nodalarc.catalog_repository import CatalogNotFoundError, CatalogScope
from nodalarc.filesystem_catalog_repository import FilesystemCatalogRepository
from nodalarc.models.builder_api import (
    BuilderCompileRequest,
    BuilderDraftEnvelope,
    BuilderSessionSaveRequest,
)
from vs_api import builder_session_service as service_module
from vs_api.builder_compiler import (
    canonicalize_persisted_configuration,
    compile_builder_draft,
)
from vs_api.builder_session_service import (
    BuilderSessionSaveBlockedError,
    BuilderSessionSaveErrorCode,
    BuilderSessionSavePersistenceError,
    BuilderSessionSaveStaleError,
    save_builder_session,
)
from vs_api.catalog_context import CatalogContext

from tests.builder_world_fixtures import builder_world_preview

ROOT = Path(__file__).resolve().parents[2]
SHIPPED_ROOT = ROOT / "catalog" / "nodalarc"
SIMPLE_SESSION = SHIPPED_ROOT / "sessions" / "earth-leo-simple.yaml"
SHIPPED_SESSIONS = tuple(sorted((SHIPPED_ROOT / "sessions").glob("*.yaml")))


def _load(path: Path) -> dict[str, Any]:
    return yaml.safe_load(path.read_bytes())


def _load_ref(ref: str) -> dict[str, Any]:
    parsed = CatalogRef(ref)
    assert parsed.namespace == "nodalarc"
    return _load(SHIPPED_ROOT / parsed.relative_path)


@dataclass(frozen=True)
class SaveHarness:
    scope: CatalogScope
    user_root: Path
    repository: FilesystemCatalogRepository
    context: CatalogContext


@pytest.fixture()
def harness(tmp_path: Path) -> SaveHarness:
    scope = CatalogScope()
    user_root = tmp_path / "user-catalog"
    repository = FilesystemCatalogRepository(
        shipped_root=SHIPPED_ROOT,
        scope_roots={scope: user_root},
    )
    return SaveHarness(
        scope=scope,
        user_root=user_root,
        repository=repository,
        context=CatalogContext(repository=repository, scope=scope),
    )


def _deep_request(
    name: str,
    *,
    node_display_name: str = "User spacecraft",
    expected_session_revision: str | None = None,
) -> BuilderSessionSaveRequest:
    session = deepcopy(_load(SIMPLE_SESSION))
    shipped_constellation_ref = session["segments"][0]["source"]
    session["session"]["name"] = name
    session["segments"][0]["source"] = f"user:constellations/{name}-ring.yaml"

    constellation = deepcopy(_load_ref(shipped_constellation_ref))
    shipped_node_ref = constellation["constellation"]["node"]
    constellation["constellation"]["id"] = f"{name}-ring"
    constellation["constellation"]["node"] = f"user:nodes/{name}-node.yaml"

    node = deepcopy(_load_ref(shipped_node_ref))
    shipped_terminal_ref = node["node"]["terminals"][0]["terminal"]
    node["node"]["id"] = f"{name}-node"
    node["node"]["display_name"] = node_display_name
    node["node"]["terminals"][0]["terminal"] = f"user:terminals/{name}-terminal.yaml"

    terminal = deepcopy(_load_ref(shipped_terminal_ref))
    terminal["terminal"]["id"] = f"{name}-terminal"

    documents = (
        (f"user:terminals/{name}-terminal.yaml", terminal),
        (f"user:constellations/{name}-ring.yaml", constellation),
        (f"user:nodes/{name}-node.yaml", node),
    )
    return BuilderSessionSaveRequest(
        draft=BuilderDraftEnvelope(
            draft_revision=1,
            state={
                "session": session,
                "catalog_documents": [
                    {
                        "ref": ref,
                        "document": document,
                        "origin": "generated",
                    }
                    for ref, document in documents
                ],
            },
        ),
        target_ref=f"user:sessions/{name}.yaml",
        expected_session_revision=expected_session_revision,
    )


def _session_request(
    name: str,
    session: dict[str, Any],
) -> BuilderSessionSaveRequest:
    candidate = deepcopy(session)
    candidate.setdefault("session", {})["name"] = name
    return BuilderSessionSaveRequest(
        draft=BuilderDraftEnvelope(
            draft_revision=1,
            state={"session": candidate},
        ),
        target_ref=f"user:sessions/{name}.yaml",
    )


def _compile(request: BuilderSessionSaveRequest, harness: SaveHarness):
    return compile_builder_draft(
        BuilderCompileRequest(draft=request.draft, target_ref=request.target_ref),
        harness.repository.snapshot(harness.scope),
        available_node_count=1_000_000,
        preview_factory=lambda raw, _roots: builder_world_preview(raw["session"]["name"]),
    )


def _save(
    request: BuilderSessionSaveRequest,
    harness: SaveHarness,
    **kwargs: Any,
):
    return save_builder_session(
        request,
        harness.context,
        available_node_count=1_000_000,
        preview_factory=lambda raw, _roots: builder_world_preview(raw["session"]["name"]),
        **kwargs,
    )


def test_deep_user_save_is_atomic_exact_and_publishes_session_last(
    harness: SaveHarness,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = _deep_request("truthful-save")
    compiled = _compile(request, harness)
    assert compiled.save_verdict.allowed is True
    assert compiled.canonical_session_yaml is not None
    before = harness.repository.snapshot(harness.scope)

    write_order: list[str] = []
    snapshot_calls = 0
    original_snapshot = harness.repository.snapshot
    original_begin = harness.repository.begin

    def recording_snapshot(scope: CatalogScope):
        nonlocal snapshot_calls
        snapshot_calls += 1
        return original_snapshot(scope)

    def recording_begin(
        scope: CatalogScope,
        *,
        base_generation=None,
    ):
        transaction = original_begin(scope, base_generation=base_generation)
        original_write = transaction.write_bytes

        def recording_write(ref, content, *, expected_revision):
            write_order.append(str(ref))
            original_write(ref, content, expected_revision=expected_revision)

        monkeypatch.setattr(transaction, "write_bytes", recording_write)
        return transaction

    monkeypatch.setattr(harness.repository, "snapshot", recording_snapshot)
    monkeypatch.setattr(harness.repository, "begin", recording_begin)
    result = _save(request, harness)

    assert snapshot_calls == 1
    assert write_order[-1] == str(request.target_ref)
    assert all("user:sessions/" not in ref for ref in write_order[:-1])
    assert write_order[:-1] == sorted(write_order[:-1])
    assert result.session.canonical_yaml == compiled.canonical_session_yaml
    assert result.session.canonical_json["segments"][0]["source"].startswith("user:")
    assert result.deploy_verdict.allowed is True

    committed = original_snapshot(harness.scope)
    assert committed.generation != before.generation
    stored = committed.get(request.target_ref)
    assert stored.content == result.session.canonical_yaml.encode("utf-8")
    assert str(stored.revision) == result.session.revision
    user_entries = {
        str(entry.ref): entry
        for entry in result.dependency_closure.entries
        if entry.ref.namespace == "user"
    }
    assert set(user_entries) == {
        "user:constellations/truthful-save-ring.yaml",
        "user:nodes/truthful-save-node.yaml",
        "user:terminals/truthful-save-terminal.yaml",
    }
    assert all(entry.revision is not None for entry in user_entries.values())
    assert all(
        entry.revision == str(committed.get(ref).revision) for ref, entry in user_entries.items()
    )
    for proposal in request.draft.state.catalog_documents:
        canonical = canonicalize_persisted_configuration(proposal.ref, proposal.document)
        assert committed.read_bytes(proposal.ref) == canonical.yaml_bytes

    reopened_repository = FilesystemCatalogRepository(
        shipped_root=SHIPPED_ROOT,
        scope_roots={harness.scope: harness.user_root},
    )
    reopened = reopened_repository.snapshot(harness.scope)
    assert reopened.read_bytes(request.target_ref) == stored.content
    assert reopened.generation == committed.generation


@pytest.mark.parametrize("session_path", SHIPPED_SESSIONS, ids=lambda path: path.stem)
def test_every_shipped_session_saves_and_reopens_as_first_class_user_yaml(
    harness: SaveHarness,
    session_path: Path,
) -> None:
    request = _session_request(session_path.stem, _load(session_path))

    result = _save(request, harness)

    assert result.deploy_verdict.allowed is True
    assert result.session.ref == f"user:sessions/{session_path.name}"
    assert result.session.canonical_yaml.encode("utf-8") == harness.repository.snapshot(
        harness.scope
    ).read_bytes(result.session.ref)

    reopened_repository = FilesystemCatalogRepository(
        shipped_root=SHIPPED_ROOT,
        scope_roots={harness.scope: harness.user_root},
    )
    reopened_snapshot = reopened_repository.snapshot(harness.scope)
    reopened_document = yaml.safe_load(reopened_snapshot.read_bytes(result.session.ref))
    reopened = compile_builder_draft(
        BuilderCompileRequest(
            draft=BuilderDraftEnvelope(
                draft_revision=2,
                state={"session": reopened_document},
            ),
            target_ref=result.session.ref,
        ),
        reopened_snapshot,
        available_node_count=1_000_000,
        preview_factory=lambda raw, _roots: builder_world_preview(raw["session"]["name"]),
    )
    assert reopened.save_verdict.allowed is True
    assert reopened.deploy_eligibility_after_save.allowed is True
    assert reopened.canonical_session_yaml == result.session.canonical_yaml
    assert reopened.digests == result.digests


def test_session_save_proposals_cannot_replace_existing_catalog_components(
    harness: SaveHarness,
) -> None:
    initial_request = _deep_request("cas-save", node_display_name="First")
    initial = _save(initial_request, harness)
    current = harness.repository.snapshot(harness.scope)
    stored_node = current.get("user:nodes/cas-save-node.yaml")
    replacement_request = _deep_request(
        "cas-save",
        node_display_name="Second",
        expected_session_revision=initial.session.revision,
    )
    with pytest.raises(BuilderSessionSaveStaleError) as blocked:
        _save(replacement_request, harness)
    assert blocked.value.code is BuilderSessionSaveErrorCode.STALE_WRITE
    assert any("create-only" in issue.message for issue in blocked.value.evidence.issues)
    assert (
        harness.repository.snapshot(harness.scope).get("user:nodes/cas-save-node.yaml").content
        == stored_node.content
    )

    with pytest.raises(ValueError, match="Extra inputs are not permitted"):
        BuilderSessionSaveRequest.model_validate(
            {
                **replacement_request.model_dump(mode="json"),
                "draft": {
                    **replacement_request.draft.model_dump(mode="json"),
                    "state": {
                        **replacement_request.draft.state.model_dump(mode="json"),
                        "catalog_documents": [
                            {
                                **proposal.model_dump(mode="json"),
                                "expected_revision": str(stored_node.revision),
                            }
                            for proposal in replacement_request.draft.state.catalog_documents
                        ],
                    },
                },
            }
        )


def test_runtime_unsupported_session_saves_but_cannot_deploy(
    harness: SaveHarness,
) -> None:
    unsupported = deepcopy(_load(SIMPLE_SESSION))
    unsupported["segments"][0]["clock"] = {"model": "affine", "rate": 2.0}
    request = _session_request("unsupported-save", unsupported)

    result = _save(request, harness)

    assert result.deploy_verdict.allowed is False
    assert result.session.canonical_yaml == harness.repository.snapshot(harness.scope).read_bytes(
        request.target_ref
    ).decode("utf-8")
    runtime_blockers = [
        issue for issue in result.deploy_verdict.blockers if issue.stage == "runtime_support"
    ]
    assert runtime_blockers
    assert all(issue.blocks == ("deploy",) for issue in runtime_blockers)


@pytest.mark.parametrize("failure", ["incomplete", "dangling"])
def test_incomplete_or_dangling_draft_writes_nothing(
    harness: SaveHarness,
    failure: str,
) -> None:
    if failure == "incomplete":
        request = _session_request("incomplete-save", {"session": {}})
    else:
        dangling = deepcopy(_load(SIMPLE_SESSION))
        dangling["segments"][0]["source"] = "user:constellations/missing.yaml"
        request = _session_request("dangling-save", dangling)
    before = harness.repository.snapshot(harness.scope)

    expected_error = (
        BuilderSessionSaveBlockedError
        if failure == "incomplete"
        else BuilderSessionSavePersistenceError
    )
    with pytest.raises(expected_error) as raised:
        _save(request, harness)

    assert raised.value.code is (
        BuilderSessionSaveErrorCode.SAVE_BLOCKED
        if failure == "incomplete"
        else BuilderSessionSaveErrorCode.GRAPH_INVALID
    )
    assert raised.value.compile_result is not None
    assert raised.value.evidence.issues
    after = harness.repository.snapshot(harness.scope)
    assert after.generation == before.generation
    assert after.list(namespace="user") == before.list(namespace="user")


def test_direct_save_excludes_invalid_stale_orphan_proposals(
    harness: SaveHarness,
) -> None:
    orphan_ref = CatalogRef("user:nodes/orphan-direct-save.yaml")
    base = _session_request("orphan-direct-save", _load(SIMPLE_SESSION))
    request = BuilderSessionSaveRequest(
        draft=BuilderDraftEnvelope(
            draft_revision=base.draft.draft_revision,
            state={
                "session": base.draft.state.session,
                "catalog_documents": [
                    {
                        "ref": orphan_ref,
                        "origin": "generated",
                        "document": {
                            "node": {
                                "id": "orphan-direct-save",
                                "unknown": True,
                            }
                        },
                    }
                ],
            },
        ),
        target_ref=base.target_ref,
    )

    result = _save(request, harness)

    warning = next(
        issue
        for issue in result.issues
        if issue.code == "builder.draft.unreferenced_catalog_documents"
    )
    assert warning.blocks == ()
    assert warning.related_refs == (str(orphan_ref),)
    committed = harness.repository.snapshot(harness.scope)
    with pytest.raises(CatalogNotFoundError):
        committed.get(orphan_ref)
    assert str(orphan_ref) not in {str(entry.ref) for entry in result.dependency_closure.entries}


def test_injected_commit_failure_keeps_active_repository_unchanged(
    harness: SaveHarness,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = _deep_request("failed-commit")
    before = harness.repository.snapshot(harness.scope)

    def fail_publish(*_args, **_kwargs):
        raise OSError("injected CURRENT publication failure")

    monkeypatch.setattr(harness.repository, "_publish_current", fail_publish)
    with pytest.raises(BuilderSessionSavePersistenceError) as raised:
        _save(request, harness)

    assert raised.value.code is BuilderSessionSaveErrorCode.PERSISTENCE_FAILED
    assert raised.value.evidence.repository_committed is False
    after = harness.repository.snapshot(harness.scope)
    assert after.generation == before.generation
    assert after.list(namespace="user") == before.list(namespace="user")


def test_post_commit_verification_error_reports_that_generation_is_active(
    harness: SaveHarness,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = _session_request("verification-failed", _load(SIMPLE_SESSION))

    def fail_verification(*_args, **_kwargs):
        raise ValueError("injected verification fault")

    monkeypatch.setattr(
        service_module,
        "canonicalize_persisted_configuration",
        fail_verification,
    )
    with pytest.raises(BuilderSessionSavePersistenceError) as raised:
        _save(request, harness)

    assert raised.value.code is BuilderSessionSaveErrorCode.STORAGE_VERIFICATION_FAILED
    assert raised.value.evidence.repository_committed is True
    assert harness.repository.snapshot(harness.scope).get(request.target_ref).family == "sessions"


def test_concurrent_generation_change_is_typed_and_does_not_publish_save(
    harness: SaveHarness,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = _deep_request("concurrent-save")
    original_compile = service_module.compile_builder_draft

    def racing_compile(*args, **kwargs):
        result = original_compile(*args, **kwargs)
        body = {
            "body": {
                "id": "racing-write",
                "display_name": "Racing write",
                "gravitational_parameter_km3_s2": 398600.4418,
                "mean_radius_km": 6371.0088,
                "equatorial_radius_km": 6378.137,
                "polar_radius_km": 6356.752,
                "reference": "urn:nodalarc:test",
            }
        }
        document = canonicalize_persisted_configuration(
            CatalogRef("user:bodies/racing-write.yaml"),
            body,
        )
        transaction = harness.repository.begin(harness.scope)
        transaction.write_bytes(
            document.ref,
            document.yaml_bytes,
            expected_revision=None,
        )
        transaction.commit()
        return result

    monkeypatch.setattr(service_module, "compile_builder_draft", racing_compile)
    with pytest.raises(BuilderSessionSaveStaleError) as raised:
        _save(request, harness)

    assert raised.value.code is BuilderSessionSaveErrorCode.STALE_WRITE
    current = harness.repository.snapshot(harness.scope)
    assert current.get("user:bodies/racing-write.yaml").family == "bodies"
    with pytest.raises(CatalogNotFoundError):
        current.get(request.target_ref)
    for proposal in request.draft.state.catalog_documents:
        with pytest.raises(CatalogNotFoundError):
            current.get(proposal.ref)


def test_post_commit_preparation_issue_blocks_only_deploy(
    harness: SaveHarness,
) -> None:
    request = _session_request("post-commit-blocked", _load(SIMPLE_SESSION))

    def unavailable(*_args, **_kwargs):
        raise ValueError("injected preparation failure")

    result = _save(request, harness, preparer=unavailable)

    assert result.deploy_verdict.allowed is False
    blocker = next(
        issue
        for issue in result.deploy_verdict.blockers
        if issue.code == "builder.persistence.post_commit.preparation"
    )
    assert blocker.stage == "persistence"
    assert harness.repository.snapshot(harness.scope).read_bytes(request.target_ref) == (
        result.session.canonical_yaml.encode("utf-8")
    )
