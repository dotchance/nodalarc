"""Neutral catalog-family registry for canonical configuration documents."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, cast

from pydantic import BaseModel

from nodalarc.catalog_refs import CatalogFamily, CatalogRef
from nodalarc.models.configuration import CONFIGURATION_DOCUMENT_MODELS
from nodalarc.models.segment_session import SegmentSessionConfig


@dataclass(frozen=True)
class CatalogFamilySpec:
    family: CatalogFamily
    wrapper: str | None
    model_type: type[BaseModel]
    document_model_type: type[BaseModel]

    @property
    def is_wrapped(self) -> bool:
        return self.wrapper is not None

    def validate_document(self, data: Any) -> BaseModel:
        if self.wrapper is None:
            return self.document_model_type.model_validate(data)
        if not isinstance(data, dict):
            raise ValueError("catalog document must be a mapping")
        if len(data) != 1:
            raise ValueError("catalog document must contain exactly one top-level object wrapper")
        wrapper = next(iter(data))
        if wrapper != self.wrapper:
            raise ValueError(
                f"catalog family {self.family!r} requires wrapper {self.wrapper!r}, "
                f"found {wrapper!r}"
            )
        document = self.document_model_type.model_validate(data)
        return cast(BaseModel, getattr(document, self.wrapper))


def _family_spec(
    family: CatalogFamily,
    document_model_type: type[BaseModel],
) -> CatalogFamilySpec:
    if document_model_type is SegmentSessionConfig:
        return CatalogFamilySpec(
            family=family,
            wrapper=None,
            model_type=SegmentSessionConfig,
            document_model_type=SegmentSessionConfig,
        )
    fields = tuple(document_model_type.model_fields.items())
    if len(fields) != 1:
        raise RuntimeError(f"catalog document model for {family!r} must have exactly one field")
    wrapper, field = fields[0]
    model_type = field.annotation
    if not isinstance(model_type, type) or not issubclass(model_type, BaseModel):
        raise RuntimeError(f"catalog document model for {family!r} must wrap one BaseModel")
    return CatalogFamilySpec(
        family=family,
        wrapper=wrapper,
        model_type=model_type,
        document_model_type=document_model_type,
    )


CATALOG_FAMILY_REGISTRY: Mapping[CatalogFamily, CatalogFamilySpec] = MappingProxyType(
    {
        family: _family_spec(family, document_model_type)
        for family, document_model_type in CONFIGURATION_DOCUMENT_MODELS.items()
    }
)

CATALOG_WRAPPER_TO_FAMILY: Mapping[str, CatalogFamily] = MappingProxyType(
    {
        spec.wrapper: family
        for family, spec in CATALOG_FAMILY_REGISTRY.items()
        if spec.wrapper is not None
    }
)


def catalog_family_spec(family: str) -> CatalogFamilySpec:
    family_key = cast(CatalogFamily, family)
    try:
        return CATALOG_FAMILY_REGISTRY[family_key]
    except KeyError as exc:
        raise ValueError(f"unknown catalog family {family!r}") from exc


def validate_configuration_document(family: str, data: Any) -> BaseModel:
    return catalog_family_spec(family).validate_document(data)


def validate_referenced_document_identity(ref: CatalogRef, model: BaseModel) -> None:
    """Require a referenced document's identity to match its filename stem."""

    family = ref.family
    if family is None:
        raise ValueError(f"catalog reference {ref!r} has no registered family")
    identity = model.session.name if family == "sessions" else getattr(model, "id", None)
    filename_identity = ref.relative_path.stem
    if identity != filename_identity:
        field = "session.name" if family == "sessions" else "object id"
        raise ValueError(
            f"{field} {identity!r} must match filename stem {filename_identity!r} "
            f"referenced by {ref}"
        )


def validate_referenced_configuration_document(
    ref: str | CatalogRef,
    data: Any,
) -> tuple[str | None, BaseModel]:
    """Validate one referenced document through its family and identity contract."""

    parsed = ref if isinstance(ref, CatalogRef) else CatalogRef(ref)
    family = parsed.family
    if family is None:
        raise ValueError(f"catalog reference {parsed!r} has no registered family")
    spec = catalog_family_spec(family)
    model = spec.validate_document(data)
    validate_referenced_document_identity(parsed, model)
    return spec.wrapper, model


def validate_catalog_document(data: Any) -> tuple[str, BaseModel]:
    """Validate one wrapped component document through the canonical registry."""

    if not isinstance(data, dict):
        raise ValueError("catalog document must be a mapping")
    if len(data) != 1:
        raise ValueError("catalog document must contain exactly one top-level object wrapper")
    wrapper = next(iter(data))
    try:
        family = CATALOG_WRAPPER_TO_FAMILY[wrapper]
    except KeyError as exc:
        raise ValueError(f"unsupported catalog object wrapper {wrapper!r}") from exc
    model = CATALOG_FAMILY_REGISTRY[family].validate_document(data)
    return wrapper, model
