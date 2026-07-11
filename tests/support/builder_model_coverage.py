"""Test-only discovery of graphical Builder obligations from canonical models."""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass
from dataclasses import field as dataclass_field
from enum import Enum
from types import NoneType, UnionType
from typing import Annotated, Literal, Union, get_args, get_origin

from pydantic import BaseModel
from pydantic.fields import FieldInfo

type BuilderObligationKey = str


@dataclass(frozen=True, slots=True)
class BuilderFieldObligation:
    model: type[BaseModel]
    field_name: str
    wire_alias: str
    required: bool


@dataclass(frozen=True, slots=True)
class BuilderUnionBranchObligation:
    field: BuilderFieldObligation
    annotation_path: tuple[str, ...]
    branch: object = dataclass_field(compare=False, hash=False)
    branch_key: str = dataclass_field(init=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "branch_key", _annotation_key(self.branch))


@dataclass(frozen=True, slots=True)
class BuilderLiteralObligation:
    field: BuilderFieldObligation
    annotation_path: tuple[str, ...]
    value: object = dataclass_field(compare=False, hash=False)
    value_key: str = dataclass_field(init=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "value_key", _literal_value_key(self.value))


type BuilderModelObligation = (
    BuilderFieldObligation | BuilderUnionBranchObligation | BuilderLiteralObligation
)


@dataclass(frozen=True, slots=True)
class BuilderModelGraph:
    root_model: type[BaseModel]
    models: frozenset[type[BaseModel]]
    fields: frozenset[BuilderFieldObligation]
    union_branches: frozenset[BuilderUnionBranchObligation]
    literals: frozenset[BuilderLiteralObligation]

    @property
    def obligations(self) -> frozenset[BuilderModelObligation]:
        return frozenset((*self.fields, *self.union_branches, *self.literals))

    @property
    def obligations_by_key(self) -> dict[BuilderObligationKey, BuilderModelObligation]:
        indexed: dict[BuilderObligationKey, BuilderModelObligation] = {}
        for obligation in self.obligations:
            key = obligation_key(obligation)
            previous = indexed.setdefault(key, obligation)
            if previous != obligation:
                raise AssertionError(
                    f"canonical model graph produced duplicate Builder obligation key {key!r}"
                )
        return indexed

    @property
    def obligation_keys(self) -> frozenset[BuilderObligationKey]:
        return frozenset(self.obligations_by_key)


@dataclass(frozen=True, slots=True)
class BuilderCoverageDifference:
    missing: frozenset[BuilderObligationKey]
    stale: frozenset[BuilderObligationKey]

    @property
    def complete(self) -> bool:
        return not self.missing and not self.stale


class BuilderGraphicalCoverageRecorder:
    """Collect obligation keys emitted by real editable graphical controls.

    Production code stays independent of this test helper. Tests can pass
    ``record`` as an instrumentation callback while building controls, or use
    ``record_field`` for an explicit dedicated-editor registry.
    """

    def __init__(self) -> None:
        self._obligation_keys: set[BuilderObligationKey] = set()

    def record(self, obligation_key: BuilderObligationKey) -> None:
        if not isinstance(obligation_key, str) or not obligation_key:
            raise TypeError("Builder graphical obligation keys must be non-empty strings")
        self._obligation_keys.add(obligation_key)

    def record_many(self, obligation_keys: Iterable[BuilderObligationKey]) -> None:
        for key in obligation_keys:
            self.record(key)

    def record_obligation(self, obligation: BuilderModelObligation) -> None:
        self.record(obligation_key(obligation))

    def record_field(self, model: type[BaseModel], field_name: str) -> None:
        self.record(field_obligation_key(model, field_name))

    @property
    def obligation_keys(self) -> frozenset[BuilderObligationKey]:
        return frozenset(self._obligation_keys)


def discover_builder_model_graph(root_model: type[BaseModel]) -> BuilderModelGraph:
    """Walk every Pydantic model reachable from ``root_model``.

    The result is an in-memory test fact. It is deliberately derived from the
    canonical model classes on every run and is never serialized as a schema or
    coverage manifest.
    """

    if not _is_model_type(root_model):
        raise TypeError("root_model must be a Pydantic BaseModel subclass")

    models: set[type[BaseModel]] = set()
    fields: set[BuilderFieldObligation] = set()
    union_branches: set[BuilderUnionBranchObligation] = set()
    literals: set[BuilderLiteralObligation] = set()
    pending = [root_model]

    while pending:
        model = pending.pop()
        if model in models:
            continue
        models.add(model)

        for field_name, field_info in model.model_fields.items():
            field = BuilderFieldObligation(
                model=model,
                field_name=field_name,
                wire_alias=_wire_alias(field_name, field_info),
                required=field_info.is_required(),
            )
            fields.add(field)
            _walk_annotation(
                field,
                field_info.annotation,
                annotation_path=(),
                pending=pending,
                union_branches=union_branches,
                literals=literals,
            )

    return BuilderModelGraph(
        root_model=root_model,
        models=frozenset(models),
        fields=frozenset(fields),
        union_branches=frozenset(union_branches),
        literals=frozenset(literals),
    )


def compare_builder_coverage(
    graph: BuilderModelGraph,
    registered: set[BuilderObligationKey] | frozenset[BuilderObligationKey],
) -> BuilderCoverageDifference:
    discovered = graph.obligation_keys
    supplied = frozenset(registered)
    if any(not isinstance(key, str) or not key for key in supplied):
        raise TypeError("registered Builder graphical obligations must be non-empty strings")
    return BuilderCoverageDifference(
        missing=discovered - supplied,
        stale=supplied - discovered,
    )


def assert_complete_builder_graphical_coverage(
    graph: BuilderModelGraph,
    registered: set[BuilderObligationKey] | frozenset[BuilderObligationKey],
    *,
    registry_name: str = "Builder graphical controls",
) -> None:
    """Require editable graphical bindings for the complete persisted grammar."""

    difference = compare_builder_coverage(graph, registered)
    if difference.complete:
        return
    raise AssertionError(format_builder_coverage_failure(graph, difference, registry_name))


def format_builder_coverage_failure(
    graph: BuilderModelGraph,
    difference: BuilderCoverageDifference,
    registry_name: str,
) -> str:
    indexed = graph.obligations_by_key
    lines = [
        f"{registry_name} does not cover the canonical {graph.root_model.__name__} grammar.",
        "Only editable, round-tripping graphical controls count as representation coverage.",
    ]
    if difference.missing:
        lines.append(f"Missing graphical obligations ({len(difference.missing)}):")
        for key in sorted(difference.missing):
            lines.append(f"  - {key} :: {obligation_label(indexed[key])}")
    if difference.stale:
        lines.append(f"Stale or unknown graphical obligations ({len(difference.stale)}):")
        lines.extend(f"  - {key}" for key in sorted(difference.stale))
    return "\n".join(lines)


def obligation_key(obligation: BuilderModelObligation) -> BuilderObligationKey:
    field = obligation.field if not isinstance(obligation, BuilderFieldObligation) else obligation
    field_key = _field_key(field.model, field.field_name)
    if isinstance(obligation, BuilderFieldObligation):
        return field_key
    path_key = json.dumps(obligation.annotation_path, ensure_ascii=True, separators=(",", ":"))
    if isinstance(obligation, BuilderUnionBranchObligation):
        return f"union:{field_key.removeprefix('field:')}@{path_key}={obligation.branch_key}"
    return f"literal:{field_key.removeprefix('field:')}@{path_key}={obligation.value_key}"


def field_obligation_key(
    model: type[BaseModel],
    field_name: str,
) -> BuilderObligationKey:
    if not _is_model_type(model):
        raise TypeError("model must be a Pydantic BaseModel subclass")
    if field_name not in model.model_fields:
        owner = f"{model.__module__}.{model.__qualname__}"
        raise KeyError(f"{owner} has no canonical field {field_name!r}")
    return _field_key(model, field_name)


def obligation_label(obligation: BuilderModelObligation) -> str:
    field = obligation.field if not isinstance(obligation, BuilderFieldObligation) else obligation
    owner = f"{field.model.__module__}.{field.model.__qualname__}"
    location = f"{owner}.{field.field_name}"
    if field.wire_alias != field.field_name:
        location += f" (wire alias {field.wire_alias!r})"
    if isinstance(obligation, BuilderFieldObligation):
        presence = "required" if obligation.required else "optional/defaulted"
        return f"field {location} [{presence}]"
    path = "" if not obligation.annotation_path else f" at {'/'.join(obligation.annotation_path)}"
    if isinstance(obligation, BuilderUnionBranchObligation):
        return f"union branch {location}{path}: {obligation.branch_key}"
    return f"literal {location}{path}: {obligation.value!r}"


def _wire_alias(field_name: str, field_info: FieldInfo) -> str:
    alias = field_info.serialization_alias or field_info.alias or field_name
    if not isinstance(alias, str):
        raise TypeError(
            f"{field_name} uses a non-string serialization alias that the coverage walker "
            "cannot identify"
        )
    return alias


def _walk_annotation(
    field: BuilderFieldObligation,
    annotation: object,
    *,
    annotation_path: tuple[str, ...],
    pending: list[type[BaseModel]],
    union_branches: set[BuilderUnionBranchObligation],
    literals: set[BuilderLiteralObligation],
) -> None:
    annotation = _unwrap_annotated(annotation)
    origin = get_origin(annotation)

    if origin in {Union, UnionType}:
        for branch in get_args(annotation):
            branch = _unwrap_annotated(branch)
            union_branches.add(
                BuilderUnionBranchObligation(
                    field=field,
                    annotation_path=annotation_path,
                    branch=branch,
                )
            )
            _walk_annotation(
                field,
                branch,
                annotation_path=(
                    *annotation_path,
                    f"union:{_annotation_label(branch)}",
                ),
                pending=pending,
                union_branches=union_branches,
                literals=literals,
            )
        return

    if origin is Literal:
        for value in get_args(annotation):
            literals.add(
                BuilderLiteralObligation(
                    field=field,
                    annotation_path=annotation_path,
                    value=value,
                )
            )
        return

    if _is_model_type(annotation):
        pending.append(annotation)
        return

    if annotation is NoneType:
        return

    arguments = get_args(annotation)
    if not arguments:
        if isinstance(annotation, str):
            raise TypeError(f"unresolved annotation {annotation!r} on {obligation_label(field)}")
        return

    if origin is tuple and len(arguments) == 2 and arguments[1] is Ellipsis:
        _walk_annotation(
            field,
            arguments[0],
            annotation_path=(*annotation_path, "sequence-item"),
            pending=pending,
            union_branches=union_branches,
            literals=literals,
        )
        return

    if origin in {list, set, frozenset}:
        _walk_annotation(
            field,
            arguments[0],
            annotation_path=(*annotation_path, "sequence-item"),
            pending=pending,
            union_branches=union_branches,
            literals=literals,
        )
        return

    if origin is dict:
        labels = ("mapping-key", "mapping-value")
    elif origin is tuple:
        labels = tuple(f"tuple-item:{index}" for index in range(len(arguments)))
    else:
        labels = tuple(f"argument:{index}" for index in range(len(arguments)))

    for label, argument in zip(labels, arguments, strict=True):
        _walk_annotation(
            field,
            argument,
            annotation_path=(*annotation_path, label),
            pending=pending,
            union_branches=union_branches,
            literals=literals,
        )


def _unwrap_annotated(annotation: object) -> object:
    while get_origin(annotation) is Annotated:
        annotation = get_args(annotation)[0]
    return annotation


def _is_model_type(annotation: object) -> bool:
    return isinstance(annotation, type) and issubclass(annotation, BaseModel)


def _field_key(model: type[BaseModel], field_name: str) -> BuilderObligationKey:
    return f"field:{model.__module__}.{model.__qualname__}.{field_name}"


def _literal_value_key(value: object) -> str:
    if isinstance(value, Enum):
        owner = f"{type(value).__module__}.{type(value).__qualname__}"
        return f"enum:{owner}:{_literal_value_key(value.value)}"
    if value is None:
        return "none"
    if isinstance(value, bool):
        return f"bool:{json.dumps(value)}"
    if isinstance(value, int):
        return f"int:{value}"
    if isinstance(value, str):
        return f"str:{json.dumps(value, ensure_ascii=True)}"
    if isinstance(value, bytes):
        return f"bytes:{value.hex()}"
    raise TypeError(
        f"unsupported Literal value {value!r} ({type(value).__module__}.{type(value).__qualname__})"
    )


def _annotation_label(annotation: object) -> str:
    return _annotation_key(annotation)


def _annotation_key(annotation: object) -> str:
    annotation = _unwrap_annotated(annotation)
    if annotation is NoneType:
        return "None"
    if isinstance(annotation, type):
        return f"{annotation.__module__}.{annotation.__qualname__}"
    origin = get_origin(annotation)
    if origin is Literal:
        values = ", ".join(_literal_value_key(value) for value in get_args(annotation))
        return f"Literal[{values}]"
    if origin is not None:
        origin_label = _annotation_key(origin)
        arguments = ", ".join(
            "..." if argument is Ellipsis else _annotation_key(argument)
            for argument in get_args(annotation)
        )
        return f"{origin_label}[{arguments}]"
    return repr(annotation)
