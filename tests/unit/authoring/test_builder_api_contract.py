"""Tests for Builder application contracts and their generated TypeScript."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from nodalarc.models.builder_api import (
    BuilderCatalogDocument,
    BuilderCompileRequest,
    BuilderCompileResult,
    BuilderContentEdit,
    BuilderDeployVerdict,
    BuilderDigests,
    BuilderDraftEnvelope,
    BuilderIssue,
    BuilderListEdit,
    BuilderPendingEdit,
    BuilderPendingEditOutcome,
    BuilderPendingEdits,
    BuilderSessionSaveRequest,
    BuilderSessionSaveResult,
    BuilderVerdict,
    DependencyClosureEntry,
    DependencyClosureInventory,
)
from nodalarc.resolve_session import resolve_session_with_assets
from pydantic import TypeAdapter, ValidationError
from vs_api.builder_world import world_from_resolution

from tests.catalog_session_fixtures import shipped_read_view

ROOT = Path(__file__).resolve().parents[3]
PENDING_EDIT = TypeAdapter(BuilderPendingEdit)
PENDING_EDITS = TypeAdapter(BuilderPendingEdits)
DOCUMENT_DIGEST = f"sha256:{'a' * 64}"
DEPENDENCY_DIGEST = f"sha256:{'b' * 64}"
OTHER_DIGEST = f"sha256:{'c' * 64}"

SESSION_DOCUMENT = {
    "session": {"name": "demo", "display_name": "Demo session"},
    "segments": [
        {
            "id": "leo",
            "source": "nodalarc:constellations/earth/leo/earth-leo-ring-36.yaml",
        }
    ],
    "time": {
        "start_time": "2026-06-08T00:00:00Z",
        "step_seconds": 10,
        "compression": 1,
    },
}
SESSION_YAML = """session:
  name: demo
  display_name: Demo session
segments:
- id: leo
  source: nodalarc:constellations/earth/leo/earth-leo-ring-36.yaml
time:
  start_time: '2026-06-08T00:00:00Z'
  step_seconds: 10
  compression: 1
"""


def _blocking_issue(operation: str) -> BuilderIssue:
    return BuilderIssue(
        code=f"builder.{operation}.blocked",
        stage="persistence" if operation == "save" else "deployment",
        severity="error",
        message=f"{operation} is blocked",
        blocks=(operation,),
    )


def _draft() -> BuilderDraftEnvelope:
    return BuilderDraftEnvelope(
        draft_revision=7,
        state={
            "session": SESSION_DOCUMENT,
            "catalog_documents": [
                {
                    "ref": "user:nodes/router.yaml",
                    "document": {"node": {"id": "router"}},
                    "origin": "generated",
                }
            ],
        },
    )


def _closure() -> DependencyClosureInventory:
    entry = DependencyClosureEntry(
        ref="nodalarc:nodes/router.yaml",
        family="nodes",
        revision="node-rev-1",
        document_digest=OTHER_DIGEST,
        preserved_path="catalog/nodalarc/nodes/router.yaml",
        size_bytes=41,
    )
    return DependencyClosureInventory(
        entries=(entry,),
        file_count=1,
        total_bytes=41,
        closure_digest=DEPENDENCY_DIGEST,
    )


def _digests() -> BuilderDigests:
    return BuilderDigests(
        document=DOCUMENT_DIGEST,
        dependency=DEPENDENCY_DIGEST,
    )


def _member_edit(list_pointer: str, object_id: str, **values: Any) -> dict[str, Any]:
    return {
        "kind": "content",
        "address": {"kind": "member", "list_pointer": list_pointer, "id": object_id},
        "base": {"id": object_id},
        "working": {"id": object_id},
        **values,
    }


def _boundaries_edit(**values: Any) -> dict[str, Any]:
    return {
        "kind": "content",
        "address": {"kind": "whole_list", "pointer": "/routing/boundaries"},
        "base": [],
        "working": [],
        **values,
    }


def _list_edit(list_pointer: str, ids_before: list[str], ids_wanted: list[str]) -> dict[str, Any]:
    return {
        "kind": "list",
        "list_pointer": list_pointer,
        "ids_before": ids_before,
        "ids_wanted": ids_wanted,
    }


def _session_document() -> BuilderCatalogDocument:
    return BuilderCatalogDocument(
        ref="user:sessions/demo.yaml",
        family="sessions",
        canonical_yaml=SESSION_YAML,
        canonical_json=SESSION_DOCUMENT,
        content_digest=DOCUMENT_DIGEST,
        revision="session-rev-2",
    )


def test_draft_catalog_proposals_are_user_owned_unique_components() -> None:
    proposal = {
        "ref": "user:nodes/router.yaml",
        "document": {"node": {}},
        "origin": "generated",
    }
    draft = BuilderDraftEnvelope(
        draft_revision=0,
        state={"session": {}, "catalog_documents": [proposal]},
    )
    assert draft.state.catalog_documents[0].document == {"node": {}}

    with pytest.raises(ValidationError, match="user: namespace"):
        BuilderDraftEnvelope(
            draft_revision=0,
            state={
                "session": {},
                "catalog_documents": [{**proposal, "ref": "nodalarc:nodes/router.yaml"}],
            },
        )

    with pytest.raises(ValidationError, match="stored separately"):
        BuilderDraftEnvelope(
            draft_revision=0,
            state={
                "session": {},
                "catalog_documents": [{**proposal, "ref": "user:sessions/demo.yaml"}],
            },
        )

    with pytest.raises(ValidationError, match="catalog family"):
        BuilderDraftEnvelope(
            draft_revision=0,
            state={
                "session": {},
                "catalog_documents": [{**proposal, "ref": "user:unregistered.yaml"}],
            },
        )

    with pytest.raises(ValidationError, match="must be unique"):
        BuilderDraftEnvelope(
            draft_revision=0,
            state={"session": {}, "catalog_documents": [proposal, proposal]},
        )


def test_catalog_documents_and_requests_enforce_reference_families() -> None:
    with pytest.raises(ValidationError, match="family must match"):
        BuilderCatalogDocument(
            ref="user:nodes/router.yaml",
            family="sessions",
            canonical_yaml="node:\n  id: router\n  forwarding: routed\n",
            canonical_json={
                "node": {
                    "id": "router",
                    "forwarding": "routed",
                    "ethernet": [],
                    "terminals": [],
                    "payloads": [],
                }
            },
            content_digest=DOCUMENT_DIGEST,
            revision="node-rev-1",
        )

    with pytest.raises(ValidationError, match="canonical document must match"):
        BuilderCatalogDocument(
            ref="user:nodes/router.yaml",
            family="nodes",
            canonical_yaml=SESSION_YAML,
            canonical_json=SESSION_DOCUMENT,
            content_digest=DOCUMENT_DIGEST,
            revision="node-rev-1",
        )

    with pytest.raises(ValidationError, match="catalog family"):
        BuilderCompileRequest(draft=_draft(), target_ref="user:nodes/router.yaml")

    with pytest.raises(ValidationError, match="user: namespace"):
        BuilderCompileRequest(
            draft=_draft(),
            target_ref="nodalarc:sessions/demo.yaml",
        )

    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        BuilderSessionSaveRequest(
            draft=_draft(),
            target_ref="user:sessions/demo.yaml",
            unexpected_dependency_revisions={"user:nodes/router.yaml": "node-rev-1"},
        )


def test_blocking_issues_and_operation_verdicts_are_self_consistent() -> None:
    save_blocker = _blocking_issue("save")

    blocked = BuilderVerdict(operation="save", allowed=False, blockers=(save_blocker,))
    assert blocked.allowed is False

    with pytest.raises(ValidationError, match="only error issues"):
        BuilderIssue(
            code="builder.save.warning",
            stage="persistence",
            severity="warning",
            message="warning cannot block",
            blocks=("save",),
        )

    with pytest.raises(ValidationError, match="allowed operation cannot carry blockers"):
        BuilderVerdict(operation="save", allowed=True, blockers=(save_blocker,))

    with pytest.raises(ValidationError, match="must identify at least one blocker"):
        BuilderVerdict(operation="save", allowed=False)

    with pytest.raises(ValidationError, match="must block the verdict operation"):
        BuilderVerdict(operation="deploy", allowed=False, blockers=(save_blocker,))


def test_dependency_closure_inventory_proves_counts_sizes_and_uniqueness() -> None:
    closure = _closure()
    entry = closure.entries[0]

    with pytest.raises(ValidationError, match="file_count"):
        DependencyClosureInventory(
            entries=(entry,),
            file_count=2,
            total_bytes=41,
            closure_digest=DEPENDENCY_DIGEST,
        )

    with pytest.raises(ValidationError, match="byte total"):
        DependencyClosureInventory(
            entries=(entry,),
            file_count=1,
            total_bytes=40,
            closure_digest=DEPENDENCY_DIGEST,
        )

    with pytest.raises(ValidationError, match="refs must be unique"):
        DependencyClosureInventory(
            entries=(entry, entry),
            file_count=2,
            total_bytes=82,
            closure_digest=DEPENDENCY_DIGEST,
        )


def test_compile_result_requires_canonical_facts_before_save() -> None:
    draft = _draft()
    save_allowed = BuilderVerdict(operation="save", allowed=True)
    deploy_allowed = BuilderVerdict(operation="deploy", allowed=True)

    result = BuilderCompileResult(
        draft=draft,
        target_ref="user:sessions/demo.yaml",
        canonical_session_yaml=SESSION_YAML,
        canonical_session_json=SESSION_DOCUMENT,
        dependency_closure=_closure(),
        resolved_preview=world_from_resolution(
            resolve_session_with_assets(SESSION_DOCUMENT, catalog=shipped_read_view())
        ),
        digests=_digests(),
        save_verdict=save_allowed,
        deploy_eligibility_after_save=deploy_allowed,
    )
    assert result.deploy_eligibility_after_save.allowed is True

    with pytest.raises(ValidationError, match="resolved_preview"):
        BuilderCompileResult(
            draft=draft,
            target_ref="user:sessions/demo.yaml",
            canonical_session_yaml=SESSION_YAML,
            canonical_session_json=SESSION_DOCUMENT,
            dependency_closure=_closure(),
            resolved_preview={"nodes": ["sat-1"]},
            digests=_digests(),
            save_verdict=save_allowed,
            deploy_eligibility_after_save=deploy_allowed,
        )

    with pytest.raises(ValidationError, match="must include all canonical facts"):
        BuilderCompileResult(
            draft=draft,
            target_ref="user:sessions/demo.yaml",
            save_verdict=save_allowed,
            deploy_eligibility_after_save=deploy_allowed,
        )

    with pytest.raises(ValidationError, match="dependency digest"):
        BuilderCompileResult(
            draft=draft,
            target_ref="user:sessions/demo.yaml",
            canonical_session_yaml=SESSION_YAML,
            canonical_session_json=SESSION_DOCUMENT,
            dependency_closure=_closure(),
            digests=BuilderDigests(
                document=DOCUMENT_DIGEST,
                dependency=OTHER_DIGEST,
            ),
            save_verdict=save_allowed,
            deploy_eligibility_after_save=deploy_allowed,
        )


def test_compile_result_cannot_approve_deploy_when_save_is_blocked() -> None:
    with pytest.raises(ValidationError, match="unsaveable draft"):
        BuilderCompileResult(
            draft=_draft(),
            target_ref="user:sessions/demo.yaml",
            save_verdict=BuilderVerdict(
                operation="save",
                allowed=False,
                blockers=(_blocking_issue("save"),),
            ),
            deploy_eligibility_after_save=BuilderVerdict(operation="deploy", allowed=True),
        )


def test_compile_result_counts_every_pending_edit_it_refused() -> None:
    stale_issue = BuilderIssue(
        code="builder.staleness.pending_edit",
        stage="staleness",
        severity="error",
        message="Link rule 'isl' changed since the edit began",
        blocks=("save", "deploy"),
        json_pointer="/link_rules/0",
        draft_path="pending_edits.0",
    )
    stale = BuilderPendingEditOutcome(status="stale", issues=(stale_issue,))
    blocked = {
        "save_verdict": BuilderVerdict(operation="save", allowed=False, blockers=(stale_issue,)),
        "deploy_eligibility_after_save": BuilderVerdict(
            operation="deploy", allowed=False, blockers=(stale_issue,)
        ),
    }
    result = BuilderCompileResult(
        draft=_draft(),
        target_ref="user:sessions/demo.yaml",
        issues=(stale_issue,),
        pending_edit_outcomes=(stale,),
        **blocked,
    )
    assert result.pending_edit_outcomes == (stale,)

    with pytest.raises(ValidationError, match="stale edit's issues must be among"):
        BuilderCompileResult(
            draft=_draft(),
            target_ref="user:sessions/demo.yaml",
            pending_edit_outcomes=(stale,),
            **blocked,
        )

    missing_issue = BuilderIssue(
        code="builder.structural.missing",
        stage="structural",
        severity="error",
        message="Field required",
        blocks=("save", "deploy"),
        json_pointer="/link_rules/0/endpoints",
        draft_path="pending_edits.0",
    )
    invalid = BuilderPendingEditOutcome(status="invalid", issues=(missing_issue,))
    with pytest.raises(ValidationError, match="invalid edit cannot be saveable"):
        BuilderCompileResult(
            draft=_draft(),
            target_ref="user:sessions/demo.yaml",
            canonical_session_yaml=SESSION_YAML,
            canonical_session_json=SESSION_DOCUMENT,
            dependency_closure=_closure(),
            digests=_digests(),
            pending_edit_outcomes=(invalid,),
            save_verdict=BuilderVerdict(operation="save", allowed=True),
            deploy_eligibility_after_save=BuilderVerdict(operation="deploy", allowed=True),
        )


def test_save_result_binds_document_closure_and_deploy_verdict_identity() -> None:
    digests = _digests()
    deploy_verdict = BuilderDeployVerdict(
        allowed=True,
        session_ref="user:sessions/demo.yaml",
        session_revision="session-rev-2",
        digests=digests,
    )
    result = BuilderSessionSaveResult(
        session=_session_document(),
        digests=digests,
        dependency_closure=_closure(),
        deploy_verdict=deploy_verdict,
    )
    assert result.deploy_verdict.allowed is True

    with pytest.raises(ValidationError, match="saved session digest"):
        BuilderSessionSaveResult(
            session=_session_document(),
            digests=BuilderDigests(
                document=OTHER_DIGEST,
                dependency=DEPENDENCY_DIGEST,
            ),
            dependency_closure=_closure(),
            deploy_verdict=deploy_verdict,
        )

    with pytest.raises(ValidationError, match="revision must match"):
        BuilderSessionSaveResult(
            session=_session_document(),
            digests=digests,
            dependency_closure=_closure(),
            deploy_verdict=BuilderDeployVerdict(
                allowed=True,
                session_ref="user:sessions/demo.yaml",
                session_revision="stale-revision",
                digests=digests,
            ),
        )


def test_blocked_deploy_verdict_requires_a_deploy_blocker() -> None:
    with pytest.raises(ValidationError, match="must identify at least one blocker"):
        BuilderDeployVerdict(
            allowed=False,
            session_ref="user:sessions/demo.yaml",
            session_revision="session-rev-2",
            digests=_digests(),
        )

    with pytest.raises(ValidationError, match="must block deployment"):
        BuilderDeployVerdict(
            allowed=False,
            session_ref="user:sessions/demo.yaml",
            session_revision="session-rev-2",
            digests=_digests(),
            blockers=(_blocking_issue("save"),),
        )


def test_pending_edits_address_only_the_closed_session_lists() -> None:
    rule = PENDING_EDIT.validate_json(json.dumps(_member_edit("/link_rules", "leo_isl")))
    domain = PENDING_EDIT.validate_json(json.dumps(_member_edit("/routing/domains", "earth")))
    boundaries = PENDING_EDIT.validate_json(json.dumps(_boundaries_edit()))
    removal = PENDING_EDIT.validate_json(
        json.dumps(_list_edit("/routing/domains", ["earth", "luna"], ["luna"]))
    )
    assert isinstance(rule, BuilderContentEdit)
    assert isinstance(domain, BuilderContentEdit)
    assert isinstance(boundaries, BuilderContentEdit)
    assert isinstance(removal, BuilderListEdit)
    assert removal.ids_wanted == ("luna",)

    for address in (
        {"kind": "member", "list_pointer": "/ephemeris", "id": "leo"},
        {"kind": "member", "list_pointer": "/routing/boundaries", "id": "leo"},
        {"kind": "section", "pointer": "/link_rules"},
        {"kind": "member", "list_pointer": "/link_rules", "id": "Leo ISL"},
        {"kind": "whole_list", "pointer": "/link_rules"},
        {"kind": "member", "list_pointer": "/link_rules", "id": "leo", "label": "ISL"},
    ):
        with pytest.raises(ValidationError):
            PENDING_EDIT.validate_python({**_member_edit("/link_rules", "leo"), "address": address})

    # A boundary has no id to remove or reorder it by; the whole list is one content edit.
    with pytest.raises(ValidationError, match="'/routing/domains' or '/segments'"):
        PENDING_EDIT.validate_python(_list_edit("/routing/boundaries", ["a"], []))


def test_pending_edit_values_fit_their_address_and_keep_its_id() -> None:
    new_rule = PENDING_EDIT.validate_python({**_member_edit("/link_rules", "isl"), "base": None})
    assert isinstance(new_rule, BuilderContentEdit)
    assert new_rule.base is None
    absent_list = PENDING_EDIT.validate_python({**_boundaries_edit(), "base": None})
    assert isinstance(absent_list, BuilderContentEdit)
    assert absent_list.base is None

    with pytest.raises(ValidationError, match="holds that object"):
        PENDING_EDIT.validate_python(_member_edit("/link_rules", "isl", working=[]))
    with pytest.raises(ValidationError, match="holds that object"):
        PENDING_EDIT.validate_python(_member_edit("/link_rules", "isl", base=[]))
    with pytest.raises(ValidationError, match="holds that list"):
        PENDING_EDIT.validate_python(_boundaries_edit(working={"over": "isl"}))
    with pytest.raises(ValidationError, match="holds that list"):
        PENDING_EDIT.validate_python(_boundaries_edit(base={"over": "isl"}))
    for values in (
        {"working": {"id": "renamed"}},
        {"working": {"topology": {"mode": "visible_candidates"}}},
        {"base": {"id": "other"}},
    ):
        with pytest.raises(ValidationError, match="a rename is an edit of the session YAML"):
            PENDING_EDIT.validate_python(_member_edit("/link_rules", "isl", **values))


def test_list_edits_only_remove_or_reorder_their_ids() -> None:
    reorder = PENDING_EDIT.validate_python(_list_edit("/link_rules", ["a", "b"], ["b", "a"]))
    assert isinstance(reorder, BuilderListEdit)
    assert reorder.ids_before == ("a", "b")
    emptied = PENDING_EDIT.validate_python(_list_edit("/link_rules", ["a"], []))
    assert isinstance(emptied, BuilderListEdit)
    assert emptied.ids_wanted == ()

    with pytest.raises(ValidationError, match="only removes or reorders"):
        PENDING_EDIT.validate_python(_list_edit("/link_rules", ["a"], ["a", "c"]))
    with pytest.raises(ValidationError, match="ids must be unique"):
        PENDING_EDIT.validate_python(_list_edit("/link_rules", ["a", "a"], ["a"]))
    with pytest.raises(ValidationError, match="ids must be unique"):
        PENDING_EDIT.validate_python(_list_edit("/link_rules", ["a", "b"], ["a", "a"]))


def test_a_pending_edit_batch_holds_one_edit_per_address() -> None:
    accepted = PENDING_EDITS.validate_json(
        json.dumps(
            [
                _member_edit("/link_rules", "isl"),
                _member_edit("/routing/domains", "isl"),
                _list_edit("/link_rules", ["isl"], ["isl"]),
                _list_edit("/routing/domains", ["isl"], ["isl"]),
                _boundaries_edit(),
            ]
        )
    )
    assert len(accepted) == 5

    for duplicated in (
        _member_edit("/link_rules", "isl"),
        _boundaries_edit(),
        _list_edit("/routing/domains", ["isl"], []),
    ):
        with pytest.raises(ValidationError, match="one content edit per address"):
            PENDING_EDITS.validate_python([duplicated, duplicated])


def test_pending_edit_outcomes_carry_issues_only_when_not_applied() -> None:
    stale = BuilderIssue(
        code="builder.staleness.pending_edit",
        stage="staleness",
        severity="error",
        message="Link rule 'isl' changed since the edit began",
        blocks=("save", "deploy"),
        json_pointer="/link_rules/0",
        draft_path="pending_edits.0",
    )
    assert BuilderPendingEditOutcome(status="applied").issues == ()
    assert BuilderPendingEditOutcome(status="stale", issues=(stale,)).issues == (stale,)

    with pytest.raises(ValidationError, match="applied edit carries no issues"):
        BuilderPendingEditOutcome(status="applied", issues=(stale,))
    for status in ("stale", "invalid"):
        with pytest.raises(ValidationError, match="names at least one issue"):
            BuilderPendingEditOutcome(status=status)


def test_generated_builder_api_types_are_fresh() -> None:
    completed = subprocess.run(
        [sys.executable, "scripts/gen_builder_api_types.py", "--check"],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr
