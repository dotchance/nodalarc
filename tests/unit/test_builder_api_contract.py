"""Tests for Builder application contracts and their generated TypeScript."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest
from nodalarc.models.builder_api import (
    BuilderCatalogDocument,
    BuilderCompileRequest,
    BuilderCompileResult,
    BuilderDeployVerdict,
    BuilderDigests,
    BuilderDraftEnvelope,
    BuilderDraftState,
    BuilderIssue,
    BuilderProposedCatalogDocument,
    BuilderSessionDeployAccepted,
    BuilderSessionDeployRequest,
    BuilderSessionSaveRefusal,
    BuilderSessionSaveRequest,
    BuilderSessionSaveResult,
    BuilderVerdict,
    DependencyClosureEntry,
    DependencyClosureInventory,
    WizardAvailableStationResponse,
    WizardConstellationCapability,
    WizardConstellationGeometry,
    WizardConstellationPreset,
    WizardConstellationPresetResponse,
    WizardExtensionRulesResponse,
    WizardGroundStationSetPresetResponse,
    WizardOrbitModelMetadata,
    WizardSatelliteTypePresetResponse,
)
from nodalarc.models.builder_catalog_api import CatalogDraftCompileResult
from pydantic import ValidationError

from tests.builder_world_fixtures import builder_world_preview

ROOT = Path(__file__).resolve().parents[2]
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

CONTRACT_MODELS = (
    BuilderIssue,
    BuilderCatalogDocument,
    BuilderProposedCatalogDocument,
    BuilderDraftState,
    BuilderDraftEnvelope,
    DependencyClosureEntry,
    DependencyClosureInventory,
    BuilderDigests,
    BuilderVerdict,
    BuilderDeployVerdict,
    BuilderCompileRequest,
    BuilderCompileResult,
    BuilderSessionDeployRequest,
    BuilderSessionDeployAccepted,
    BuilderSessionSaveRequest,
    BuilderSessionSaveResult,
    BuilderSessionSaveRefusal,
    WizardConstellationCapability,
    WizardConstellationPreset,
    WizardConstellationPresetResponse,
    WizardOrbitModelMetadata,
    WizardSatelliteTypePresetResponse,
    WizardGroundStationSetPresetResponse,
    WizardAvailableStationResponse,
    WizardExtensionRulesResponse,
)


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


def _session_document() -> BuilderCatalogDocument:
    return BuilderCatalogDocument(
        ref="user:sessions/demo.yaml",
        family="sessions",
        canonical_yaml=SESSION_YAML,
        canonical_json=SESSION_DOCUMENT,
        content_digest=DOCUMENT_DIGEST,
        revision="session-rev-2",
    )


def test_application_contracts_are_closed_and_immutable() -> None:
    for model in CONTRACT_MODELS:
        assert model.model_json_schema()["additionalProperties"] is False

    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        BuilderDraftEnvelope(
            draft_revision=0,
            state={"session": {}},
            private_session_field=True,
        )

    with pytest.raises(ValidationError, match="valid integer"):
        BuilderDraftEnvelope(draft_revision="7", state={"session": {}})

    draft = _draft()
    with pytest.raises(ValidationError, match="frozen"):
        draft.draft_revision = 8


def test_wizard_constellation_capability_cannot_claim_false_availability() -> None:
    with pytest.raises(ValidationError, match="default propagator must be runtime-supported"):
        WizardConstellationCapability(
            source_kind="constellation",
            runtime_supported_propagators=("two_body",),
            default_propagator="j2_mean_elements",
            unavailable_reason=None,
        )

    with pytest.raises(ValidationError, match="unavailable source must explain"):
        WizardConstellationCapability(
            source_kind="constellation",
            runtime_supported_propagators=(),
            default_propagator=None,
            unavailable_reason=None,
        )


def test_wizard_walker_geometry_requires_multiple_planes() -> None:
    with pytest.raises(ValidationError, match="greater than or equal to 2"):
        WizardConstellationGeometry(
            display_name="single-plane walker",
            description="invalid Wizard geometry",
            altitude_km=550,
            inclination_deg=53,
            pattern="walker_delta",
            planes=1,
            slots_per_plane=12,
            raan_spacing_deg=0,
            phase_offset_deg=0,
        )


def test_successful_canonical_responses_reuse_configuration_schemas() -> None:
    document = _session_document()
    assert document.canonical_json == SESSION_DOCUMENT

    for mode in ("validation", "serialization"):
        schema = BuilderCatalogDocument.model_json_schema(mode=mode)
        assert schema["properties"]["canonical_json"] == {
            "$ref": "#/$defs/ValidatedConfigurationJson"
        }
        assert schema["$defs"]["ValidatedConfigurationJson"] == {
            "$ref": "#/$defs/ConfigurationDocument"
        }
        assert schema["$defs"]["ConfigurationDocument"]["anyOf"]

        session_schema = BuilderCompileResult.model_json_schema(mode=mode)
        assert {
            tuple(item.items())
            for item in session_schema["properties"]["canonical_session_json"]["anyOf"]
        } == {
            (("$ref", "#/$defs/ValidatedSessionJson"),),
            (("type", "null"),),
        }
        assert session_schema["$defs"]["ValidatedSessionJson"] == {
            "$ref": "#/$defs/SegmentSessionConfig"
        }
        assert session_schema["$defs"]["SegmentSessionConfig"]["properties"]

        component_schema = CatalogDraftCompileResult.model_json_schema(mode=mode)
        assert {
            tuple(item.items())
            for item in component_schema["properties"]["canonical_json"]["anyOf"]
        } == {
            (("$ref", "#/$defs/ValidatedConfigurationJson"),),
            (("type", "null"),),
        }
        assert component_schema["$defs"]["ValidatedConfigurationJson"] == {
            "$ref": "#/$defs/ConfigurationDocument"
        }

    with pytest.raises(ValidationError):
        BuilderCatalogDocument(
            ref="user:sessions/demo.yaml",
            family="sessions",
            canonical_yaml=SESSION_YAML,
            canonical_json=["a document root must be a mapping"],
            content_digest=DOCUMENT_DIGEST,
            revision="session-rev-2",
        )

    with pytest.raises(ValidationError):
        BuilderCatalogDocument(
            ref="user:sessions/demo.yaml",
            family="sessions",
            canonical_yaml=SESSION_YAML,
            canonical_json={
                "session": {"name": "demo"},
                "segments": [{"id": "leo", "source": {"constellation": {}}}],
            },
            content_digest=DOCUMENT_DIGEST,
            revision="session-rev-2",
        )

    with pytest.raises(ValidationError):
        BuilderDraftEnvelope(draft_revision=0, state=["draft root must be a mapping"])

    incomplete = BuilderDraftEnvelope(draft_revision=0, state={"session": {}})
    assert incomplete.state.session == {}
    draft_schema = BuilderDraftState.model_json_schema()
    assert draft_schema["properties"]["session"] == {
        "additionalProperties": {"$ref": "#/$defs/JsonValue"},
        "title": "Session",
        "type": "object",
    }


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


def test_draft_contract_version_and_digest_format_are_closed() -> None:
    with pytest.raises(ValidationError, match="Input should be 1"):
        BuilderDraftEnvelope(
            contract_version=2,
            draft_revision=0,
            state={"session": {}},
        )

    with pytest.raises(ValidationError, match="String should match pattern"):
        BuilderDigests(
            document="not-a-digest",
            dependency=DEPENDENCY_DIGEST,
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
        resolved_preview=builder_world_preview("demo"),
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


def test_generated_builder_api_types_are_fresh() -> None:
    completed = subprocess.run(
        [sys.executable, "scripts/gen_builder_api_types.py", "--check"],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr
