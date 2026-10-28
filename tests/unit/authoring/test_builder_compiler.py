"""Focused contracts for read-only Builder draft compilation."""

from __future__ import annotations

import json
from copy import deepcopy
from itertools import permutations
from pathlib import Path
from typing import Any

import pytest
import yaml
from nodalarc.catalog_closure import CatalogReadDocument
from nodalarc.catalog_refs import CatalogRef
from nodalarc.catalog_registry import validate_referenced_configuration_document
from nodalarc.catalog_repository import CatalogReadSnapshot, CatalogScope
from nodalarc.filesystem_catalog_repository import FilesystemCatalogRepository
from nodalarc.models.builder_api import (
    BuilderCompileRequest,
    BuilderContentEdit,
    BuilderDraftEnvelope,
    BuilderPendingEdits,
)
from nodalarc.models.builder_world import BuilderWorld
from nodalarc.resolve_session import SessionResolution, SessionResolutionError
from pydantic import TypeAdapter, ValidationError
from vs_api.builder_compiler import (
    apply_pending_edits,
    canonicalize_persisted_configuration,
    compile_builder_draft,
)

from tests.builder_world_fixtures import builder_world_preview, preview_from_resolution

ROOT = Path(__file__).resolve().parents[3]
SHIPPED_ROOT = ROOT / "catalog" / "nodalarc"
SIMPLE_SESSION = SHIPPED_ROOT / "sessions" / "earth-leo-simple.yaml"
QUIC_SESSION = SHIPPED_ROOT / "sessions" / "earth-luna-quic.yaml"
PENDING_EDITS = TypeAdapter(BuilderPendingEdits)
SHIPPED_CONSTELLATION = SHIPPED_ROOT / "constellations" / "earth" / "leo" / "earth-leo-ring-36.yaml"
SHIPPED_NODE = SHIPPED_ROOT / "nodes" / "space" / "starlink-v2-mesh.yaml"
SHIPPED_SESSIONS = tuple(sorted((SHIPPED_ROOT / "sessions").glob("*.yaml")))


def _load(path: Path) -> dict[str, Any]:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def snapshot(tmp_path_factory: pytest.TempPathFactory) -> CatalogReadSnapshot:
    scope = CatalogScope()
    repository = FilesystemCatalogRepository(
        shipped_root=SHIPPED_ROOT,
        scope_roots={scope: tmp_path_factory.mktemp("builder-compiler-user")},
    )
    return repository.snapshot(scope)


ORBIT_REF = CatalogRef("nodalarc:orbits/earth/leo/earth-leo-starlink.yaml")


class _MutatingSnapshot(CatalogReadSnapshot):
    """Pinned snapshot whose orbit bytes change after the first read."""

    def __init__(self, base: CatalogReadSnapshot, altered: bytes) -> None:
        self._base = base
        self._altered = altered
        self.reads = 0

    @property
    def scope(self):
        return self._base.scope

    @property
    def generation(self):
        return self._base.generation

    def read_bytes(self, ref):
        return self._base.read_bytes(ref)

    def read(self, ref: CatalogRef) -> CatalogReadDocument:
        document = self._base.read(ref)
        if ref != ORBIT_REF:
            return document
        self.reads += 1
        if self.reads == 1:
            return document
        return CatalogReadDocument(
            family=document.family,
            preserved_path=document.preserved_path,
            yaml_bytes=self._altered,
        )

    def get(self, ref):
        return self._base.get(ref)

    def list(self, *args, **kwargs):
        return self._base.list(*args, **kwargs)

    def export_documents(self, *args, **kwargs):
        return self._base.export_documents(*args, **kwargs)


def _request(
    session: dict[str, Any],
    *,
    catalog_documents: list[dict[str, Any]] | None = None,
) -> BuilderCompileRequest:
    name = session.get("session", {}).get("name", "incomplete")
    return BuilderCompileRequest(
        draft=BuilderDraftEnvelope(
            draft_revision=1,
            state={
                "session": session,
                "catalog_documents": [
                    {**document, "origin": document.get("origin", "generated")}
                    for document in (catalog_documents or [])
                ],
            },
        ),
        target_ref=f"user:sessions/{name}.yaml",
    )


def _compile(
    request: BuilderCompileRequest,
    snapshot: CatalogReadSnapshot,
):
    return compile_builder_draft(
        request,
        snapshot,
        available_node_count=1_000_000,
        preview_factory=preview_from_resolution,
    )


def _deep_user_draft(*, node_display_name: str = "User spacecraft"):
    session = deepcopy(_load(SIMPLE_SESSION))
    session["session"]["name"] = "deep-user"
    session["segments"][0]["source"] = "user:constellations/user-ring.yaml"

    constellation = deepcopy(_load(SHIPPED_CONSTELLATION))
    constellation["constellation"]["id"] = "user-ring"
    constellation["constellation"]["node"] = "user:nodes/user-spacecraft.yaml"

    node = deepcopy(_load(SHIPPED_NODE))
    node["node"]["id"] = "user-spacecraft"
    node["node"]["display_name"] = node_display_name
    proposals = [
        {
            "ref": "user:constellations/user-ring.yaml",
            "document": constellation,
        },
        {
            "ref": "user:nodes/user-spacecraft.yaml",
            "document": node,
        },
    ]
    return session, proposals


@pytest.mark.parametrize(
    ("ref", "document"),
    (
        (
            CatalogRef("user:sessions/wrong-session.yaml"),
            _load(SIMPLE_SESSION),
        ),
        (
            CatalogRef("user:nodes/wrong-node.yaml"),
            _load(SHIPPED_NODE),
        ),
    ),
)
def test_canonicalizer_uses_shared_referenced_document_identity_contract(
    ref: CatalogRef,
    document: dict[str, Any],
) -> None:
    with pytest.raises(ValueError) as authority_error:
        validate_referenced_configuration_document(ref, document)

    with pytest.raises(ValueError) as canonicalizer_error:
        canonicalize_persisted_configuration(ref, document)

    assert str(canonicalizer_error.value) == str(authority_error.value)


def test_shipped_simple_no_op_compile_is_saveable_and_deployable(
    snapshot: CatalogReadSnapshot,
) -> None:
    before_generation = snapshot.generation
    before_user_documents = snapshot.list(namespace="user")

    result = _compile(_request(_load(SIMPLE_SESSION)), snapshot)

    assert result.save_verdict.allowed is True
    assert result.deploy_eligibility_after_save.allowed is True
    assert result.canonical_session_yaml is not None
    assert result.canonical_session_json is not None
    assert result.dependency_closure is not None
    assert result.dependency_closure.file_count > 0
    assert all(entry.ref.startswith("nodalarc:") for entry in result.dependency_closure.entries)
    assert all(
        entry.preserved_path
        == f"catalog/{entry.ref.namespace}/{entry.ref.relative_path.as_posix()}"
        for entry in result.dependency_closure.entries
    )
    assert result.digests is not None
    assert result.digests.resolved_semantic is not None
    assert result.resolved_preview == builder_world_preview("earth-leo-simple")
    assert snapshot.generation == before_generation
    assert snapshot.list(namespace="user") == before_user_documents


@pytest.mark.parametrize("session_path", SHIPPED_SESSIONS, ids=lambda path: path.stem)
def test_every_shipped_session_no_op_compiles_without_semantic_loss(
    snapshot: CatalogReadSnapshot,
    session_path: Path,
) -> None:
    raw = _load(session_path)

    first = _compile(_request(raw), snapshot)

    assert first.save_verdict.allowed is True
    assert first.deploy_eligibility_after_save.allowed is True
    assert first.canonical_session_json is not None
    assert first.canonical_session_yaml is not None
    assert first.dependency_closure is not None
    assert first.digests is not None

    reopened = _compile(_request(first.canonical_session_json), snapshot)
    assert reopened.save_verdict.allowed is True
    assert reopened.deploy_eligibility_after_save.allowed is True
    assert reopened.canonical_session_json == first.canonical_session_json
    assert reopened.canonical_session_yaml == first.canonical_session_yaml
    assert reopened.digests == first.digests


def test_default_preview_uses_the_existing_ome_builder_world(
    snapshot: CatalogReadSnapshot,
) -> None:
    result = compile_builder_draft(
        _request(_load(SIMPLE_SESSION)),
        snapshot,
        available_node_count=1_000_000,
    )

    assert result.save_verdict.allowed is True
    assert result.deploy_eligibility_after_save.allowed is True
    assert isinstance(result.resolved_preview, BuilderWorld)
    assert result.resolved_preview.session.name == "earth-leo-simple"
    assert result.resolved_preview.nodes


def test_direct_and_deep_user_refs_compile_without_flattening(
    snapshot: CatalogReadSnapshot,
) -> None:
    session, proposals = _deep_user_draft()

    result = _compile(_request(session, catalog_documents=proposals), snapshot)

    assert result.save_verdict.allowed is True
    assert result.canonical_session_json is not None
    assert result.canonical_session_json["segments"][0]["source"] == (
        "user:constellations/user-ring.yaml"
    )
    assert result.dependency_closure is not None
    entries = {str(entry.ref): entry for entry in result.dependency_closure.entries}
    assert "user:constellations/user-ring.yaml" in entries
    assert "user:nodes/user-spacecraft.yaml" in entries
    assert entries["user:constellations/user-ring.yaml"].revision is None
    assert entries["user:nodes/user-spacecraft.yaml"].revision is None


def test_deep_dependency_content_changes_dependency_digest(
    snapshot: CatalogReadSnapshot,
) -> None:
    first_session, first_proposals = _deep_user_draft(node_display_name="First")
    second_session, second_proposals = _deep_user_draft(node_display_name="Second")

    first = _compile(_request(first_session, catalog_documents=first_proposals), snapshot)
    second = _compile(_request(second_session, catalog_documents=second_proposals), snapshot)

    assert first.digests is not None
    assert second.digests is not None
    assert first.digests.document == second.digests.document
    assert first.digests.dependency != second.digests.dependency


def test_literal_user_reference_prose_is_not_a_dependency(
    snapshot: CatalogReadSnapshot,
) -> None:
    body = {
        "body": {
            "id": "prose-body",
            "display_name": "Prose body",
            "gravitational_parameter_km3_s2": 398600.4418,
            "mean_radius_km": 6371.0088,
            "equatorial_radius_km": 6378.137,
            "polar_radius_km": 6356.752,
            "reference": "urn:nodalarc:test",
            "notes": "This prose names user:nodes/missing.yaml but is not a ref slot.",
        }
    }
    result = _compile(
        _request(
            _load(SIMPLE_SESSION),
            catalog_documents=[{"ref": "user:bodies/prose-body.yaml", "document": body}],
        ),
        snapshot,
    )

    assert result.save_verdict.allowed is True
    assert all(issue.stage != "reference" for issue in result.issues)


def test_invalid_stale_orphan_proposal_is_excluded_without_blocking(
    snapshot: CatalogReadSnapshot,
) -> None:
    orphan_ref = "user:nodes/orphan-spacecraft.yaml"
    result = _compile(
        _request(
            _load(SIMPLE_SESSION),
            catalog_documents=[
                {
                    "ref": orphan_ref,
                    "document": {"node": {"id": "orphan-spacecraft", "unknown": True}},
                }
            ],
        ),
        snapshot,
    )

    assert result.save_verdict.allowed is True
    assert result.draft.state.catalog_documents == ()
    warning = next(
        issue
        for issue in result.issues
        if issue.code == "builder.draft.unreferenced_catalog_documents"
    )
    assert warning.severity == "warning"
    assert warning.blocks == ()
    assert warning.related_refs == (orphan_ref,)
    assert all(issue.stage not in {"structural", "staleness"} for issue in result.issues)


def test_invalid_reachable_proposal_blocks_with_typed_issues(
    snapshot: CatalogReadSnapshot,
) -> None:
    session, proposals = _deep_user_draft()
    proposals[1]["document"] = {"node": {"id": "user-spacecraft", "unknown": True}}

    result = _compile(_request(session, catalog_documents=proposals), snapshot)

    assert result.save_verdict.allowed is False
    assert any(issue.stage == "structural" for issue in result.issues)
    assert not any(issue.stage == "staleness" for issue in result.issues)
    assert not any(
        issue.code == "builder.draft.unreferenced_catalog_documents" for issue in result.issues
    )


def test_incomplete_inner_session_returns_structural_issues(
    snapshot: CatalogReadSnapshot,
) -> None:
    request = _request({"session": {"name": "incomplete"}})

    result = _compile(request, snapshot)

    assert result.save_verdict.allowed is False
    assert result.deploy_eligibility_after_save.allowed is False
    assert result.dependency_closure is None
    assert any(issue.stage == "structural" for issue in result.issues)
    assert all("save" in issue.blocks for issue in result.issues)


def test_dangling_reference_and_semantic_failure_have_distinct_stages(
    snapshot: CatalogReadSnapshot,
) -> None:
    dangling = deepcopy(_load(SIMPLE_SESSION))
    dangling["session"]["name"] = "dangling"
    dangling["segments"][0]["source"] = "user:constellations/missing.yaml"
    dangling_result = _compile(_request(dangling), snapshot)
    assert dangling_result.save_verdict.allowed is False
    assert {issue.stage for issue in dangling_result.issues} == {"reference"}

    semantic = deepcopy(_load(SIMPLE_SESSION))
    semantic["session"]["name"] = "semantic-invalid"
    semantic["link_rules"][0]["endpoints"][1]["select"] = {"segment": "missing"}
    semantic_result = _compile(_request(semantic), snapshot)
    assert semantic_result.save_verdict.allowed is False
    assert any(issue.stage == "semantic" for issue in semantic_result.issues)


def test_runtime_unsupported_and_readiness_failures_block_only_deploy(
    snapshot: CatalogReadSnapshot,
) -> None:
    unsupported = deepcopy(_load(SIMPLE_SESSION))
    unsupported["session"]["name"] = "unsupported-affine"
    unsupported["segments"][0]["clock"] = {"model": "affine", "rate": 2.0}
    unsupported_result = _compile(_request(unsupported), snapshot)
    assert unsupported_result.save_verdict.allowed is True
    assert unsupported_result.deploy_eligibility_after_save.allowed is False
    runtime_issues = [
        issue for issue in unsupported_result.issues if issue.stage == "runtime_support"
    ]
    assert runtime_issues
    assert all(issue.blocks == ("deploy",) for issue in runtime_issues)

    ground_only = deepcopy(_load(SIMPLE_SESSION))
    ground_only["session"]["name"] = "ground-only"
    ground_only["segments"] = [
        segment for segment in ground_only["segments"] if segment["id"] == "ground"
    ]
    ground_only.pop("link_rules", None)
    ground_only.pop("simulation", None)
    readiness_result = _compile(_request(ground_only), snapshot)
    assert readiness_result.save_verdict.allowed is True
    assert readiness_result.deploy_eligibility_after_save.allowed is False
    readiness_issues = [issue for issue in readiness_result.issues if issue.stage == "readiness"]
    assert any(issue.code == "builder.readiness.no_satellites" for issue in readiness_issues)
    assert all("save" not in issue.blocks for issue in readiness_issues)


def test_canonical_compile_is_idempotent_and_does_not_write(
    snapshot: CatalogReadSnapshot,
) -> None:
    session, proposals = _deep_user_draft()
    first = _compile(_request(session, catalog_documents=proposals), snapshot)
    assert first.canonical_session_json is not None

    second = _compile(
        _request(first.canonical_session_json, catalog_documents=proposals),
        snapshot,
    )

    assert first.canonical_session_yaml == second.canonical_session_yaml
    assert first.canonical_session_json == second.canonical_session_json
    assert first.digests == second.digests
    assert snapshot.list(namespace="user") == ()


def test_expected_preview_failure_is_a_typed_deploy_blocker(
    snapshot: CatalogReadSnapshot,
) -> None:
    def unavailable(_resolution: SessionResolution):
        raise ValueError("preview physics unavailable")

    result = compile_builder_draft(
        _request(_load(SIMPLE_SESSION)),
        snapshot,
        available_node_count=1_000_000,
        preview_factory=unavailable,
    )

    assert result.save_verdict.allowed is True
    assert result.deploy_eligibility_after_save.allowed is False
    issue = next(
        issue for issue in result.issues if issue.code == "builder.readiness.preview_unavailable"
    )
    assert issue.stage == "readiness"
    assert issue.blocks == ("deploy",)


def test_inconsistent_preview_pair_blocks_save_and_deploy(
    snapshot: CatalogReadSnapshot,
) -> None:
    """The preview's fail-closed refusal is a semantic issue, unlike plain unavailability."""

    def inconsistent(_resolution: SessionResolution):
        raise SessionResolutionError(
            "link rule 'isl': allocated pair matches neither endpoint orientation"
        )

    result = compile_builder_draft(
        _request(_load(SIMPLE_SESSION)),
        snapshot,
        available_node_count=1_000_000,
        preview_factory=inconsistent,
    )

    assert result.save_verdict.allowed is False
    assert result.deploy_eligibility_after_save.allowed is False
    issue = next(
        issue for issue in result.issues if issue.code == "builder.semantic.preview_resolution"
    )
    assert issue.stage == "semantic"
    assert issue.blocks == ("save", "deploy")
    assert result.resolved_preview is None
    assert result.digests is not None


def test_compile_resolves_the_captured_closure_not_the_live_snapshot(
    snapshot: CatalogReadSnapshot,
) -> None:
    orbit = yaml.safe_load(snapshot.read(ORBIT_REF).yaml_bytes.decode("utf-8"))
    authored_altitude_km = orbit["orbit"]["shape"]["altitude_km"]
    orbit["orbit"]["shape"]["altitude_km"] = authored_altitude_km + 650
    live = _MutatingSnapshot(snapshot, yaml.safe_dump(orbit, sort_keys=False).encode("utf-8"))

    result = compile_builder_draft(
        _request(_load(SIMPLE_SESSION)),
        live,
        available_node_count=1_000_000,
    )

    assert live.reads == 1
    assert [issue for issue in result.issues if issue.severity == "error"] == []
    assert isinstance(result.resolved_preview, BuilderWorld)
    satellites = [node for node in result.resolved_preview.nodes if node.kind == "satellite"]
    assert satellites
    for node in satellites:
        assert node.epoch_position is not None
        assert abs(node.epoch_position.alt_km - authored_altitude_km) < 100


def _accepted(path: Path) -> dict[str, Any]:
    """A shipped session as the Builder holds it once accepted: canonical JSON."""
    ref = CatalogRef(f"user:sessions/{path.stem}.yaml")
    return canonicalize_persisted_configuration(ref, _load(path)).canonical_json


def _apply(session: dict[str, Any], *edits: dict[str, Any]):
    batch = PENDING_EDITS.validate_json(json.dumps(edits))
    return apply_pending_edits(session, batch, source_ref="user:sessions/earth-luna-quic.yaml")


def _member(session: dict[str, Any], list_pointer: str, object_id: str) -> dict[str, Any]:
    members = (
        session["link_rules"] if list_pointer == "/link_rules" else session["routing"]["domains"]
    )
    return next(member for member in members if member["id"] == object_id)


def _content(
    list_pointer: str,
    object_id: str,
    working: dict[str, Any],
    *,
    base: dict[str, Any] | None,
) -> dict[str, Any]:
    return {
        "kind": "content",
        "address": {"kind": "member", "list_pointer": list_pointer, "id": object_id},
        "base": base,
        "working": working,
    }


def _edited(
    session: dict[str, Any], list_pointer: str, object_id: str, **fields: Any
) -> dict[str, Any]:
    base = _member(session, list_pointer, object_id)
    return _content(list_pointer, object_id, {**deepcopy(base), **fields}, base=base)


def _created(session: dict[str, Any], list_pointer: str, template_id: str, object_id: str):
    working = {**deepcopy(_member(session, list_pointer, template_id)), "id": object_id}
    return _content(list_pointer, object_id, working, base=None)


def _boundaries(base: list[Any] | None, working: list[Any]) -> dict[str, Any]:
    return {
        "kind": "content",
        "address": {"kind": "whole_list", "pointer": "/routing/boundaries"},
        "base": base,
        "working": working,
    }


def _ids(session: dict[str, Any], list_pointer: str) -> list[str]:
    members = (
        session["link_rules"] if list_pointer == "/link_rules" else session["routing"]["domains"]
    )
    return [member["id"] for member in members]


def _removal(session: dict[str, Any], list_pointer: str, ids_wanted: list[str]):
    return {
        "kind": "list",
        "list_pointer": list_pointer,
        "ids_before": _ids(session, list_pointer),
        "ids_wanted": ids_wanted,
    }


def test_pending_edits_land_in_the_same_place_whatever_the_batch_order() -> None:
    session = _accepted(QUIC_SESSION)
    untouched = deepcopy(session)
    rule_ids = _ids(session, "/link_rules")
    kept = [rule_id for rule_id in reversed(rule_ids) if rule_id != "luna_access"]
    boundaries = deepcopy(session["routing"]["boundaries"])
    for export in boundaries[0]["export"]:
        export["export_node_loopbacks"] = False
    edits = (
        _removal(session, "/link_rules", kept),
        _edited(session, "/link_rules", "leo_isl", constraints={"max_range_km": 12345.0}),
        _created(session, "/link_rules", "geo_isl", "zz_isl"),
        _created(session, "/link_rules", "leo_isl", "aa_isl"),
        _edited(
            session,
            "/routing/domains",
            "luna_domain",
            timers={"hello_interval_s": 2, "hold_interval_s": 7},
        ),
        _boundaries(session["routing"]["boundaries"], boundaries),
    )

    candidates = []
    for order in permutations(range(len(edits))):
        candidate, outcomes = _apply(session, *(edits[index] for index in order))
        assert [outcome.status for outcome in outcomes] == ["applied"] * len(edits)
        candidates.append(candidate)

    candidate = candidates[0]
    assert all(other == candidate for other in candidates)
    assert session == untouched
    # The list edit orders the members it kept; new members follow it in id order.
    assert _ids(candidate, "/link_rules") == [*kept, "aa_isl", "zz_isl"]
    assert _member(candidate, "/link_rules", "leo_isl")["constraints"] == {"max_range_km": 12345.0}
    assert _member(candidate, "/routing/domains", "luna_domain")["timers"] == {
        "hello_interval_s": 2,
        "hold_interval_s": 7,
    }
    assert candidate["routing"]["boundaries"] == boundaries
    ref = CatalogRef("user:sessions/earth-luna-quic.yaml")
    canonical = canonicalize_persisted_configuration(ref, candidate).canonical_json
    assert _ids(canonical, "/link_rules") == _ids(candidate, "/link_rules")


def test_a_stale_base_leaves_its_edit_out_and_names_it() -> None:
    session = _accepted(QUIC_SESSION)
    leo_isl = _member(session, "/link_rules", "leo_isl")
    timers = {"hello_interval_s": 2, "hold_interval_s": 7}
    edits = (
        _content(
            "/link_rules",
            "leo_isl",
            {**leo_isl, "enabled": False},
            base={**leo_isl, "topology": {"mode": "visible_candidates"}},
        ),
        _content("/link_rules", "gone_isl", {"id": "gone_isl"}, base={"id": "gone_isl"}),
        _created(session, "/link_rules", "leo_isl", "geo_isl"),
        {
            "kind": "list",
            "list_pointer": "/routing/domains",
            "ids_before": ["luna_domain", "earth_domain"],
            "ids_wanted": ["luna_domain"],
        },
        _boundaries([], []),
        _edited(session, "/routing/domains", "earth_domain", timers=timers),
    )

    candidate, outcomes = _apply(session, *edits)

    expected = deepcopy(session)
    _member(expected, "/routing/domains", "earth_domain")["timers"] = timers
    assert candidate == expected
    assert [outcome.status for outcome in outcomes] == ["stale"] * 5 + ["applied"]
    messages = [outcome.issues[0].message for outcome in outcomes[:5]]
    assert messages == [
        "Link rule 'leo_isl' changed since the edit began",
        "Link rule 'gone_isl' no longer exists",
        "Link rule 'geo_isl' already exists",
        "The routing domain list changed since the edit began",
        "The routing boundary list changed since the edit began",
    ]
    pointers = [outcome.issues[0].json_pointer for outcome in outcomes[:5]]
    leo_isl_at = _ids(session, "/link_rules").index("leo_isl")
    assert pointers == [
        f"/link_rules/{leo_isl_at}",
        "/link_rules",
        f"/link_rules/{_ids(session, '/link_rules').index('geo_isl')}",
        "/routing/domains",
        "/routing/boundaries",
    ]
    for index, outcome in enumerate(outcomes[:5]):
        (issue,) = outcome.issues
        assert issue.code == "builder.staleness.pending_edit"
        assert issue.stage == "staleness"
        assert issue.blocks == ("save", "deploy")
        assert issue.draft_path == f"pending_edits.{index}"

    # A member a list edit in the same batch removes has nothing left to edit.
    kept = [rule_id for rule_id in _ids(session, "/link_rules") if rule_id != "leo_isl"]
    candidate, outcomes = _apply(
        session,
        _edited(session, "/link_rules", "leo_isl", enabled=False),
        _removal(session, "/link_rules", kept),
    )
    assert _ids(candidate, "/link_rules") == kept
    assert [outcome.status for outcome in outcomes] == ["stale", "applied"]
    assert outcomes[0].issues[0].message == (
        "Link rule 'leo_isl' is removed by a list edit in this batch"
    )


def test_an_incomplete_edit_is_written_and_named_where_it_lands() -> None:
    session = _accepted(QUIC_SESSION)
    working = {**deepcopy(_member(session, "/link_rules", "geo_isl")), "id": "new_isl"}
    del working["endpoints"][1]["select"]

    candidate, outcomes = _apply(
        session,
        _edited(session, "/link_rules", "leo_isl", enabled=False),
        _content("/link_rules", "new_isl", working, base=None),
    )

    assert _member(candidate, "/link_rules", "new_isl") == working
    assert _member(candidate, "/link_rules", "leo_isl")["enabled"] is False
    assert [outcome.status for outcome in outcomes] == ["applied", "invalid"]
    (issue,) = outcomes[1].issues
    new_isl_at = _ids(candidate, "/link_rules").index("new_isl")
    assert issue.json_pointer == f"/link_rules/{new_isl_at}/endpoints/1/select"
    assert issue.code == "builder.structural.missing"
    assert issue.draft_path == "pending_edits.1"
    assert issue.stage == "structural"
    assert issue.blocks == ("save", "deploy")


def test_removing_the_last_domain_drops_routing_only_once_no_boundary_remains() -> None:
    session = _accepted(QUIC_SESSION)
    no_domains = _removal(session, "/routing/domains", [])

    candidate, outcomes = _apply(session, no_domains)
    assert candidate["routing"] == {"domains": [], "boundaries": session["routing"]["boundaries"]}
    assert [outcome.status for outcome in outcomes] == ["invalid"]
    (issue,) = outcomes[0].issues
    assert issue.code == "builder.draft.routing_boundaries_remain"
    assert issue.json_pointer == "/routing/domains"
    assert issue.draft_path == "pending_edits.0"

    candidate, outcomes = _apply(
        session, _boundaries(session["routing"]["boundaries"], []), no_domains
    )
    assert "routing" not in candidate
    assert [outcome.status for outcome in outcomes] == ["applied", "applied"]
    ref = CatalogRef("user:sessions/earth-luna-quic.yaml")
    assert canonicalize_persisted_configuration(ref, candidate).canonical_json == candidate

    # A domain created in the same batch keeps the section and its boundaries.
    candidate, outcomes = _apply(
        session, no_domains, _created(session, "/routing/domains", "earth_domain", "solo")
    )
    assert _ids(candidate, "/routing/domains") == ["solo"]
    assert candidate["routing"]["boundaries"] == session["routing"]["boundaries"]
    assert [outcome.status for outcome in outcomes] == ["applied", "applied"]

    # Removing the last link rule removes that section too.
    candidate, outcomes = _apply(session, _removal(session, "/link_rules", []))
    assert "link_rules" not in candidate
    assert [outcome.status for outcome in outcomes] == ["applied"]


def test_the_first_domain_creates_a_routing_section_that_compiles(
    snapshot: CatalogReadSnapshot,
) -> None:
    session = _accepted(SIMPLE_SESSION)
    assert "routing" not in session
    domain = {
        "id": "earth_domain",
        "protocol": "isis",
        "selectors": [{"any": [{"segment": "leo"}, {"segment": "ground"}]}],
        "area_assignment": {"strategy": "flat"},
    }

    candidate, outcomes = _apply(
        session, _content("/routing/domains", "earth_domain", domain, base=None)
    )

    assert candidate["routing"] == {"domains": [domain]}
    assert [outcome.status for outcome in outcomes] == ["applied"]
    result = _compile(_request(candidate), snapshot)
    assert result.save_verdict.allowed is True
    assert result.canonical_session_json is not None
    assert result.canonical_session_json["routing"] == {"domains": [domain]}


def test_applying_refuses_a_batch_that_addresses_one_object_twice() -> None:
    session = _accepted(SIMPLE_SESSION)
    edit = BuilderContentEdit.model_validate(
        _edited(session, "/link_rules", "leo_isl", enabled=False)
    )

    with pytest.raises(ValidationError, match="one content edit per address"):
        apply_pending_edits(session, (edit, edit), source_ref="user:sessions/earth-leo-simple.yaml")
