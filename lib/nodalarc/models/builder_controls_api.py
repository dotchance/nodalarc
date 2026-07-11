"""Typed application contracts for backend-derived graphical controls.

These models are transient Builder projections. They describe editable controls
for one validated configuration revision and are never persisted as NodalArc
configuration.
"""

from __future__ import annotations

from typing import Annotated, Literal, get_origin

from pydantic import BaseModel, ConfigDict, Field, model_validator

from nodalarc.catalog_refs import CatalogFamily

BuilderControlScalarValue = str | int | float | bool | None
BuilderControlScalarKind = Literal["text", "number", "boolean", "datetime", "reference"]
BuilderControlNumberKind = Literal["integer", "float"]
BuilderControlChoiceKind = Literal["literal", "type", "null"]
BuilderControlMutationOperation = Literal[
    "set_scalar",
    "set_present",
    "select_choice",
    "insert_item",
    "remove_item",
    "move_item",
    "insert_map_entry",
    "remove_map_entry",
    "rename_map_key",
]
BuilderMutationScalar = str | int | float | bool


class _BuilderControlApplicationModel(BaseModel):
    """Closed immutable base for graphical-control application state."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True, allow_inf_nan=False)

    @model_validator(mode="before")
    @classmethod
    def _accept_json_arrays_for_tuple_fields(cls, value: object) -> object:
        if not isinstance(value, dict):
            return value
        normalized = dict(value)
        for field_name, field in cls.model_fields.items():
            if get_origin(field.annotation) is tuple and isinstance(
                normalized.get(field_name), list
            ):
                normalized[field_name] = tuple(normalized[field_name])
        return normalized


class BuilderScalarConstraints(_BuilderControlApplicationModel):
    """Pydantic-derived restrictions used to configure one scalar widget."""

    minimum: int | float | None = None
    exclusive_minimum: int | float | None = None
    maximum: int | float | None = None
    exclusive_maximum: int | float | None = None
    multiple_of: int | float | None = None
    min_length: int | None = Field(default=None, ge=0)
    max_length: int | None = Field(default=None, ge=0)
    pattern: str | None = None
    format: str | None = None

    @model_validator(mode="after")
    def _ordered_bounds(self) -> BuilderScalarConstraints:
        lower = self.minimum if self.minimum is not None else self.exclusive_minimum
        upper = self.maximum if self.maximum is not None else self.exclusive_maximum
        if lower is not None and upper is not None and lower > upper:
            raise ValueError("scalar minimum must not exceed scalar maximum")
        if (
            self.min_length is not None
            and self.max_length is not None
            and self.min_length > self.max_length
        ):
            raise ValueError("scalar min_length must not exceed max_length")
        return self


class BuilderControlBase(_BuilderControlApplicationModel):
    """Identity and placement shared by every editable graphical control."""

    control_id: str = Field(pattern=r"^ctl_[0-9a-f]{32}$")
    json_pointer: str
    label: str = Field(min_length=1)
    required: bool
    present: bool
    editable: Literal[True] = True
    specialized: bool = False
    description: str | None = Field(default=None, min_length=1)


class BuilderScalarControl(BuilderControlBase):
    """One scalar editor with a backend-derived widget and restrictions."""

    kind: Literal["scalar"] = "scalar"
    scalar_kind: BuilderControlScalarKind
    value: BuilderControlScalarValue = None
    number_kind: BuilderControlNumberKind | None = None
    constraints: BuilderScalarConstraints = Field(default_factory=BuilderScalarConstraints)
    reference_families: tuple[CatalogFamily, ...] = ()

    @model_validator(mode="after")
    def _kind_metadata_matches(self) -> BuilderScalarControl:
        if (self.number_kind is not None) != (self.scalar_kind == "number"):
            raise ValueError("number_kind is required only for numeric scalar controls")
        if bool(self.reference_families) != (self.scalar_kind == "reference"):
            raise ValueError("reference_families are required only for reference controls")
        return self


class BuilderObjectField(_BuilderControlApplicationModel):
    """One canonical model field and its graphical control."""

    field_name: str = Field(min_length=1)
    wire_name: str = Field(min_length=1)
    control: BuilderControl


class BuilderObjectControl(BuilderControlBase):
    """A structured object editor, including explicit empty parameter objects."""

    kind: Literal["object"] = "object"
    model_name: str | None = Field(default=None, min_length=1)
    fields: tuple[BuilderObjectField, ...] = ()
    empty_parameters: bool = False
    recursive_reference: bool = False

    @model_validator(mode="after")
    def _object_shape_is_explicit(self) -> BuilderObjectControl:
        if self.empty_parameters and (self.fields or self.model_name is not None):
            raise ValueError("empty parameter objects must not declare fields or a model name")
        if self.recursive_reference and self.empty_parameters:
            raise ValueError("recursive model controls are not empty parameter objects")
        return self


class BuilderChoiceBranch(_BuilderControlApplicationModel):
    """One selectable Literal, union type, or null branch."""

    branch_id: str = Field(pattern=r"^opt_[0-9a-f]{32}$")
    label: str = Field(min_length=1)
    branch_kind: BuilderControlChoiceKind
    selected: bool
    literal_value: BuilderControlScalarValue = None
    control: BuilderControl | None = None

    @model_validator(mode="after")
    def _branch_payload_matches_kind(self) -> BuilderChoiceBranch:
        if self.branch_kind == "literal" and self.control is not None:
            raise ValueError("literal branches must not carry a nested control")
        if self.branch_kind == "null" and (
            self.literal_value is not None or self.control is not None
        ):
            raise ValueError("null branches must not carry a value or nested control")
        if self.branch_kind == "type" and self.control is None:
            raise ValueError("type branches require a nested control")
        return self


class BuilderChoiceControl(BuilderControlBase):
    """A compact selector whose branches remain fully typed."""

    kind: Literal["choice"] = "choice"
    branches: tuple[BuilderChoiceBranch, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _at_most_one_selected_branch(self) -> BuilderChoiceControl:
        if sum(branch.selected for branch in self.branches) > 1:
            raise ValueError("choice controls may select at most one branch")
        ids = [branch.branch_id for branch in self.branches]
        if len(set(ids)) != len(ids):
            raise ValueError("choice branch ids must be unique")
        return self


class BuilderSequenceItem(_BuilderControlApplicationModel):
    """One typed row in an ordered sequence."""

    index: int = Field(ge=0)
    control: BuilderControl


class BuilderSequenceControl(BuilderControlBase):
    """A typed row editor for variable or fixed-length sequences."""

    kind: Literal["sequence"] = "sequence"
    items: tuple[BuilderSequenceItem, ...] = ()
    add_item_control: BuilderControl | None = None
    can_add: bool
    can_remove: bool
    can_reorder: bool
    min_items: int | None = Field(default=None, ge=0)
    max_items: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def _sequence_metadata_is_consistent(self) -> BuilderSequenceControl:
        if self.can_add != (self.add_item_control is not None):
            raise ValueError("add_item_control must match can_add")
        if (
            self.min_items is not None
            and self.max_items is not None
            and self.min_items > self.max_items
        ):
            raise ValueError("sequence min_items must not exceed max_items")
        if [item.index for item in self.items] != list(range(len(self.items))):
            raise ValueError("sequence item indices must be contiguous")
        return self


class BuilderMapEntry(_BuilderControlApplicationModel):
    """One typed key/value row in a mapping."""

    key: BuilderControl
    value: BuilderControl


class BuilderMapControl(BuilderControlBase):
    """A typed mapping editor with explicit key and value controls."""

    kind: Literal["map"] = "map"
    entries: tuple[BuilderMapEntry, ...] = ()
    add_key_control: BuilderControl
    add_value_control: BuilderControl
    can_add: Literal[True] = True
    can_remove: Literal[True] = True
    can_rename_keys: Literal[True] = True
    min_entries: int | None = Field(default=None, ge=0)
    max_entries: int | None = Field(default=None, ge=0)


BuilderControl = (
    BuilderScalarControl
    | BuilderObjectControl
    | BuilderChoiceControl
    | BuilderSequenceControl
    | BuilderMapControl
)


class BuilderControlTree(_BuilderControlApplicationModel):
    """Complete graphical control projection for one applied configuration revision."""

    projection_revision: int = Field(ge=0)
    root: BuilderObjectControl


class BuilderSetScalarCommand(_BuilderControlApplicationModel):
    operation: Literal["set_scalar"]
    control_id: str = Field(pattern=r"^ctl_[0-9a-f]{32}$")
    value: BuilderMutationScalar


class BuilderSetPresentCommand(_BuilderControlApplicationModel):
    operation: Literal["set_present"]
    control_id: str = Field(pattern=r"^ctl_[0-9a-f]{32}$")
    present: bool


class BuilderSelectChoiceCommand(_BuilderControlApplicationModel):
    operation: Literal["select_choice"]
    control_id: str = Field(pattern=r"^ctl_[0-9a-f]{32}$")
    branch_id: str = Field(pattern=r"^opt_[0-9a-f]{32}$")


class BuilderInsertItemCommand(_BuilderControlApplicationModel):
    operation: Literal["insert_item"]
    control_id: str = Field(pattern=r"^ctl_[0-9a-f]{32}$")
    index: int = Field(ge=0)


class BuilderRemoveItemCommand(_BuilderControlApplicationModel):
    operation: Literal["remove_item"]
    control_id: str = Field(pattern=r"^ctl_[0-9a-f]{32}$")
    index: int = Field(ge=0)


class BuilderMoveItemCommand(_BuilderControlApplicationModel):
    operation: Literal["move_item"]
    control_id: str = Field(pattern=r"^ctl_[0-9a-f]{32}$")
    from_index: int = Field(ge=0)
    to_index: int = Field(ge=0)


class BuilderInsertMapEntryCommand(_BuilderControlApplicationModel):
    operation: Literal["insert_map_entry"]
    control_id: str = Field(pattern=r"^ctl_[0-9a-f]{32}$")
    key: BuilderMutationScalar
    value: BuilderMutationScalar | None = None


class BuilderRemoveMapEntryCommand(_BuilderControlApplicationModel):
    operation: Literal["remove_map_entry"]
    control_id: str = Field(pattern=r"^ctl_[0-9a-f]{32}$")
    index: int = Field(ge=0)


class BuilderRenameMapKeyCommand(_BuilderControlApplicationModel):
    operation: Literal["rename_map_key"]
    control_id: str = Field(pattern=r"^ctl_[0-9a-f]{32}$")
    index: int = Field(ge=0)
    key: BuilderMutationScalar


BuilderControlMutation = Annotated[
    BuilderSetScalarCommand
    | BuilderSetPresentCommand
    | BuilderSelectChoiceCommand
    | BuilderInsertItemCommand
    | BuilderRemoveItemCommand
    | BuilderMoveItemCommand
    | BuilderInsertMapEntryCommand
    | BuilderRemoveMapEntryCommand
    | BuilderRenameMapKeyCommand,
    Field(discriminator="operation"),
]


BuilderObjectField.model_rebuild()
BuilderObjectControl.model_rebuild()
BuilderChoiceBranch.model_rebuild()
BuilderChoiceControl.model_rebuild()
BuilderSequenceItem.model_rebuild()
BuilderSequenceControl.model_rebuild()
BuilderMapEntry.model_rebuild()
BuilderMapControl.model_rebuild()
BuilderControlTree.model_rebuild()
