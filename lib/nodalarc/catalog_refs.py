"""Typed catalog-reference tokens shared by grammar and path consumers."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar, Literal, Self, get_args

from pydantic.json_schema import GetJsonSchemaHandler, JsonSchemaValue
from pydantic_core import core_schema

CatalogNamespace = Literal["nodalarc", "user"]
CatalogFamily = Literal[
    "bodies",
    "terminals",
    "payloads",
    "profiles",
    "orbits",
    "nodes",
    "sites",
    "site-sets",
    "constellations",
    "space-node-sets",
    "sessions",
]

_CATALOG_NAMESPACES: tuple[CatalogNamespace, ...] = ("nodalarc", "user")
_CATALOG_FAMILIES = frozenset(get_args(CatalogFamily))
_SAFE_NAME = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
_YAML_SUFFIXES = {".yaml", ".yml"}


class CatalogReferenceError(ValueError):
    """Raised when a catalog-reference token is structurally invalid."""


@dataclass(frozen=True)
class ParsedCatalogReference:
    """A validated reference token split into namespace and relative path."""

    namespace: CatalogNamespace
    relative_path: Path
    family: str | None


def catalog_reference_namespace(source: str | Path) -> CatalogNamespace | None:
    """Return the namespace prefix of a catalog token, if it has one."""

    raw = str(source)
    for namespace in _CATALOG_NAMESPACES:
        if raw.startswith(f"{namespace}:"):
            return namespace
    return None


def is_catalog_name(value: str) -> bool:
    """Return whether a path component matches the accepted catalog spelling."""

    return bool(_SAFE_NAME.fullmatch(value))


def validate_catalog_name(value: str, *, label: str = "name") -> str:
    """Validate one catalog path component without changing its spelling."""

    if not isinstance(value, str):
        raise CatalogReferenceError(f"{label} must be a string")
    if "/" in value or "\\" in value:
        raise CatalogReferenceError(f"{label} must not contain path separators")
    if value == ".." or ".." in Path(value).parts:
        raise CatalogReferenceError(f"{label} must not contain path traversal")
    if not is_catalog_name(value):
        raise CatalogReferenceError(
            f"{label} must start with a lowercase letter or digit and contain only "
            "lowercase letters, digits, '-' and '_'"
        )
    return value


def parse_catalog_reference(
    source: str | Path,
    *,
    expected_families: frozenset[str] | None = _CATALOG_FAMILIES,
    label: str = "catalog reference",
) -> ParsedCatalogReference:
    """Parse a contained-path token without reading either catalog root.

    Generic callers intentionally retain the catalog path spellings accepted by
    the existing loader. Typed grammar aliases pass ``expected_families`` to
    additionally enforce the family directory for their catalog-valued slot.
    """

    raw = str(source)
    namespace = catalog_reference_namespace(raw)
    if namespace is None:
        raise CatalogReferenceError(f"{label} must be a nodalarc:<path> or user:<path> reference")

    relative = raw.split(":", 1)[1]
    if not relative:
        raise CatalogReferenceError(f"{label} is required")
    if "\\" in relative:
        raise CatalogReferenceError(f"{label} must not contain backslash path separators")
    raw_parts = relative.split("/")
    if any(part == "" for part in raw_parts):
        raise CatalogReferenceError(f"{label} must not contain empty path components")
    if any(part == "." for part in raw_parts):
        raise CatalogReferenceError(f"{label} must not contain dot path components")

    path = Path(relative)
    if path.is_absolute():
        raise CatalogReferenceError(f"{label} must not be absolute")
    if ".." in path.parts:
        raise CatalogReferenceError(f"{label} must not contain path traversal")
    if not path.parts:
        raise CatalogReferenceError(f"{label} path is required")

    filename = path.name
    suffix = Path(filename).suffix
    if suffix not in _YAML_SUFFIXES:
        raise CatalogReferenceError(f"{label} path must be YAML")

    parts = [validate_catalog_name(part, label=f"{label} directory") for part in path.parts[:-1]]
    stem = validate_catalog_name(Path(filename).stem, label=f"{label} filename")
    relative_path = Path(*parts, f"{stem}{suffix}")
    family = parts[0] if parts else None

    if expected_families is not None and family not in expected_families:
        expected = " or ".join(sorted(expected_families))
        raise CatalogReferenceError(f"{label} must reference the {expected} catalog family")

    return ParsedCatalogReference(
        namespace=namespace,
        relative_path=relative_path,
        family=family,
    )


class CatalogRef(str):
    """A validated catalog token that remains distinguishable from prose."""

    allowed_families: ClassVar[frozenset[str] | None] = _CATALOG_FAMILIES

    @classmethod
    def json_schema_pattern(cls) -> str:
        component = r"[a-z0-9][a-z0-9_-]*"
        suffix = r"(?:yaml|yml)"
        if cls.allowed_families is None:
            path = rf"(?:{component}/)*{component}"
        else:
            families = "|".join(re.escape(family) for family in sorted(cls.allowed_families))
            path = rf"(?:{families})/(?:{component}/)*{component}"
        return rf"^(?:nodalarc|user):{path}\.{suffix}$"

    def __new__(cls, value: str) -> Self:
        parsed = parse_catalog_reference(value, expected_families=cls.allowed_families)
        canonical = f"{parsed.namespace}:{parsed.relative_path.as_posix()}"
        return super().__new__(cls, canonical)

    def _parsed(self) -> ParsedCatalogReference:
        return parse_catalog_reference(self, expected_families=type(self).allowed_families)

    @property
    def namespace(self) -> CatalogNamespace:
        return self._parsed().namespace

    @property
    def family(self) -> str | None:
        return self._parsed().family

    @property
    def relative_path(self) -> Path:
        return self._parsed().relative_path

    @classmethod
    def __get_pydantic_core_schema__(
        cls,
        _source_type: object,
        _handler: object,
    ) -> core_schema.CoreSchema:
        return core_schema.no_info_after_validator_function(
            cls,
            core_schema.str_schema(),
            serialization=core_schema.plain_serializer_function_ser_schema(
                str,
                return_schema=core_schema.str_schema(),
            ),
        )

    @classmethod
    def __get_pydantic_json_schema__(
        cls,
        schema: core_schema.CoreSchema,
        handler: GetJsonSchemaHandler,
    ) -> JsonSchemaValue:
        json_schema = handler(schema)
        json_schema["pattern"] = cls.json_schema_pattern()
        return json_schema


class BodyRef(CatalogRef):
    allowed_families = frozenset({"bodies"})


class TerminalRef(CatalogRef):
    allowed_families = frozenset({"terminals"})


class PayloadRef(CatalogRef):
    allowed_families = frozenset({"payloads"})


class ProfileRef(CatalogRef):
    allowed_families = frozenset({"profiles"})


class OrbitRef(CatalogRef):
    allowed_families = frozenset({"orbits"})


class NodeRef(CatalogRef):
    allowed_families = frozenset({"nodes"})


class SiteRef(CatalogRef):
    allowed_families = frozenset({"sites"})


class SiteSetRef(CatalogRef):
    allowed_families = frozenset({"site-sets"})


class ConstellationRef(CatalogRef):
    allowed_families = frozenset({"constellations"})


class SpaceNodeSetRef(CatalogRef):
    allowed_families = frozenset({"space-node-sets"})


class SessionRef(CatalogRef):
    allowed_families = frozenset({"sessions"})


class SpaceSourceRef(CatalogRef):
    allowed_families = frozenset({"constellations", "space-node-sets"})
