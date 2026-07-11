"""Apply revision-bound graphical control commands to canonical session JSON."""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from types import NoneType, UnionType
from typing import Annotated, Literal, Union, cast, get_args, get_origin

from nodalarc.catalog_refs import CatalogRef
from nodalarc.models.builder_api import JsonDocument
from nodalarc.models.builder_controls_api import (
    BuilderControlMutation,
    BuilderInsertItemCommand,
    BuilderInsertMapEntryCommand,
    BuilderMoveItemCommand,
    BuilderRemoveItemCommand,
    BuilderRemoveMapEntryCommand,
    BuilderRenameMapKeyCommand,
    BuilderSelectChoiceCommand,
    BuilderSetPresentCommand,
    BuilderSetScalarCommand,
)
from pydantic import BaseModel
from pydantic.fields import FieldInfo

from .builder_control_tree import BuilderControlBinding

_MISSING = object()
_TEMPLATE_MARKERS = frozenset({"add-item", "add-key", "add-value"})


class BuilderControlMutationError(ValueError):
    """A mutation batch cannot be applied to its revision-bound controls."""


@dataclass(frozen=True, slots=True)
class _VirtualTarget:
    trail: tuple[str, ...]
    template_pointer: str
    actual_pointer: str


def apply_builder_control_mutations(
    document: JsonDocument,
    bindings: Mapping[str, BuilderControlBinding],
    commands: tuple[BuilderControlMutation, ...],
) -> JsonDocument:
    """Apply one command batch without validating an intermediate document."""

    if not commands:
        raise BuilderControlMutationError("control mutation batches must not be empty")
    shadow = deepcopy(document)
    virtual_targets: dict[tuple[str, ...], _VirtualTarget] = {}
    shifted_collections: set[str] = set()

    for command in commands:
        binding = _binding(bindings, command.control_id)
        if isinstance(command, BuilderSetScalarCommand):
            _require_role(binding, "scalar")
            pointer = _resolve_target(binding, virtual_targets, shifted_collections)
            _set_pointer(shadow, pointer, command.value)
        elif isinstance(command, BuilderSetPresentCommand):
            if binding.owner_model is None:
                raise BuilderControlMutationError("the session root cannot be removed")
            pointer = _resolve_target(binding, virtual_targets, shifted_collections)
            if command.present:
                if _get_pointer(shadow, pointer) is _MISSING:
                    _set_pointer(shadow, pointer, _seed_for_annotation(binding.annotation))
            else:
                _delete_pointer(shadow, pointer)
        elif isinstance(command, BuilderSelectChoiceCommand):
            _require_role(binding, "choice")
            branch = _binding(bindings, command.branch_id)
            if branch.role not in {"choice_branch", "literal_branch"}:
                raise BuilderControlMutationError("branch_id does not identify a choice branch")
            if branch.trail[: len(binding.trail)] != binding.trail:
                raise BuilderControlMutationError("branch_id does not belong to control_id")
            pointer = _resolve_target(binding, virtual_targets, shifted_collections)
            if branch.role == "literal_branch":
                _set_pointer(shadow, pointer, _literal_value(branch.choice_value))
            elif branch.annotation is NoneType:
                _delete_pointer(shadow, pointer)
            else:
                _set_pointer(
                    shadow,
                    pointer,
                    _seed_for_choice_annotation(branch.annotation),
                )
        elif isinstance(command, BuilderInsertItemCommand):
            _require_role(binding, "sequence")
            item_annotation = _variable_sequence_item(binding.annotation)
            sequence = _sequence_at(shadow, binding.json_pointer, create=True)
            if command.index > len(sequence):
                raise BuilderControlMutationError("insert_item index is outside the sequence")
            template_trail = (*binding.trail, "add-item")
            if template_trail in virtual_targets:
                raise BuilderControlMutationError(
                    "one mutation batch may insert only one virtual item per sequence"
                )
            sequence.insert(command.index, _seed_for_annotation(item_annotation))
            virtual_targets[template_trail] = _VirtualTarget(
                trail=template_trail,
                template_pointer=_pointer_append(binding.json_pointer, "-"),
                actual_pointer=_pointer_append(binding.json_pointer, str(command.index)),
            )
            shifted_collections.add(binding.json_pointer)
        elif isinstance(command, BuilderRemoveItemCommand):
            _require_role(binding, "sequence")
            _variable_sequence_item(binding.annotation)
            sequence = _sequence_at(shadow, binding.json_pointer)
            if command.index >= len(sequence):
                raise BuilderControlMutationError("remove_item index is outside the sequence")
            sequence.pop(command.index)
            shifted_collections.add(binding.json_pointer)
        elif isinstance(command, BuilderMoveItemCommand):
            _require_role(binding, "sequence")
            _variable_sequence_item(binding.annotation)
            sequence = _sequence_at(shadow, binding.json_pointer)
            if command.from_index >= len(sequence) or command.to_index >= len(sequence):
                raise BuilderControlMutationError("move_item index is outside the sequence")
            value = sequence.pop(command.from_index)
            sequence.insert(command.to_index, value)
            shifted_collections.add(binding.json_pointer)
        elif isinstance(command, BuilderInsertMapEntryCommand):
            _require_role(binding, "mapping")
            mapping = _mapping_at(shadow, binding.json_pointer, create=True)
            key = _map_key(command.key)
            if key in mapping:
                raise BuilderControlMutationError(f"mapping key {key!r} already exists")
            _key_annotation, value_annotation = _mapping_annotations(binding.annotation)
            if _is_scalar_annotation(value_annotation):
                if command.value is None:
                    raise BuilderControlMutationError(
                        "scalar-valued mappings require an inserted scalar value"
                    )
                mapping[key] = command.value
            else:
                if command.value is not None:
                    raise BuilderControlMutationError(
                        "structured mapping values are populated through child controls"
                    )
                mapping[key] = _seed_for_annotation(value_annotation)
            template_trail = (*binding.trail, "add-value")
            virtual_targets[template_trail] = _VirtualTarget(
                trail=template_trail,
                template_pointer=_pointer_append(binding.json_pointer, "-"),
                actual_pointer=_pointer_append(binding.json_pointer, key),
            )
            shifted_collections.add(binding.json_pointer)
        elif isinstance(command, BuilderRemoveMapEntryCommand):
            _require_role(binding, "mapping")
            mapping = _mapping_at(shadow, binding.json_pointer)
            key = _mapping_key_at(mapping, command.index)
            del mapping[key]
            shifted_collections.add(binding.json_pointer)
        elif isinstance(command, BuilderRenameMapKeyCommand):
            _require_role(binding, "mapping")
            mapping = _mapping_at(shadow, binding.json_pointer)
            old_key = _mapping_key_at(mapping, command.index)
            new_key = _map_key(command.key)
            if new_key != old_key and new_key in mapping:
                raise BuilderControlMutationError(f"mapping key {new_key!r} already exists")
            replaced: dict[str, object] = {}
            for key, value in mapping.items():
                replaced[new_key if key == old_key else key] = value
            mapping.clear()
            mapping.update(replaced)
            shifted_collections.add(binding.json_pointer)
        else:
            raise AssertionError(f"unhandled control mutation {type(command).__name__}")

    return shadow


def _binding(
    bindings: Mapping[str, BuilderControlBinding],
    control_id: str,
) -> BuilderControlBinding:
    binding = bindings.get(control_id)
    if not isinstance(binding, BuilderControlBinding):
        raise BuilderControlMutationError(f"unknown or stale control id {control_id!r}")
    return binding


def _require_role(binding: BuilderControlBinding, role: str) -> None:
    if binding.role != role:
        raise BuilderControlMutationError(
            f"control {binding.json_pointer or '/'} does not support this operation"
        )


def _resolve_target(
    binding: BuilderControlBinding,
    virtual_targets: dict[tuple[str, ...], _VirtualTarget],
    shifted_collections: set[str],
) -> str:
    candidates = [
        target for trail, target in virtual_targets.items() if binding.trail[: len(trail)] == trail
    ]
    if candidates:
        target = max(candidates, key=lambda item: len(item.trail))
        if binding.json_pointer == target.template_pointer:
            return target.actual_pointer
        prefix = f"{target.template_pointer}/"
        if not binding.json_pointer.startswith(prefix):
            raise BuilderControlMutationError("virtual control pointer escaped its template")
        suffix = binding.json_pointer[len(target.template_pointer) :]
        return f"{target.actual_pointer}{suffix}"
    if any(marker in binding.trail for marker in _TEMPLATE_MARKERS):
        raise BuilderControlMutationError(
            "virtual template controls require their insert operation first"
        )
    for pointer in shifted_collections:
        if binding.json_pointer.startswith(f"{pointer}/"):
            raise BuilderControlMutationError(
                "a shifted sequence or map item cannot be edited later in the same batch"
            )
    return binding.json_pointer


def _split_annotated(annotation: object) -> tuple[object, tuple[object, ...]]:
    metadata: list[object] = []
    while get_origin(annotation) is Annotated:
        arguments = get_args(annotation)
        annotation = arguments[0]
        metadata.extend(arguments[1:])
    return annotation, tuple(metadata)


def _seed_for_annotation(annotation: object) -> object:
    annotation, metadata = _split_annotated(annotation)
    origin = get_origin(annotation)
    if origin in {Union, UnionType}:
        branch = next(
            (
                branch
                for branch in get_args(annotation)
                if _split_annotated(branch)[0] is not NoneType
            ),
            NoneType,
        )
        return None if branch is NoneType else _seed_for_annotation(branch)
    if origin is Literal:
        values = get_args(annotation)
        return _literal_value(values[0]) if values else None
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        return {}
    if origin in {tuple, list, set, frozenset}:
        return []
    if origin is dict or annotation is dict:
        return {}
    if isinstance(annotation, type) and issubclass(annotation, Enum):
        member = next(iter(annotation))
        return _literal_value(member.value)
    if isinstance(annotation, type) and issubclass(annotation, CatalogRef):
        return ""
    if annotation is bool:
        return False
    if annotation in {int, float}:
        minimum, exclusive = _numeric_minimum(metadata)
        if minimum is None:
            return 0 if annotation is int else 0.0
        if exclusive:
            minimum += 1
        return int(minimum) if annotation is int else float(minimum)
    if annotation in {str, datetime}:
        return ""
    if annotation is NoneType:
        return None
    raise BuilderControlMutationError(f"cannot seed control type {annotation!r}")


def _seed_for_choice_annotation(annotation: object) -> object:
    """Seed enough branch structure for the next control projection to retain it."""

    base, _metadata = _split_annotated(annotation)
    origin = get_origin(base)
    if origin in {Union, UnionType}:
        branch = next(
            (branch for branch in get_args(base) if _split_annotated(branch)[0] is not NoneType),
            NoneType,
        )
        return None if branch is NoneType else _seed_for_choice_annotation(branch)
    if isinstance(base, type) and issubclass(base, BaseModel):
        model_type = cast(type[BaseModel], base)
        seeded: dict[str, object] = {}
        for field_name, field in model_type.model_fields.items():
            if not field.is_required():
                continue
            alias = field.serialization_alias or field.alias or field_name
            if not isinstance(alias, str):
                raise BuilderControlMutationError(
                    f"cannot seed non-string field alias on {model_type.__qualname__}.{field_name}"
                )
            seeded[alias] = _seed_for_choice_annotation(field.annotation)
        return seeded
    return _seed_for_annotation(annotation)


def _numeric_minimum(metadata: tuple[object, ...]) -> tuple[float | None, bool]:
    for item in _flatten_metadata(metadata):
        ge = getattr(item, "ge", None)
        if ge is not None:
            return float(ge), False
        gt = getattr(item, "gt", None)
        if gt is not None:
            return float(gt), True
    return None, False


def _flatten_metadata(metadata: tuple[object, ...]) -> tuple[object, ...]:
    flattened: list[object] = []
    pending = list(metadata)
    while pending:
        item = pending.pop(0)
        flattened.append(item)
        if isinstance(item, FieldInfo):
            pending[0:0] = item.metadata
    return tuple(flattened)


def _variable_sequence_item(annotation: object) -> object:
    annotation, _ = _split_annotated(annotation)
    origin = get_origin(annotation)
    arguments = get_args(annotation)
    if origin not in {tuple, list, set, frozenset}:
        raise BuilderControlMutationError("control does not target a sequence")
    if origin is tuple and not (len(arguments) == 2 and arguments[1] is Ellipsis):
        raise BuilderControlMutationError("fixed-length tuples cannot be structurally changed")
    if not arguments:
        raise BuilderControlMutationError("untyped sequences cannot be changed")
    return arguments[0]


def _mapping_annotations(annotation: object) -> tuple[object, object]:
    annotation, _ = _split_annotated(annotation)
    if get_origin(annotation) is not dict or len(get_args(annotation)) != 2:
        raise BuilderControlMutationError("control does not target a typed mapping")
    key_annotation, value_annotation = get_args(annotation)
    return key_annotation, value_annotation


def _is_scalar_annotation(annotation: object) -> bool:
    annotation, _ = _split_annotated(annotation)
    origin = get_origin(annotation)
    if origin in {Union, UnionType}:
        return all(
            branch is NoneType or _is_scalar_annotation(branch) for branch in get_args(annotation)
        )
    if origin is Literal:
        return True
    if isinstance(annotation, type) and issubclass(annotation, (CatalogRef, Enum)):
        return True
    return annotation in {str, int, float, bool, datetime}


def _literal_value(value: object) -> object:
    return value.value if isinstance(value, Enum) else value


def _map_key(value: object) -> str:
    if not isinstance(value, str):
        raise BuilderControlMutationError("canonical mapping keys must be strings")
    return value


def _mapping_key_at(mapping: dict[str, object], index: int) -> str:
    keys = tuple(mapping)
    if index >= len(keys):
        raise BuilderControlMutationError("mapping entry index is outside the mapping")
    return keys[index]


def _pointer_tokens(pointer: str) -> tuple[str, ...]:
    if pointer == "":
        return ()
    if not pointer.startswith("/"):
        raise BuilderControlMutationError(f"invalid canonical JSON pointer {pointer!r}")
    return tuple(token.replace("~1", "/").replace("~0", "~") for token in pointer[1:].split("/"))


def _get_pointer(document: object, pointer: str) -> object:
    current = document
    for token in _pointer_tokens(pointer):
        if isinstance(current, dict):
            if token not in current:
                return _MISSING
            current = current[token]
        elif isinstance(current, list):
            try:
                current = current[int(token)]
            except IndexError, ValueError:
                return _MISSING
        else:
            return _MISSING
    return current


def _set_pointer(document: JsonDocument, pointer: str, value: object) -> None:
    tokens = _pointer_tokens(pointer)
    if not tokens:
        raise BuilderControlMutationError("the session root cannot be replaced")
    current: object = document
    for index, token in enumerate(tokens[:-1]):
        next_token = tokens[index + 1]
        if isinstance(current, dict):
            child = current.get(token, _MISSING)
            if child is _MISSING:
                child = [] if next_token.isdigit() else {}
                current[token] = child
            current = child
        elif isinstance(current, list):
            try:
                current = current[int(token)]
            except (IndexError, ValueError) as error:
                raise BuilderControlMutationError(
                    f"control pointer {pointer!r} is outside the current document"
                ) from error
        else:
            raise BuilderControlMutationError(f"control pointer {pointer!r} crosses a scalar value")
    final = tokens[-1]
    if isinstance(current, dict):
        current[final] = value
    elif isinstance(current, list):
        try:
            current[int(final)] = value
        except (IndexError, ValueError) as error:
            raise BuilderControlMutationError(
                f"control pointer {pointer!r} is outside the current document"
            ) from error
    else:
        raise BuilderControlMutationError(f"control pointer {pointer!r} crosses a scalar value")


def _delete_pointer(document: JsonDocument, pointer: str) -> None:
    tokens = _pointer_tokens(pointer)
    if not tokens:
        raise BuilderControlMutationError("the session root cannot be removed")
    parent_pointer = "".join(f"/{_escape_pointer(token)}" for token in tokens[:-1])
    parent = _get_pointer(document, parent_pointer)
    if isinstance(parent, dict):
        parent.pop(tokens[-1], None)
    elif isinstance(parent, list):
        try:
            parent.pop(int(tokens[-1]))
        except IndexError, ValueError:
            return


def _sequence_at(document: JsonDocument, pointer: str, *, create: bool = False) -> list[object]:
    value = _get_pointer(document, pointer)
    if value is _MISSING and create:
        _set_pointer(document, pointer, [])
        value = _get_pointer(document, pointer)
    if not isinstance(value, list):
        raise BuilderControlMutationError("sequence control does not target a current sequence")
    return value


def _mapping_at(
    document: JsonDocument,
    pointer: str,
    *,
    create: bool = False,
) -> dict[str, object]:
    value = _get_pointer(document, pointer)
    if value is _MISSING and create:
        _set_pointer(document, pointer, {})
        value = _get_pointer(document, pointer)
    if not isinstance(value, dict):
        raise BuilderControlMutationError("map control does not target a current mapping")
    return value


def _pointer_append(pointer: str, token: str) -> str:
    return f"{pointer}/{_escape_pointer(token)}"


def _escape_pointer(token: str) -> str:
    return token.replace("~", "~0").replace("/", "~1")
