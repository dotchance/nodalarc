"""Pending rule, domain and boundary edits, applied and saved on every shipped session.

Each object is edited the way the page edits it: through its card when the
listing offers one, and through its field editor otherwise. Every request is
JSON validated into its model, as the page sends it.
"""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest
import yaml
from nodalarc.catalog_repository import CatalogScope
from nodalarc.filesystem_catalog_repository import FilesystemCatalogRepository
from nodalarc.models.builder_api import (
    BuilderCompileRequest,
    BuilderCompileResult,
    BuilderSessionSaveRequest,
)
from nodalarc.models.builder_visual_api import (
    BuilderVisualCustomizeChainRequest,
    BuilderVisualDraftApplyYamlRequest,
    BuilderVisualDraftAssemblyResult,
    BuilderVisualDraftCompileRequest,
    BuilderVisualDraftCreateRequest,
    BuilderVisualDraftEnvelope,
    BuilderVisualDraftOpenRequest,
    BuilderVisualLinkRuleListing,
    BuilderVisualObjectEditResult,
    BuilderVisualRoutingBoundaryListing,
    BuilderVisualRoutingDomainListing,
    BuilderVisualSessionListing,
)
from pydantic import BaseModel
from vs_api.builder_compiler import canonicalize_persisted_configuration, compile_builder_draft
from vs_api.builder_session_service import save_builder_session
from vs_api.builder_visual_draft import BuilderVisualDraftService
from vs_api.catalog_context import CatalogContext

from tests.unit.authoring.test_builder_visual_draft import (
    _BOUNDARIES_ADDRESS,
    SHIPPED_ROOT,
    SHIPPED_SESSIONS,
    _accept_state,
    _apply_edits,
    _boundary_overlay_session,
    _content_edit,
    _edited_control,
    _member,
    _object_edit,
    _present_branch,
    _stored_state,
)

# The boundary overlay holds two boundaries a card writes back and one it does not.
BOUNDARY_OVERLAY = "boundary-overlay"
SOURCES = (*(path.stem for path in SHIPPED_SESSIONS), BOUNDARY_OVERLAY)
REACHABILITY = "earth-leo-heo-geo-luna-reachability"
RANGE = {"max_range_km": 12345.0}
TIMERS = {"hello_interval_s": 2, "hold_interval_s": 7}


@pytest.fixture()
def context(tmp_path: Path) -> CatalogContext:
    scope = CatalogScope()
    repository = FilesystemCatalogRepository(
        shipped_root=SHIPPED_ROOT,
        scope_roots={scope: tmp_path / "user-catalog"},
    )
    return CatalogContext(repository=repository, scope=scope)


@pytest.fixture()
def service(context: CatalogContext) -> BuilderVisualDraftService:
    return BuilderVisualDraftService(context)


def _json(model: BaseModel) -> Any:
    return json.loads(model.model_dump_json())


def _stored_session(source: str) -> dict[str, Any]:
    """The session document as it is stored, before the Builder reads it."""
    if source != BOUNDARY_OVERLAY:
        return yaml.safe_load((SHIPPED_ROOT / f"sessions/{source}.yaml").read_bytes())
    session = _boundary_overlay_session()
    # Timers apply to link-state domains only, so the overlay's four domains run
    # IS-IS here. The card writes each of them back unchanged.
    for domain in session["routing"]["domains"]:
        domain["protocol"] = "isis"
        domain["area_assignment"] = {"strategy": "flat"}
    return session


def _accepted_draft(service: BuilderVisualDraftService, source: str) -> BuilderVisualDraftEnvelope:
    """A draft whose accepted session is the stored one."""
    if source != BOUNDARY_OVERLAY:
        return service.open(
            BuilderVisualDraftOpenRequest.model_validate_json(
                json.dumps({"source_ref": f"nodalarc:sessions/{source}.yaml"})
            )
        )
    created = service.create(
        BuilderVisualDraftCreateRequest.model_validate_json(json.dumps({"session_name": source}))
    )
    applied = service.apply_yaml(
        BuilderVisualDraftApplyYamlRequest.model_validate_json(
            json.dumps(
                {
                    "draft": _json(created),
                    "expected_draft_revision": created.draft_revision,
                    "buffer_generation": 1,
                    "yaml_text": yaml.safe_dump(_stored_session(source), sort_keys=False),
                }
            )
        )
    )
    assert applied.applied is True, applied.issues
    return applied.draft


def _listing(
    service: BuilderVisualDraftService,
    draft: BuilderVisualDraftEnvelope,
) -> BuilderVisualSessionListing:
    """The listing the page reads its rows and editors from."""
    compiled = service.compile(
        BuilderVisualDraftCompileRequest.model_validate_json(json.dumps({"draft": _json(draft)})),
        available_node_count=1_000_000,
    )
    assert compiled.listing is not None
    return compiled.listing


def _set_scalar(
    answer: BuilderVisualObjectEditResult,
    pointer: str,
    value: Any,
) -> dict[str, Any]:
    return {
        "operation": "set_scalar",
        "control_id": _edited_control(answer, pointer, "scalar")["control_id"],
        "value": value,
    }


def _made_present(answer: BuilderVisualObjectEditResult, pointer: str) -> list[dict[str, Any]]:
    """The command that adds an absent optional field, or none when the field is present."""
    choice = _edited_control(answer, pointer, "choice")
    if choice["present"]:
        return []
    return [
        {
            "operation": "select_choice",
            "control_id": choice["control_id"],
            "branch_id": _present_branch(choice),
        }
    ]


def _member_edit(
    service: BuilderVisualDraftService,
    draft: BuilderVisualDraftEnvelope,
    listed: BuilderVisualLinkRuleListing | BuilderVisualRoutingDomainListing,
    *,
    field: str,
    values: dict[str, Any],
) -> dict[str, Any]:
    """A pending edit of one rule or domain that sets ``values`` inside its ``field``.

    The card's own fields carry the same names, so a card edit sets them on the card.
    """
    assert draft.applied_session is not None and draft.applied_revision is not None
    if isinstance(listed, BuilderVisualLinkRuleListing):
        list_pointer, member_id, card_key = "/link_rules", listed.rule_id, "rule"
    else:
        list_pointer, member_id, card_key = "/routing/domains", listed.domain_id, "domain"
    base = _member(draft.applied_session, list_pointer, member_id)
    address = {"kind": "member", "list_pointer": list_pointer, "id": member_id}

    def edit(working: Any, change: dict[str, Any] | None = None) -> BuilderVisualObjectEditResult:
        return _object_edit(
            service,
            target_ref=draft.target_ref,
            applied_revision=draft.applied_revision,
            address=address,
            working=working,
            change=change,
        )

    described = edit(base)
    # The card opens with the editor the listing named.
    card = None if described.widget is None else getattr(described.widget, card_key)
    assert card == listed.widget
    if described.widget is not None:
        change = described.widget.model_dump(mode="json")
        change[card_key].update(values)
    else:
        change = {
            "kind": "controls",
            "commands": [
                *_made_present(described, f"/{field}"),
                *(
                    _set_scalar(described, f"/{field}/{name}", value)
                    for name, value in values.items()
                ),
            ],
        }
    edited = edit(described.working, change)
    assert edited.issues == ()
    return _content_edit(address, base, edited.working)


def _boundaries_edit(
    service: BuilderVisualDraftService,
    draft: BuilderVisualDraftEnvelope,
    listed: tuple[BuilderVisualRoutingBoundaryListing, ...],
    *,
    export_node_loopbacks: bool,
) -> dict[str, Any]:
    """A pending edit of the boundary list that sets ``export_node_loopbacks`` on every export.

    A boundary with a card is edited on its card, and every other one in the field editor.
    """
    assert draft.applied_session is not None and draft.applied_revision is not None
    applied_revision = draft.applied_revision
    base = draft.applied_session["routing"]["boundaries"]

    def edit(working: Any, change: dict[str, Any] | None = None) -> BuilderVisualObjectEditResult:
        return _object_edit(
            service,
            target_ref=draft.target_ref,
            applied_revision=applied_revision,
            address=_BOUNDARIES_ADDRESS,
            working=working,
            change=change,
        )

    described = edit(base)
    assert described.widget is not None and described.widget.kind == "routing_boundaries"
    # Each boundary opens with the editor the listing named.
    assert described.widget.boundaries == tuple(boundary.widget for boundary in listed)
    edited = described
    if any(card is not None for card in described.widget.boundaries):
        cards = described.widget.model_dump(mode="json")
        for card in cards["boundaries"]:
            if card is not None:
                card["export_node_loopbacks"] = export_node_loopbacks
        edited = edit(edited.working, cards)
        assert edited.issues == ()
    commands = [
        _set_scalar(
            edited,
            f"/boundaries/{index}/export/{export}/export_node_loopbacks",
            export_node_loopbacks,
        )
        for index, card in enumerate(described.widget.boundaries)
        if card is None
        for export in range(len(base[index]["export"]))
    ]
    if commands:
        edited = edit(edited.working, {"kind": "controls", "commands": commands})
        assert edited.issues == ()
    return _content_edit(_BOUNDARIES_ADDRESS, base, edited.working)


def _with_the_same_edits(stored: dict[str, Any]) -> dict[str, Any]:
    """The stored session with every edit the acceptance test makes, written by hand."""
    edited = deepcopy(stored)
    for rule in edited.get("link_rules", ()):
        rule["constraints"] = {**rule.get("constraints", {}), **RANGE}
    routing = edited.get("routing", {})
    for domain in routing.get("domains", ()):
        domain["timers"] = {**domain.get("timers", {}), **TIMERS}
    for boundary in routing.get("boundaries", ()):
        for export in boundary["export"]:
            export["export_node_loopbacks"] = False
    return edited


def _preview_with(
    context: CatalogContext,
    accepted: BuilderVisualDraftAssemblyResult,
    pending: list[dict[str, Any]],
) -> BuilderCompileResult:
    """The page's preview: the accepted session compiled with the pending edits, accepting nothing."""
    return compile_builder_draft(
        BuilderCompileRequest.model_validate_json(
            json.dumps(
                {
                    "draft": _json(accepted.assembled_draft),
                    "target_ref": accepted.visual_draft.target_ref,
                    "pending_edits": pending,
                }
            )
        ),
        context.repository.snapshot(context.scope),
        available_node_count=1_000_000,
    )


@pytest.mark.parametrize("source", SOURCES)
def test_pending_edits_to_every_rule_domain_and_boundary_save_as_the_edited_session(
    source: str,
    context: CatalogContext,
    service: BuilderVisualDraftService,
) -> None:
    draft = _accepted_draft(service, source)
    session = draft.applied_session
    assert session is not None
    listing = _listing(service, draft)
    # The listing names every rule, domain and boundary the session declares, so
    # each one is edited below.
    routing = session.get("routing", {})
    assert [rule.rule_id for rule in listing.link_rules] == [
        rule["id"] for rule in session.get("link_rules", ())
    ]
    assert [domain.domain_id for domain in listing.routing_domains] == [
        domain["id"] for domain in routing.get("domains", ())
    ]
    assert len(listing.boundaries) == len(routing.get("boundaries", ()))
    if source == BOUNDARY_OVERLAY:
        # The overlay reaches the domain and boundary cards no shipped session reaches.
        assert all(domain.widget is not None for domain in listing.routing_domains)
        assert [boundary.widget is not None for boundary in listing.boundaries] == [
            False,
            True,
            True,
        ]

    edits = [
        *(
            _member_edit(service, draft, rule, field="constraints", values=RANGE)
            for rule in listing.link_rules
        ),
        *(
            _member_edit(service, draft, domain, field="timers", values=TIMERS)
            for domain in listing.routing_domains
        ),
    ]
    if listing.boundaries:
        edits.append(
            _boundaries_edit(service, draft, listing.boundaries, export_node_loopbacks=False)
        )
    # A stored value equal to the edit's would hide that edit from the comparison below.
    assert all(edit["working"] != edit["base"] for edit in edits)

    result = _apply_edits(service, draft, edits)

    expected = canonicalize_persisted_configuration(
        draft.target_ref,
        _with_the_same_edits(_stored_session(source)),
    )
    assert result.applied is True, result.outcomes
    assert result.assembly is not None
    accepted = result.assembly.visual_draft
    assert accepted.draft_revision == accepted.applied_revision == draft.draft_revision + 1
    assert accepted.applied_session == expected.canonical_json
    compiled = result.assembly.compile_result
    assert compiled.save_verdict.allowed is True, compiled.issues
    saved = save_builder_session(
        BuilderSessionSaveRequest.model_validate_json(
            result.assembly.save_request.model_dump_json()
        ),
        context,
        available_node_count=1_000_000,
    )
    assert saved.session.ref == draft.target_ref
    assert saved.session.canonical_json == expected.canonical_json


def test_an_incomplete_edit_refuses_its_batch_stays_pending_and_blocks_save(
    context: CatalogContext,
    service: BuilderVisualDraftService,
) -> None:
    draft = _accepted_draft(service, REACHABILITY)
    session = draft.applied_session
    assert session is not None and draft.applied_revision is not None
    listing = _listing(service, draft)
    luna = next(domain for domain in listing.routing_domains if domain.domain_id == "luna_domain")
    timed = _member_edit(service, draft, luna, field="timers", values=TIMERS)

    # No card writes geo_access back, so its topology is removed in the field editor.
    rule = _member(session, "/link_rules", "geo_access")
    address = {"kind": "member", "list_pointer": "/link_rules", "id": "geo_access"}
    described = _object_edit(
        service,
        target_ref=draft.target_ref,
        applied_revision=draft.applied_revision,
        address=address,
        working=rule,
    )
    assert described.widget is None
    untopologized = _object_edit(
        service,
        target_ref=draft.target_ref,
        applied_revision=draft.applied_revision,
        address=address,
        working=described.working,
        change={
            "kind": "controls",
            "commands": [
                {
                    "operation": "set_present",
                    "control_id": _edited_control(described, "/topology", "choice")["control_id"],
                    "present": False,
                }
            ],
        },
    )
    assert [(issue.code, issue.json_pointer) for issue in untopologized.issues] == [
        ("builder.structural.missing", "/topology")
    ]
    incomplete = _content_edit(address, rule, untopologized.working)
    position = [item["id"] for item in session["link_rules"]].index("geo_access")
    pointer = f"/link_rules/{position}/topology"

    refused = _apply_edits(service, draft, [timed, incomplete])

    # Nothing is accepted, and the refusal names the missing field.
    assert refused.applied is False
    assert refused.assembly is None
    assert [outcome.status for outcome in refused.outcomes] == ["applied", "invalid"]
    assert [
        (issue.code, issue.draft_path, issue.json_pointer) for issue in refused.outcomes[1].issues
    ] == [("builder.structural.missing", "pending_edits.1", pointer)]

    # The complete edit is accepted on its own at the next revision.
    applied = _apply_edits(service, draft, [timed])
    assert applied.applied is True
    assert applied.assembly is not None
    assert applied.assembly.visual_draft.draft_revision == draft.draft_revision + 1
    assert applied.assembly.compile_result.save_verdict.allowed is True

    # The incomplete edit still fits the accepted session. A preview holding it
    # shows the refusal where it lands, and the session cannot be saved.
    preview = _preview_with(context, applied.assembly, [incomplete])
    assert preview.draft == applied.assembly.assembled_draft
    (outcome,) = preview.pending_edit_outcomes
    assert outcome.status == "invalid"
    assert [issue.json_pointer for issue in outcome.issues] == [pointer]
    assert preview.save_verdict.allowed is False
    assert pointer in {issue.json_pointer for issue in preview.save_verdict.blockers}


def test_an_edit_begun_before_an_undo_that_changed_its_object_is_stale(
    context: CatalogContext,
    service: BuilderVisualDraftService,
) -> None:
    opened = _accepted_draft(service, REACHABILITY)
    assert opened.applied_session is not None
    listing = _listing(service, opened)
    rules = {rule.rule_id: rule for rule in listing.link_rules}
    luna = next(domain for domain in listing.routing_domains if domain.domain_id == "luna_domain")
    changed = _apply_edits(
        service,
        opened,
        [
            _member_edit(service, opened, rules["leo_a_access"], field="constraints", values=RANGE),
            _member_edit(service, opened, luna, field="timers", values=TIMERS),
            _boundaries_edit(service, opened, listing.boundaries, export_node_loopbacks=False),
        ],
    )
    assert changed.applied is True
    assert changed.assembly is not None and changed.assembly.listing is not None
    revised = changed.assembly.visual_draft
    revised_listing = changed.assembly.listing
    revised_rules = {rule.rule_id: rule for rule in revised_listing.link_rules}
    revised_luna = next(
        domain for domain in revised_listing.routing_domains if domain.domain_id == "luna_domain"
    )

    # Edits begun on the changed revision. The first three address objects the
    # change touched; heo_access is one it left alone.
    further_range = {"max_range_km": 23456.0}
    pending = [
        _member_edit(
            service,
            revised,
            revised_rules["leo_a_access"],
            field="constraints",
            values=further_range,
        ),
        _member_edit(
            service,
            revised,
            revised_luna,
            field="timers",
            values={"hello_interval_s": 3, "hold_interval_s": 9},
        ),
        # This edit asks for the value the undo restores. Its base is the changed
        # list, which the undo replaced, so it is stale all the same.
        _boundaries_edit(service, revised, revised_listing.boundaries, export_node_loopbacks=True),
        _member_edit(
            service, revised, revised_rules["heo_access"], field="constraints", values=further_range
        ),
    ]

    # Undo re-accepts the state the change replaced as the next revision.
    undone = _accept_state(service, revised, _stored_state(opened))
    reverted = undone.visual_draft
    assert reverted.draft_revision == revised.draft_revision + 1
    assert reverted.applied_session == opened.applied_session

    refused = _apply_edits(service, reverted, pending)

    assert refused.applied is False
    assert refused.assembly is None
    assert [outcome.status for outcome in refused.outcomes] == [
        "stale",
        "stale",
        "stale",
        "applied",
    ]
    rule_ids = [rule["id"] for rule in opened.applied_session["link_rules"]]
    domain_ids = [domain["id"] for domain in opened.applied_session["routing"]["domains"]]
    assert [
        (issue.code, issue.draft_path, issue.json_pointer)
        for outcome in refused.outcomes[:3]
        for issue in outcome.issues
    ] == [
        (
            "builder.staleness.pending_edit",
            "pending_edits.0",
            f"/link_rules/{rule_ids.index('leo_a_access')}",
        ),
        (
            "builder.staleness.pending_edit",
            "pending_edits.1",
            f"/routing/domains/{domain_ids.index('luna_domain')}",
        ),
        ("builder.staleness.pending_edit", "pending_edits.2", "/routing/boundaries"),
    ]

    # A preview leaves the stale edits out, compiles the one that still fits,
    # and the stale edits block the save.
    preview = _preview_with(context, undone, pending)
    assert [outcome.status for outcome in preview.pending_edit_outcomes] == [
        "stale",
        "stale",
        "stale",
        "applied",
    ]
    stale_issues = {
        issue for outcome in preview.pending_edit_outcomes[:3] for issue in outcome.issues
    }
    assert stale_issues <= set(preview.save_verdict.blockers)
    assert preview.save_verdict.allowed is False
    expected = deepcopy(opened.applied_session)
    _member(expected, "/link_rules", "heo_access")["constraints"] = further_range
    assert preview.canonical_session_json == (
        canonicalize_persisted_configuration(opened.target_ref, expected).canonical_json
    )


def test_a_pending_edit_of_a_proposed_node_saves_with_the_session(
    context: CatalogContext,
    service: BuilderVisualDraftService,
) -> None:
    # The session proposes its own copy of the node, then edits it by ref.
    opened = _accepted_draft(service, "earth-leo-simple")
    customized = service.customize_chain(
        BuilderVisualCustomizeChainRequest(
            draft=opened,
            expected_draft_revision=opened.draft_revision,
            segment_id="leo",
            leaf_ref="nodalarc:nodes/space/starlink-v2-mesh.yaml",
        )
    )
    assert customized.applied is True, customized.issues
    draft = customized.draft
    node_ref = next(
        entry.target_ref for entry in customized.forked_chain if entry.target_ref.family == "nodes"
    )
    [proposal] = [item for item in draft.catalog_documents if item.ref == node_ref]
    working = deepcopy(proposal.document)
    working["node"]["display_name"] = "Edited by the session"

    result = _apply_edits(
        service,
        draft,
        [
            {
                "kind": "content",
                "address": {"kind": "proposal", "ref": str(node_ref)},
                "base": proposal.document,
                "working": working,
            }
        ],
    )

    assert result.applied is True, result.outcomes
    assert result.assembly is not None
    accepted = result.assembly.visual_draft
    [edited] = [item for item in accepted.catalog_documents if item.ref == node_ref]
    assert edited.document["node"]["display_name"] == "Edited by the session"
    assert result.assembly.compile_result.save_verdict.allowed is True
    saved = save_builder_session(
        BuilderSessionSaveRequest.model_validate_json(
            result.assembly.save_request.model_dump_json()
        ),
        context,
        available_node_count=1_000_000,
    )
    stored = yaml.safe_load(context.repository.snapshot(context.scope).get(node_ref).content)
    assert stored["node"]["display_name"] == "Edited by the session"
    assert saved.session.ref == draft.target_ref
