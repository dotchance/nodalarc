from __future__ import annotations

from typing import Any

import pytest

from tests.integration import test_builder_catalog_deployment as builder_e2e


class _CleanupEndpoint:
    def __init__(
        self,
        *,
        active_source: bool = False,
        deploy_allowed: bool = True,
        refs: tuple[str, ...] = (),
    ) -> None:
        self.active_source = active_source
        self.deploy_allowed = deploy_allowed
        self.documents = {ref: f"revision-{index}" for index, ref in enumerate(refs)}
        self.events: list[tuple[str, str, Any]] = []

    def request_list(
        self,
        method: str,
        path: str,
        **_kwargs: Any,
    ) -> list[dict[str, Any]]:
        self.events.append((method, path, None))
        return [
            {
                "source_id": {
                    "kind": "catalog",
                    "session_ref": builder_e2e.SOURCE_SESSION_REF,
                },
                "active": self.active_source,
                "deploy_allowed": self.deploy_allowed,
                "source_revision": "source-revision",
                "document_digest": "document-digest",
                "dependency_digest": "dependency-digest",
                "blockers": [] if self.deploy_allowed else [{"code": "blocked"}],
            }
        ]

    def request_json_response(
        self,
        method: str,
        path: str,
        *,
        payload: dict[str, Any],
        **_kwargs: Any,
    ) -> tuple[int, dict[str, Any]]:
        self.events.append((method, path, payload))
        ref = payload["ref"]
        revision = self.documents.get(ref)
        if revision is None:
            return 404, {"detail": "not found"}
        return 200, {"ref": ref, "revision": revision}

    def request_json(
        self,
        method: str,
        path: str,
        *,
        payload: dict[str, Any] | None = None,
        **_kwargs: Any,
    ) -> dict[str, Any]:
        self.events.append((method, path, payload))
        if path == "/api/v1/builder/session/deploy":
            return {"status": "accepted", "operation_id": "restore-operation"}
        assert payload is not None
        ref = payload["ref"]
        if path == "/api/v1/builder/catalog/dependents":
            return {
                "target_ref": ref,
                "target_revision": self.documents[ref],
                "transitive_dependents": [],
                "delete_allowed": True,
                "acknowledgement": f"ack-{ref}",
            }
        if path == "/api/v1/builder/catalog/delete":
            revision = self.documents.pop(ref)
            return {
                "deleted_ref": ref,
                "deleted_revision": revision,
                "generation": f"generation-{ref}",
            }
        raise AssertionError(f"unexpected request {method} {path}")


def test_cleanup_restores_shipped_source_before_deleting_dependency_chain(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session_ref = "user:sessions/e2e-builder-test.yaml"
    forked_refs = (
        "user:constellations/e2e-builder-test/root.yaml",
        "user:nodes/e2e-builder-test/node.yaml",
        "user:terminals/e2e-builder-test.yaml",
    )
    endpoint = _CleanupEndpoint(refs=(session_ref, *forked_refs))

    def completed_transition(_endpoint: Any, operation_id: str) -> dict[str, Any]:
        endpoint.events.append(("WAIT", operation_id, None))
        return {"state": "succeeded", "operation_id": operation_id}

    monkeypatch.setattr(builder_e2e, "_wait_for_transition", completed_transition)

    evidence = builder_e2e._cleanup_builder_e2e_catalog(
        endpoint,
        session_ref=session_ref,
        forked_refs=forked_refs,
    )

    assert evidence["status"] == "PASS"
    assert evidence["source_restore"]["status"] == "restored"
    assert [item["ref"] for item in evidence["deletions"]] == [session_ref, *forked_refs]
    assert not endpoint.documents
    paths = [path for _, path, _ in endpoint.events]
    assert paths[:3] == [
        "/api/v1/sessions",
        "/api/v1/builder/session/deploy",
        "restore-operation",
    ]
    first_delete_probe = paths.index("/api/v1/builder/catalog/get")
    assert first_delete_probe > paths.index("restore-operation")


def test_cleanup_skips_absent_artifacts_after_source_is_already_active() -> None:
    session_ref = "user:sessions/e2e-builder-absent.yaml"
    endpoint = _CleanupEndpoint(active_source=True)

    evidence = builder_e2e._cleanup_builder_e2e_catalog(
        endpoint,
        session_ref=session_ref,
        forked_refs=("user:terminals/e2e-builder-absent.yaml",),
    )

    assert evidence["status"] == "PASS"
    assert evidence["source_restore"] == {
        "status": "already_active",
        "session_ref": builder_e2e.SOURCE_SESSION_REF,
    }
    assert [item["status"] for item in evidence["deletions"]] == [
        "not_found",
        "not_found",
    ]
    assert all(path != "/api/v1/builder/session/deploy" for _, path, _ in endpoint.events)


def test_cleanup_does_not_delete_when_source_restore_is_refused() -> None:
    session_ref = "user:sessions/e2e-builder-retained.yaml"
    endpoint = _CleanupEndpoint(deploy_allowed=False, refs=(session_ref,))

    with pytest.raises(builder_e2e._BuilderE2ECleanupError) as failure:
        builder_e2e._cleanup_builder_e2e_catalog(
            endpoint,
            session_ref=session_ref,
            forked_refs=(),
        )

    assert failure.value.evidence["status"] == "FAIL"
    assert failure.value.evidence["deletions"] == []
    assert endpoint.documents == {session_ref: "revision-0"}
    assert [path for _, path, _ in endpoint.events] == ["/api/v1/sessions"]
