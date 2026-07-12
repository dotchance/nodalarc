"""Build editable graphical controls directly from canonical configuration models."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping, Sequence, Set
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from types import MappingProxyType, NoneType, UnionType
from typing import Annotated, Literal, TypeGuard, Union, cast, get_args, get_origin

from nodalarc.catalog_refs import CatalogFamily, CatalogRef
from nodalarc.models.builder_controls_api import (
    BuilderChoiceBranch,
    BuilderChoiceControl,
    BuilderControl,
    BuilderControlTree,
    BuilderMapControl,
    BuilderMapEntry,
    BuilderObjectControl,
    BuilderObjectField,
    BuilderScalarConstraints,
    BuilderScalarControl,
    BuilderSequenceControl,
    BuilderSequenceItem,
)
from nodalarc.models.segment_session import SegmentSessionConfig
from pydantic import BaseModel, TypeAdapter, ValidationError
from pydantic.fields import FieldInfo

type BuilderControlInstrumentation = Callable[[str], None]

_MISSING = object()


class BuilderControlTypeError(TypeError):
    """Raised when the canonical model graph contains an unhandled type shape."""


@dataclass(frozen=True, slots=True)
class BuilderControlBinding:
    """Server-only target for one revision-scoped control or choice branch."""

    projection_revision: int
    json_pointer: str
    role: str
    annotation: object
    owner_model: type[BaseModel] | None
    field_name: str | None
    annotation_path: tuple[str, ...]
    trail: tuple[str, ...]
    choice_value: object = None


@dataclass(frozen=True, slots=True)
class BuilderControlTreeBuild:
    """Client projection plus the server-only bindings used by future mutations."""

    tree: BuilderControlTree
    bindings: Mapping[str, BuilderControlBinding]


@dataclass(frozen=True, slots=True)
class _FieldContext:
    model: type[BaseModel]
    field_name: str


class _ControlFactory:
    def __init__(
        self,
        document: BaseModel | Mapping[str, object],
        root_model: type[BaseModel],
        projection_revision: int,
        instrument: BuilderControlInstrumentation | None,
    ) -> None:
        if projection_revision < 0:
            raise ValueError("projection_revision must be non-negative")
        canonical_json = (
            document.model_dump(mode="json", by_alias=True, exclude_none=True)
            if isinstance(document, BaseModel)
            else document
        )
        serialized = json.dumps(
            canonical_json,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )
        self._scope = hashlib.sha256(f"{projection_revision}\0{serialized}".encode()).hexdigest()
        self._projection_revision = projection_revision
        self._instrument = instrument
        self._bindings: dict[str, BuilderControlBinding] = {}
        self._root_model = root_model

    @property
    def bindings(self) -> Mapping[str, BuilderControlBinding]:
        return MappingProxyType(dict(self._bindings))

    def build_root(
        self,
        document: BaseModel | Mapping[str, object],
        *,
        label: str,
    ) -> BuilderObjectControl:
        control = self._build_model(
            self._root_model,
            document,
            json_pointer="",
            label=label,
            required=True,
            present=True,
            description=self._root_model.__doc__,
            context=None,
            annotation_path=(),
            trail=("root",),
            model_stack=(),
        )
        if not isinstance(control, BuilderObjectControl):
            raise AssertionError("canonical model did not produce an object control")
        return control

    def _build_model(
        self,
        model: type[BaseModel],
        value: object,
        *,
        json_pointer: str,
        label: str,
        required: bool,
        present: bool,
        description: str | None,
        context: _FieldContext | None,
        annotation_path: tuple[str, ...],
        trail: tuple[str, ...],
        model_stack: tuple[type[BaseModel], ...],
    ) -> BuilderObjectControl:
        control_id = self._bind(
            prefix="ctl",
            json_pointer=json_pointer,
            role="object",
            annotation=model,
            context=context,
            annotation_path=annotation_path,
            trail=trail,
        )
        model_name = f"{model.__module__}.{model.__qualname__}"
        instance = value if isinstance(value, model) else None
        mapping = value if isinstance(value, Mapping) else None
        if model_stack.count(model) >= 2 and instance is None:
            return BuilderObjectControl(
                control_id=control_id,
                json_pointer=json_pointer,
                label=label,
                required=required,
                present=present,
                description=description,
                model_name=model_name,
                recursive_reference=True,
            )

        fields: list[BuilderObjectField] = []
        next_stack = (*model_stack, model)
        for field_name, field_info in model.model_fields.items():
            field_context = _FieldContext(model=model, field_name=field_name)
            self._record(_field_obligation_key(model, field_name))
            wire_name = _wire_alias(field_name, field_info)
            field_value = (
                getattr(instance, field_name)
                if instance is not None
                else mapping.get(wire_name, _MISSING)
                if mapping is not None
                else _MISSING
            )
            child = self._build_annotation(
                field_info.annotation,
                field_value,
                metadata=tuple(field_info.metadata),
                json_pointer=_pointer_append(json_pointer, wire_name),
                label=_humanize(wire_name),
                required=field_info.is_required(),
                present=field_value is not _MISSING and field_value is not None,
                description=field_info.description,
                context=field_context,
                annotation_path=(),
                trail=(*trail, "field", field_name),
                model_stack=next_stack,
            )
            fields.append(
                BuilderObjectField(
                    field_name=field_name,
                    wire_name=wire_name,
                    control=child,
                )
            )

        return BuilderObjectControl(
            control_id=control_id,
            json_pointer=json_pointer,
            label=label,
            required=required,
            present=present,
            description=description,
            model_name=model_name,
            fields=tuple(fields),
        )

    def _build_annotation(
        self,
        annotation: object,
        value: object,
        *,
        metadata: tuple[object, ...],
        json_pointer: str,
        label: str,
        required: bool,
        present: bool,
        description: str | None,
        context: _FieldContext,
        annotation_path: tuple[str, ...],
        trail: tuple[str, ...],
        model_stack: tuple[type[BaseModel], ...],
    ) -> BuilderControl:
        annotation, annotated_metadata = _split_annotated(annotation)
        all_metadata = (*metadata, *annotated_metadata)
        origin = get_origin(annotation)

        if origin in {Union, UnionType}:
            return self._build_union(
                annotation,
                value,
                metadata=all_metadata,
                json_pointer=json_pointer,
                label=label,
                required=required,
                present=present,
                description=description,
                context=context,
                annotation_path=annotation_path,
                trail=trail,
                model_stack=model_stack,
            )
        if origin is Literal:
            return self._build_literal(
                annotation,
                value,
                json_pointer=json_pointer,
                label=label,
                required=required,
                present=present,
                description=description,
                context=context,
                annotation_path=annotation_path,
                trail=trail,
            )
        if _is_model_type(annotation):
            return self._build_model(
                annotation,
                value,
                json_pointer=json_pointer,
                label=label,
                required=required,
                present=present,
                description=description,
                context=context,
                annotation_path=annotation_path,
                trail=trail,
                model_stack=model_stack,
            )
        if origin is dict or annotation is dict:
            return self._build_mapping(
                annotation,
                value,
                metadata=all_metadata,
                json_pointer=json_pointer,
                label=label,
                required=required,
                present=present,
                description=description,
                context=context,
                annotation_path=annotation_path,
                trail=trail,
                model_stack=model_stack,
            )
        if origin in {tuple, list, set, frozenset}:
            return self._build_sequence(
                annotation,
                value,
                metadata=all_metadata,
                json_pointer=json_pointer,
                label=label,
                required=required,
                present=present,
                description=description,
                context=context,
                annotation_path=annotation_path,
                trail=trail,
                model_stack=model_stack,
            )
        if _is_enum_type(annotation):
            return self._build_enum(
                annotation,
                value,
                json_pointer=json_pointer,
                label=label,
                required=required,
                present=present,
                description=description,
                context=context,
                annotation_path=annotation_path,
                trail=trail,
            )
        if annotation in {str, int, float, bool, datetime} or _is_catalog_ref_type(annotation):
            return self._build_scalar(
                annotation,
                value,
                metadata=all_metadata,
                json_pointer=json_pointer,
                label=label,
                required=required,
                present=present,
                description=description,
                context=context,
                annotation_path=annotation_path,
                trail=trail,
            )
        raise BuilderControlTypeError(
            f"unsupported control annotation {_annotation_key(annotation)} at {json_pointer or '/'}"
        )

    def _build_union(
        self,
        annotation: object,
        value: object,
        *,
        metadata: tuple[object, ...],
        json_pointer: str,
        label: str,
        required: bool,
        present: bool,
        description: str | None,
        context: _FieldContext,
        annotation_path: tuple[str, ...],
        trail: tuple[str, ...],
        model_stack: tuple[type[BaseModel], ...],
    ) -> BuilderChoiceControl:
        branches = get_args(annotation)
        selected_index = _select_union_branch(branches, value)
        rendered: list[BuilderChoiceBranch] = []
        for index, original_branch in enumerate(branches):
            branch, branch_metadata = _split_annotated(original_branch)
            self._record(_union_obligation_key(context, annotation_path, branch))
            branch_path = (*annotation_path, f"union:{_annotation_key(branch)}")
            selected = index == selected_index
            branch_id = self._bind(
                prefix="opt",
                json_pointer=json_pointer,
                role="choice_branch",
                annotation=branch,
                context=context,
                annotation_path=annotation_path,
                trail=(*trail, "union", str(index)),
                choice_value=branch,
            )
            if branch is NoneType:
                rendered.append(
                    BuilderChoiceBranch(
                        branch_id=branch_id,
                        label="Not set",
                        branch_kind="null",
                        selected=selected,
                    )
                )
                continue
            branch_value = value if selected else _MISSING
            branch_control = self._build_annotation(
                original_branch,
                branch_value,
                metadata=branch_metadata,
                json_pointer=json_pointer,
                label=_branch_label(original_branch),
                required=required,
                present=selected and value is not _MISSING and value is not None,
                description=description,
                context=context,
                annotation_path=branch_path,
                trail=(*trail, "union", str(index), "control"),
                model_stack=model_stack,
            )
            rendered.append(
                BuilderChoiceBranch(
                    branch_id=branch_id,
                    label=_branch_label(original_branch),
                    branch_kind="type",
                    selected=selected,
                    control=branch_control,
                )
            )

        control_id = self._bind(
            prefix="ctl",
            json_pointer=json_pointer,
            role="choice",
            annotation=annotation,
            context=context,
            annotation_path=annotation_path,
            trail=trail,
        )
        return BuilderChoiceControl(
            control_id=control_id,
            json_pointer=json_pointer,
            label=label,
            required=required,
            present=present,
            description=description,
            branches=tuple(rendered),
        )

    def _build_literal(
        self,
        annotation: object,
        value: object,
        *,
        json_pointer: str,
        label: str,
        required: bool,
        present: bool,
        description: str | None,
        context: _FieldContext,
        annotation_path: tuple[str, ...],
        trail: tuple[str, ...],
    ) -> BuilderChoiceControl:
        rendered: list[BuilderChoiceBranch] = []
        for index, literal in enumerate(get_args(annotation)):
            self._record(_literal_obligation_key(context, annotation_path, literal))
            scalar_value = _literal_scalar(literal)
            branch_id = self._bind(
                prefix="opt",
                json_pointer=json_pointer,
                role="literal_branch",
                annotation=annotation,
                context=context,
                annotation_path=annotation_path,
                trail=(*trail, "literal", str(index)),
                choice_value=literal,
            )
            rendered.append(
                BuilderChoiceBranch(
                    branch_id=branch_id,
                    label=_literal_label(literal),
                    branch_kind="literal",
                    selected=value is not _MISSING and value == literal,
                    literal_value=scalar_value,
                )
            )
        control_id = self._bind(
            prefix="ctl",
            json_pointer=json_pointer,
            role="choice",
            annotation=annotation,
            context=context,
            annotation_path=annotation_path,
            trail=trail,
        )
        return BuilderChoiceControl(
            control_id=control_id,
            json_pointer=json_pointer,
            label=label,
            required=required,
            present=present,
            description=description,
            branches=tuple(rendered),
        )

    def _build_enum(
        self,
        annotation: type[Enum],
        value: object,
        *,
        json_pointer: str,
        label: str,
        required: bool,
        present: bool,
        description: str | None,
        context: _FieldContext,
        annotation_path: tuple[str, ...],
        trail: tuple[str, ...],
    ) -> BuilderChoiceControl:
        rendered: list[BuilderChoiceBranch] = []
        for index, member in enumerate(annotation):
            branch_id = self._bind(
                prefix="opt",
                json_pointer=json_pointer,
                role="literal_branch",
                annotation=annotation,
                context=context,
                annotation_path=annotation_path,
                trail=(*trail, "enum", str(index)),
                choice_value=member,
            )
            rendered.append(
                BuilderChoiceBranch(
                    branch_id=branch_id,
                    label=_literal_label(member.value),
                    branch_kind="literal",
                    selected=value is not _MISSING and value == member,
                    literal_value=_literal_scalar(member.value),
                )
            )
        control_id = self._bind(
            prefix="ctl",
            json_pointer=json_pointer,
            role="choice",
            annotation=annotation,
            context=context,
            annotation_path=annotation_path,
            trail=trail,
        )
        return BuilderChoiceControl(
            control_id=control_id,
            json_pointer=json_pointer,
            label=label,
            required=required,
            present=present,
            description=description,
            branches=tuple(rendered),
        )

    def _build_sequence(
        self,
        annotation: object,
        value: object,
        *,
        metadata: tuple[object, ...],
        json_pointer: str,
        label: str,
        required: bool,
        present: bool,
        description: str | None,
        context: _FieldContext,
        annotation_path: tuple[str, ...],
        trail: tuple[str, ...],
        model_stack: tuple[type[BaseModel], ...],
    ) -> BuilderSequenceControl:
        origin = get_origin(annotation)
        arguments = get_args(annotation)
        fixed_tuple = origin is tuple and not (len(arguments) == 2 and arguments[1] is Ellipsis)
        values = _sequence_values(value)
        items: list[BuilderSequenceItem] = []

        if fixed_tuple:
            item_annotations = arguments
            for index, item_annotation in enumerate(item_annotations):
                item_value = values[index] if index < len(values) else _MISSING
                item_path = (*annotation_path, f"tuple-item:{index}")
                items.append(
                    BuilderSequenceItem(
                        index=index,
                        control=self._build_annotation(
                            item_annotation,
                            item_value,
                            metadata=(),
                            json_pointer=_pointer_append(json_pointer, str(index)),
                            label=f"Item {index + 1}",
                            required=True,
                            present=item_value is not _MISSING,
                            description=None,
                            context=context,
                            annotation_path=item_path,
                            trail=(*trail, "item", str(index)),
                            model_stack=model_stack,
                        ),
                    )
                )
            add_item_control = None
        else:
            if not arguments:
                raise BuilderControlTypeError(
                    f"untyped sequence annotation at {json_pointer or '/'}"
                )
            item_annotation = arguments[0]
            item_path = (*annotation_path, "sequence-item")
            for index, item_value in enumerate(values):
                items.append(
                    BuilderSequenceItem(
                        index=index,
                        control=self._build_annotation(
                            item_annotation,
                            item_value,
                            metadata=(),
                            json_pointer=_pointer_append(json_pointer, str(index)),
                            label=f"Item {index + 1}",
                            required=True,
                            present=True,
                            description=None,
                            context=context,
                            annotation_path=item_path,
                            trail=(*trail, "item", str(index)),
                            model_stack=model_stack,
                        ),
                    )
                )
            add_item_control = self._build_annotation(
                item_annotation,
                _MISSING,
                metadata=(),
                json_pointer=_pointer_append(json_pointer, "-"),
                label="New item",
                required=True,
                present=False,
                description=None,
                context=context,
                annotation_path=item_path,
                trail=(*trail, "add-item"),
                model_stack=model_stack,
            )

        limits = _collection_limits(metadata)
        control_id = self._bind(
            prefix="ctl",
            json_pointer=json_pointer,
            role="sequence",
            annotation=annotation,
            context=context,
            annotation_path=annotation_path,
            trail=trail,
        )
        return BuilderSequenceControl(
            control_id=control_id,
            json_pointer=json_pointer,
            label=label,
            required=required,
            present=present,
            description=description,
            items=tuple(items),
            add_item_control=add_item_control,
            can_add=not fixed_tuple,
            can_remove=not fixed_tuple,
            can_reorder=not fixed_tuple,
            min_items=limits[0],
            max_items=limits[1],
        )

    def _build_mapping(
        self,
        annotation: object,
        value: object,
        *,
        metadata: tuple[object, ...],
        json_pointer: str,
        label: str,
        required: bool,
        present: bool,
        description: str | None,
        context: _FieldContext,
        annotation_path: tuple[str, ...],
        trail: tuple[str, ...],
        model_stack: tuple[type[BaseModel], ...],
    ) -> BuilderControl:
        arguments = get_args(annotation)
        if not arguments:
            control_id = self._bind(
                prefix="ctl",
                json_pointer=json_pointer,
                role="empty_parameters",
                annotation=annotation,
                context=context,
                annotation_path=annotation_path,
                trail=trail,
            )
            return BuilderObjectControl(
                control_id=control_id,
                json_pointer=json_pointer,
                label=label,
                required=required,
                present=present,
                description=description,
                empty_parameters=True,
            )
        if len(arguments) != 2:
            raise BuilderControlTypeError(
                f"unsupported mapping annotation {_annotation_key(annotation)}"
            )

        key_annotation, value_annotation = arguments
        entries: list[BuilderMapEntry] = []
        mapping = value if isinstance(value, Mapping) else {}
        for index, (entry_key, entry_value) in enumerate(mapping.items()):
            entry_pointer = _pointer_append(json_pointer, str(entry_key))
            entries.append(
                BuilderMapEntry(
                    key=self._build_annotation(
                        key_annotation,
                        entry_key,
                        metadata=(),
                        json_pointer=entry_pointer,
                        label="Key",
                        required=True,
                        present=True,
                        description=None,
                        context=context,
                        annotation_path=(*annotation_path, "mapping-key"),
                        trail=(*trail, "entry", str(index), "key"),
                        model_stack=model_stack,
                    ),
                    value=self._build_annotation(
                        value_annotation,
                        entry_value,
                        metadata=(),
                        json_pointer=entry_pointer,
                        label=_humanize(str(entry_key)),
                        required=True,
                        present=True,
                        description=None,
                        context=context,
                        annotation_path=(*annotation_path, "mapping-value"),
                        trail=(*trail, "entry", str(index), "value"),
                        model_stack=model_stack,
                    ),
                )
            )

        add_key = self._build_annotation(
            key_annotation,
            _MISSING,
            metadata=(),
            json_pointer=json_pointer,
            label="New key",
            required=True,
            present=False,
            description=None,
            context=context,
            annotation_path=(*annotation_path, "mapping-key"),
            trail=(*trail, "add-key"),
            model_stack=model_stack,
        )
        add_value = self._build_annotation(
            value_annotation,
            _MISSING,
            metadata=(),
            json_pointer=_pointer_append(json_pointer, "-"),
            label="New value",
            required=True,
            present=False,
            description=None,
            context=context,
            annotation_path=(*annotation_path, "mapping-value"),
            trail=(*trail, "add-value"),
            model_stack=model_stack,
        )
        limits = _collection_limits(metadata)
        control_id = self._bind(
            prefix="ctl",
            json_pointer=json_pointer,
            role="mapping",
            annotation=annotation,
            context=context,
            annotation_path=annotation_path,
            trail=trail,
        )
        return BuilderMapControl(
            control_id=control_id,
            json_pointer=json_pointer,
            label=label,
            required=required,
            present=present,
            description=description,
            entries=tuple(entries),
            add_key_control=add_key,
            add_value_control=add_value,
            min_entries=limits[0],
            max_entries=limits[1],
        )

    def _build_scalar(
        self,
        annotation: object,
        value: object,
        *,
        metadata: tuple[object, ...],
        json_pointer: str,
        label: str,
        required: bool,
        present: bool,
        description: str | None,
        context: _FieldContext,
        annotation_path: tuple[str, ...],
        trail: tuple[str, ...],
    ) -> BuilderScalarControl:
        scalar_kind: Literal["text", "number", "boolean", "datetime", "reference"]
        number_kind: Literal["integer", "float"] | None = None
        reference_families: tuple[CatalogFamily, ...] = ()
        scalar_value = _scalar_value(value)
        constraints = _scalar_constraints(metadata)

        if _is_catalog_ref_type(annotation):
            scalar_kind = "reference"
            allowed = annotation.allowed_families
            if not allowed:
                raise BuilderControlTypeError("catalog reference control requires known families")
            reference_families = tuple(cast(CatalogFamily, family) for family in sorted(allowed))
            constraints = constraints.model_copy(
                update={"pattern": annotation.json_schema_pattern()}
            )
        elif annotation is bool:
            scalar_kind = "boolean"
        elif annotation in {int, float}:
            scalar_kind = "number"
            number_kind = "integer" if annotation is int else "float"
        elif annotation is datetime or constraints.format == "date-time":
            scalar_kind = "datetime"
        else:
            scalar_kind = "text"

        control_id = self._bind(
            prefix="ctl",
            json_pointer=json_pointer,
            role="scalar",
            annotation=annotation,
            context=context,
            annotation_path=annotation_path,
            trail=trail,
        )
        return BuilderScalarControl(
            control_id=control_id,
            json_pointer=json_pointer,
            label=label,
            required=required,
            present=present,
            description=description,
            scalar_kind=scalar_kind,
            value=scalar_value,
            number_kind=number_kind,
            constraints=constraints,
            reference_families=reference_families,
        )

    def _bind(
        self,
        *,
        prefix: Literal["ctl", "opt"],
        json_pointer: str,
        role: str,
        annotation: object,
        context: _FieldContext | None,
        annotation_path: tuple[str, ...],
        trail: tuple[str, ...],
        choice_value: object = None,
    ) -> str:
        identity = json.dumps(
            {
                "scope": self._scope,
                "pointer": json_pointer,
                "role": role,
                "annotation": _annotation_key(annotation),
                "trail": trail,
            },
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )
        control_id = f"{prefix}_{hashlib.sha256(identity.encode()).hexdigest()[:32]}"
        binding = BuilderControlBinding(
            projection_revision=self._projection_revision,
            json_pointer=json_pointer,
            role=role,
            annotation=annotation,
            owner_model=context.model if context is not None else None,
            field_name=context.field_name if context is not None else None,
            annotation_path=annotation_path,
            trail=trail,
            choice_value=choice_value,
        )
        previous = self._bindings.setdefault(control_id, binding)
        if previous != binding:
            raise AssertionError(f"control identity collision for {control_id}")
        return control_id

    def _record(self, key: str) -> None:
        if self._instrument is not None:
            self._instrument(key)


def build_session_control_tree(
    document: SegmentSessionConfig,
    *,
    projection_revision: int,
    instrument: BuilderControlInstrumentation | None = None,
    specialized_fields: Set[tuple[type[BaseModel], str]] = frozenset(),
) -> BuilderControlTreeBuild:
    """Project one validated session revision into editable graphical controls."""

    if not isinstance(document, SegmentSessionConfig):
        raise TypeError("document must be a validated SegmentSessionConfig")
    factory = _ControlFactory(
        document,
        SegmentSessionConfig,
        projection_revision,
        instrument,
    )
    root = factory.build_root(document, label="Session configuration")
    root = _mark_specialized_controls(root, factory.bindings, specialized_fields)
    return BuilderControlTreeBuild(
        tree=BuilderControlTree(
            projection_revision=projection_revision,
            root=root,
        ),
        bindings=factory.bindings,
    )


def build_model_control_tree(
    root_model: type[BaseModel],
    document: BaseModel | Mapping[str, object],
    *,
    projection_revision: int,
    root_label: str,
    instrument: BuilderControlInstrumentation | None = None,
    specialized_fields: Set[tuple[type[BaseModel], str]] = frozenset(),
) -> BuilderControlTreeBuild:
    """Project one canonical model graph and current mapping into typed controls."""

    if not isinstance(root_model, type) or not issubclass(root_model, BaseModel):
        raise TypeError("root_model must be a Pydantic BaseModel subclass")
    if not isinstance(document, (BaseModel, Mapping)):
        raise TypeError("document must be a validated model or canonical mapping")
    factory = _ControlFactory(document, root_model, projection_revision, instrument)
    root = factory.build_root(document, label=root_label)
    root = _mark_specialized_controls(root, factory.bindings, specialized_fields)
    return BuilderControlTreeBuild(
        tree=BuilderControlTree(
            projection_revision=projection_revision,
            root=root,
        ),
        bindings=factory.bindings,
    )


def _mark_specialized_controls(
    control: BuilderControl,
    bindings: Mapping[str, BuilderControlBinding],
    specialized_fields: Set[tuple[type[BaseModel], str]],
) -> BuilderControl:
    binding = bindings[control.control_id]
    specialized = (
        binding.owner_model is not None
        and binding.field_name is not None
        and (binding.owner_model, binding.field_name) in specialized_fields
        and binding.annotation_path == ()
        and binding.trail[-2:] == ("field", binding.field_name)
    )
    updates: dict[str, object] = {"specialized": specialized}
    if isinstance(control, BuilderObjectControl):
        updates["fields"] = tuple(
            field.model_copy(
                update={
                    "control": _mark_specialized_controls(
                        field.control,
                        bindings,
                        specialized_fields,
                    )
                }
            )
            for field in control.fields
        )
    elif isinstance(control, BuilderChoiceControl):
        updates["branches"] = tuple(
            branch.model_copy(
                update={
                    "control": (
                        _mark_specialized_controls(
                            branch.control,
                            bindings,
                            specialized_fields,
                        )
                        if branch.control is not None
                        else None
                    )
                }
            )
            for branch in control.branches
        )
    elif isinstance(control, BuilderSequenceControl):
        updates["items"] = tuple(
            item.model_copy(
                update={
                    "control": _mark_specialized_controls(
                        item.control,
                        bindings,
                        specialized_fields,
                    )
                }
            )
            for item in control.items
        )
        updates["add_item_control"] = (
            _mark_specialized_controls(
                control.add_item_control,
                bindings,
                specialized_fields,
            )
            if control.add_item_control is not None
            else None
        )
    elif isinstance(control, BuilderMapControl):
        updates["entries"] = tuple(
            entry.model_copy(
                update={
                    "key": _mark_specialized_controls(
                        entry.key,
                        bindings,
                        specialized_fields,
                    ),
                    "value": _mark_specialized_controls(
                        entry.value,
                        bindings,
                        specialized_fields,
                    ),
                }
            )
            for entry in control.entries
        )
        updates["add_key_control"] = _mark_specialized_controls(
            control.add_key_control,
            bindings,
            specialized_fields,
        )
        updates["add_value_control"] = _mark_specialized_controls(
            control.add_value_control,
            bindings,
            specialized_fields,
        )
    return control.model_copy(update=updates)


def _split_annotated(annotation: object) -> tuple[object, tuple[object, ...]]:
    metadata: list[object] = []
    while get_origin(annotation) is Annotated:
        arguments = get_args(annotation)
        annotation = arguments[0]
        metadata.extend(arguments[1:])
    return annotation, tuple(metadata)


def _is_model_type(annotation: object) -> TypeGuard[type[BaseModel]]:
    return isinstance(annotation, type) and issubclass(annotation, BaseModel)


def _is_catalog_ref_type(annotation: object) -> TypeGuard[type[CatalogRef]]:
    return isinstance(annotation, type) and issubclass(annotation, CatalogRef)


def _is_enum_type(annotation: object) -> TypeGuard[type[Enum]]:
    return isinstance(annotation, type) and issubclass(annotation, Enum)


def _select_union_branch(branches: tuple[object, ...], value: object) -> int | None:
    if value is _MISSING:
        return next(
            (
                index
                for index, branch in enumerate(branches)
                if _split_annotated(branch)[0] is NoneType
            ),
            None,
        )
    if value is None:
        return next(
            (
                index
                for index, branch in enumerate(branches)
                if _split_annotated(branch)[0] is NoneType
            ),
            None,
        )
    for index, branch in enumerate(branches):
        base, _ = _split_annotated(branch)
        if _is_model_type(base) and isinstance(value, base):
            return index
        if _is_catalog_ref_type(base) and isinstance(value, base):
            return index
        if get_origin(base) is Literal and value in get_args(base):
            return index
    for index, branch in enumerate(branches):
        base, _ = _split_annotated(branch)
        if base is NoneType:
            continue
        try:
            TypeAdapter(branch).validate_python(value, strict=True)
        except TypeError, ValidationError, ValueError:
            continue
        return index
    if isinstance(value, Mapping) and value:
        shaped = _select_model_branch_by_shape(branches, value)
        if shaped is not None:
            return shaped
    return None


def _select_model_branch_by_shape(
    branches: tuple[object, ...],
    value: Mapping[object, object],
) -> int | None:
    """Retain one incomplete model branch when its authored keys identify it."""

    string_keys = {key for key in value if isinstance(key, str)}
    ranked: list[tuple[int, int]] = []
    for index, branch in enumerate(branches):
        model, _ = _split_annotated(branch)
        if not _is_model_type(model):
            continue
        aliases = {
            _wire_alias(field_name, field) for field_name, field in model.model_fields.items()
        }
        matched = len(string_keys & aliases)
        if matched == 0:
            continue
        unknown = len(string_keys - aliases)
        ranked.append((matched - unknown, index))
    if not ranked:
        return None
    ranked.sort(reverse=True)
    if len(ranked) > 1 and ranked[0][0] == ranked[1][0]:
        return None
    return ranked[0][1]


def _sequence_values(value: object) -> Sequence[object]:
    if value is _MISSING or value is None:
        return ()
    if isinstance(value, (str, bytes, bytearray)) or not isinstance(value, Sequence):
        if isinstance(value, (set, frozenset)):
            return tuple(sorted(value, key=_stable_value_key))
        return ()
    return value


def _stable_value_key(value: object) -> str:
    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json", by_alias=True, exclude_none=True)
    elif isinstance(value, Enum):
        value = value.value
    return json.dumps(
        value,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
        default=str,
    )


def _scalar_value(value: object) -> str | int | float | bool | None:
    if value is _MISSING or value is None:
        return None
    if isinstance(value, Enum):
        return _literal_scalar(value.value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, (str, int, float, bool)):
        return value
    raise BuilderControlTypeError(f"unsupported scalar value {type(value).__qualname__}")


def _scalar_constraints(metadata: tuple[object, ...]) -> BuilderScalarConstraints:
    values: dict[str, object] = {}
    attribute_names = {
        "ge": "minimum",
        "gt": "exclusive_minimum",
        "le": "maximum",
        "lt": "exclusive_maximum",
        "multiple_of": "multiple_of",
        "min_length": "min_length",
        "max_length": "max_length",
        "pattern": "pattern",
    }
    for item in _flatten_metadata(metadata):
        for source, target in attribute_names.items():
            candidate = getattr(item, source, None)
            if candidate is not None:
                values[target] = candidate
        json_schema = getattr(item, "json_schema", None)
        if isinstance(json_schema, dict) and isinstance(json_schema.get("format"), str):
            values["format"] = json_schema["format"]
    return BuilderScalarConstraints.model_validate(values)


def _collection_limits(metadata: tuple[object, ...]) -> tuple[int | None, int | None]:
    minimum: int | None = None
    maximum: int | None = None
    for item in _flatten_metadata(metadata):
        candidate_min = getattr(item, "min_length", None)
        candidate_max = getattr(item, "max_length", None)
        if candidate_min is not None:
            minimum = int(candidate_min)
        if candidate_max is not None:
            maximum = int(candidate_max)
    return minimum, maximum


def _flatten_metadata(metadata: tuple[object, ...]) -> tuple[object, ...]:
    flattened: list[object] = []
    pending = list(metadata)
    while pending:
        item = pending.pop(0)
        flattened.append(item)
        if isinstance(item, FieldInfo):
            pending[0:0] = item.metadata
    return tuple(flattened)


def _field_obligation_key(model: type[BaseModel], field_name: str) -> str:
    return f"field:{model.__module__}.{model.__qualname__}.{field_name}"


def _union_obligation_key(
    context: _FieldContext,
    annotation_path: tuple[str, ...],
    branch: object,
) -> str:
    path_key = json.dumps(annotation_path, ensure_ascii=True, separators=(",", ":"))
    owner = f"{context.model.__module__}.{context.model.__qualname__}.{context.field_name}"
    return f"union:{owner}@{path_key}={_annotation_key(branch)}"


def _literal_obligation_key(
    context: _FieldContext,
    annotation_path: tuple[str, ...],
    value: object,
) -> str:
    path_key = json.dumps(annotation_path, ensure_ascii=True, separators=(",", ":"))
    owner = f"{context.model.__module__}.{context.model.__qualname__}.{context.field_name}"
    return f"literal:{owner}@{path_key}={_literal_value_key(value)}"


def _annotation_key(annotation: object) -> str:
    annotation, _ = _split_annotated(annotation)
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
    raise BuilderControlTypeError(
        f"unsupported Literal value {value!r} ({type(value).__qualname__})"
    )


def _literal_scalar(value: object) -> str | int | float | bool | None:
    if isinstance(value, Enum):
        value = value.value
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise BuilderControlTypeError(f"unsupported literal control value {value!r}")


def _literal_label(value: object) -> str:
    if isinstance(value, Enum):
        value = value.value
    if value is None:
        return "Not set"
    if isinstance(value, str):
        return _humanize(value)
    return str(value)


def _branch_label(annotation: object) -> str:
    base, metadata = _split_annotated(annotation)
    for item in metadata:
        json_schema = getattr(item, "json_schema", None)
        if isinstance(json_schema, dict) and isinstance(json_schema.get("format"), str):
            return _humanize(json_schema["format"])
    if _is_model_type(base):
        for discriminator in ("kind", "mode", "strategy", "provider", "adapter"):
            field = base.model_fields.get(discriminator)
            if field is None:
                continue
            literal = _split_annotated(field.annotation)[0]
            if get_origin(literal) is Literal and len(get_args(literal)) == 1:
                return _literal_label(get_args(literal)[0])
        return _humanize(base.__name__)
    if _is_catalog_ref_type(base):
        return "Catalog reference"
    if isinstance(base, type):
        return _humanize(base.__name__)
    return _humanize(_annotation_key(base))


def _wire_alias(field_name: str, field_info: FieldInfo) -> str:
    alias = field_info.serialization_alias or field_info.alias or field_name
    if not isinstance(alias, str):
        raise BuilderControlTypeError(f"field {field_name!r} has a non-string wire alias")
    return alias


def _pointer_append(pointer: str, token: str) -> str:
    escaped = token.replace("~", "~0").replace("/", "~1")
    return f"{pointer}/{escaped}"


def _humanize(value: str) -> str:
    text = value.replace("_", " ").replace("-", " ").strip()
    return text[:1].upper() + text[1:] if text else "Value"
