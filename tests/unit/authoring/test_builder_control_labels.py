"""The Builder field editor names every control and option in grammar words."""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any, get_args

import pytest
from nodalarc.catalog_refs import CatalogRef
from nodalarc.catalog_repository import CatalogScope
from nodalarc.filesystem_catalog_repository import FilesystemCatalogRepository
from nodalarc.models.builder_catalog_api import (
    CatalogComponentFamily,
    CatalogDraftNewRequest,
    CatalogDraftOpenRequest,
)
from nodalarc.models.builder_controls_api import (
    BuilderChoiceControl,
    BuilderControl,
    BuilderControlTree,
)
from nodalarc.models.builder_visual_api import (
    BuilderVisualDraftEnvelope,
    BuilderVisualDraftOpenRequest,
    BuilderVisualObjectEditRequest,
)
from pydantic import BaseModel, ConfigDict
from vs_api.builder_catalog_draft import BuilderCatalogDraftService
from vs_api.builder_control_tree import BuilderControlLabelError, build_model_control_tree
from vs_api.builder_visual_draft import BuilderVisualDraftService
from vs_api.catalog_context import CatalogContext

from tests.support.builder_controls import control_at, walk_controls

ROOT = Path(__file__).resolve().parents[3]
SHIPPED_ROOT = ROOT / "catalog/nodalarc"
SHIPPED_SESSIONS = tuple(sorted((SHIPPED_ROOT / "sessions").glob("*.yaml")))
COMPONENT_FAMILIES: tuple[CatalogComponentFamily, ...] = get_args(CatalogComponentFamily.__value__)

_PYTHON_FRAGMENTS = ("builtins", "typing", "nodalarc.", "Literal[", "tuple[", "dict[")
_INNER_CAPITAL = re.compile(r"[a-z][A-Z]")
_PYTHON_SCALARS = frozenset({"Str", "Int", "Float", "Bool"})


def _context(tmp_path: Path) -> CatalogContext:
    scope = CatalogScope()
    return CatalogContext(
        repository=FilesystemCatalogRepository(
            shipped_root=SHIPPED_ROOT,
            scope_roots={scope: tmp_path / "user-catalog"},
        ),
        scope=scope,
    )


def _labels(control: BuilderControl) -> Iterator[str]:
    for nested in walk_controls(control):
        yield nested.label
        if isinstance(nested, BuilderChoiceControl):
            yield from (branch.label for branch in nested.branches)


def _python_names(tree: BuilderControlTree) -> set[str]:
    return {
        label
        for label in _labels(tree.root)
        if any(fragment in label for fragment in _PYTHON_FRAGMENTS)
        or _INNER_CAPITAL.search(label) is not None
        or label in _PYTHON_SCALARS
    }


def _option_labels(tree: BuilderControlTree, pointer: str) -> list[str]:
    choice = control_at(tree.root, pointer)
    assert isinstance(choice, BuilderChoiceControl)
    return [branch.label for branch in choice.branches]


def _edit_object(
    service: BuilderVisualDraftService,
    opened: BuilderVisualDraftEnvelope,
    address: dict[str, Any],
    working: Any,
) -> BuilderControlTree:
    """The field editor tree the page receives for one object of an opened session."""
    request = BuilderVisualObjectEditRequest.model_validate_json(
        json.dumps(
            {
                "target_ref": opened.target_ref,
                "applied_revision": opened.applied_revision,
                "address": address,
                "working": working,
            }
        )
    )
    return service.edit_object(request).control_tree


def _session_trees(
    tmp_path: Path, session_path: Path
) -> tuple[BuilderControlTree, dict[str, BuilderControlTree]]:
    service = BuilderVisualDraftService(_context(tmp_path))
    opened = service.open(
        BuilderVisualDraftOpenRequest(source_ref=f"nodalarc:sessions/{session_path.name}")
    )
    assert opened.control_tree is not None
    session = opened.applied_session
    assert session is not None
    routing = session.get("routing") or {}
    objects: dict[str, BuilderControlTree] = {}
    for rule in session.get("link_rules") or ():
        objects[f"link rule {rule['id']}"] = _edit_object(
            service,
            opened,
            {"kind": "member", "list_pointer": "/link_rules", "id": rule["id"]},
            rule,
        )
    for domain in routing.get("domains") or ():
        objects[f"routing domain {domain['id']}"] = _edit_object(
            service,
            opened,
            {"kind": "member", "list_pointer": "/routing/domains", "id": domain["id"]},
            domain,
        )
    objects["routing boundaries"] = _edit_object(
        service,
        opened,
        {"kind": "whole_list", "pointer": "/routing/boundaries"},
        routing.get("boundaries") or [],
    )
    return opened.control_tree, objects


@pytest.mark.parametrize("session_path", SHIPPED_SESSIONS, ids=lambda path: path.stem)
def test_every_shipped_session_editor_names_options_without_python_names(
    tmp_path: Path,
    session_path: Path,
) -> None:
    session_tree, object_trees = _session_trees(tmp_path, session_path)

    assert _python_names(session_tree) == set()
    for name, tree in object_trees.items():
        assert _python_names(tree) == set(), name


@pytest.mark.parametrize("family", COMPONENT_FAMILIES)
def test_every_shipped_catalog_component_editor_names_options_without_python_names(
    tmp_path: Path,
    family: CatalogComponentFamily,
) -> None:
    drafts = BuilderCatalogDraftService(_context(tmp_path))
    shipped = sorted((SHIPPED_ROOT / family).rglob("*.yaml"))
    assert shipped
    trees = {
        source.relative_to(SHIPPED_ROOT).as_posix(): drafts.open(
            CatalogDraftOpenRequest(
                source_ref=CatalogRef(f"nodalarc:{source.relative_to(SHIPPED_ROOT).as_posix()}")
            )
        ).control_tree
        for source in shipped
    }
    trees["new"] = drafts.new(
        CatalogDraftNewRequest(family=family, object_id=f"new-{family}")
    ).control_tree

    for name, tree in trees.items():
        assert _python_names(tree) == set(), name


def test_choice_options_read_as_grammar_terms(tmp_path: Path) -> None:
    session_tree, objects = _session_trees(
        tmp_path,
        SHIPPED_ROOT / "sessions/earth-leo-heo-geo-luna-reachability.yaml",
    )
    link_rule = next(tree for name, tree in objects.items() if name.startswith("link rule "))
    domain = next(tree for name, tree in objects.items() if name.startswith("routing domain "))
    boundaries = objects["routing boundaries"]

    assert _option_labels(session_tree, "/segments/0") == [
        "Space segment",
        "Ground segment",
        "Lagrange segment",
    ]
    assert _option_labels(link_rule, "/endpoints/0/select/all") == ["All", "Not set"]
    assert _option_labels(link_rule, "/constraints/max_links_per_node") == [
        "Whole number",
        "Mapping",
        "Not set",
    ]
    assert _option_labels(domain, "/capabilities") == ["Capabilities", "Not set"]
    assert _option_labels(boundaries, "/boundaries/0/export/0/prefixes") == [
        "List of IPv4 networks or IPv6 networks",
        "Aggregate of",
    ]
    assert _option_labels(boundaries, "/boundaries/0/export/0/install_via") == [
        "Peer loopback",
        "Text",
        "Not set",
    ]

    drafts = BuilderCatalogDraftService(_context(tmp_path))
    terminal = drafts.new(CatalogDraftNewRequest(family="terminals", object_id="labels"))
    orbit = drafts.new(CatalogDraftNewRequest(family="orbits", object_id="labels"))
    assert _option_labels(terminal.control_tree, "/terminal/signal") == [
        "RF signal",
        "Optical signal",
    ]
    assert _option_labels(orbit.control_tree, "/orbit/shape") == [
        "Circular orbit",
        "Elliptical orbit",
        "Not set",
    ]


class _UntitledOption(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    first: int
    second: int


class _OtherUntitledOption(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    third: int
    fourth: int


class _ChoiceWithoutNames(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    choice: _UntitledOption | _OtherUntitledOption


def test_an_option_without_a_grammar_name_fails_the_tree_build() -> None:
    # The shipped grammar names every option; this model is the shape it must never take.
    with pytest.raises(BuilderControlLabelError) as refused:
        build_model_control_tree(
            _ChoiceWithoutNames,
            {},
            projection_revision=0,
            root_label="Choice",
        )

    assert refused.value.json_pointer == "/choice"
    assert str(refused.value) == "option 1 of the choice at /choice has no user-facing name"
