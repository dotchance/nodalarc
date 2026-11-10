# Copyright 2024-2026 .chance (dotchance)
# SPDX-License-Identifier: AGPL-3.0-only
"""One parse of an applied YAML buffer, for a session and for a component."""

from __future__ import annotations

from dataclasses import dataclass

from nodalarc.catalog_refs import CatalogRef
from nodalarc.catalog_registry import catalog_family_spec, validate_referenced_document_identity
from nodalarc.document_errors import document_errors
from nodalarc.marked_yaml import MarkedYamlError, YamlSourceMap, load_marked_yaml
from nodalarc.models.builder_api import BuilderIssue, JsonDocument
from pydantic import BaseModel, ValidationError

from .builder_compiler import CanonicalConfigurationDocument, canonicalize_persisted_configuration


@dataclass(frozen=True, slots=True)
class YamlApplication:
    """The canonical document a YAML buffer states, or the issues that refuse it."""

    model: BaseModel | None
    canonical: CanonicalConfigurationDocument | None
    source_map: YamlSourceMap | None
    issues: tuple[BuilderIssue, ...]


def _issue(
    code: str,
    message: str,
    *,
    target_ref: CatalogRef,
    json_pointer: str | None = None,
    source_map: YamlSourceMap | None = None,
    prefer_key: bool = False,
    source_line: int | None = None,
    source_column: int | None = None,
) -> BuilderIssue:
    if source_map is not None:
        span = source_map.span_for(json_pointer or "", prefer_key=prefer_key)
        if span is not None:
            source_line, source_column = span.start.line, span.start.column
    return BuilderIssue(
        code=code,
        stage="structural",
        severity="error",
        message=message,
        blocks=("save", "deploy"),
        source_ref=str(target_ref),
        json_pointer=json_pointer,
        draft_path="yaml",
        source_line=source_line,
        source_column=source_column,
    )


def parse_yaml_application(target_ref: CatalogRef, yaml_text: str) -> YamlApplication:
    """Validate a buffer as the whole document at ``target_ref``.

    The text must parse, validate as the reference's family and keep the
    identity the reference fixes. A refusal carries each issue at its pointer
    and its line in the text.
    """
    family = target_ref.family
    if family is None:
        raise ValueError(f"catalog reference {target_ref!r} has no registered family")
    spec = catalog_family_spec(family)
    kind = "Session" if spec.wrapper is None else "Component"
    try:
        marked = load_marked_yaml(yaml_text)
    except (UnicodeError, MarkedYamlError, RecursionError) as error:
        mark = error.problem_mark if isinstance(error, MarkedYamlError) else None
        return YamlApplication(
            None,
            None,
            None,
            (
                _issue(
                    "builder.yaml.invalid_syntax",
                    f"{kind} YAML is invalid: {error}",
                    target_ref=target_ref,
                    source_line=mark.line if mark is not None else None,
                    source_column=mark.column if mark is not None else None,
                ),
            ),
        )
    source_map = marked.source_map
    try:
        model = spec.validate_document(marked.data)
    except ValidationError as error:
        return YamlApplication(
            None,
            None,
            source_map,
            tuple(
                _issue(
                    f"builder.structural.{item.type}",
                    item.message,
                    target_ref=target_ref,
                    json_pointer=item.pointer or None,
                    source_map=source_map,
                    prefer_key=item.type == "extra_forbidden",
                )
                for item in document_errors(error, marked.data)
            ),
        )
    except (TypeError, ValueError) as error:
        return YamlApplication(
            None,
            None,
            source_map,
            (
                _issue(
                    "builder.structural.invalid_document",
                    str(error),
                    target_ref=target_ref,
                    source_map=source_map,
                ),
            ),
        )
    try:
        validate_referenced_document_identity(target_ref, model)
    except ValueError:
        pointer = "/session/name" if spec.wrapper is None else f"/{spec.wrapper}/id"
        return YamlApplication(
            None,
            None,
            source_map,
            (
                _issue(
                    "builder.yaml.fixed_identity",
                    f"{kind} identity is fixed by its user: reference",
                    target_ref=target_ref,
                    json_pointer=pointer,
                    source_map=source_map,
                    prefer_key=True,
                ),
            ),
        )
    document: JsonDocument = marked.data
    return YamlApplication(
        model,
        canonicalize_persisted_configuration(target_ref, document),
        source_map,
        (),
    )
