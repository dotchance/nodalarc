"""Deterministic transitive collection of strict persisted catalog documents."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from pathlib import PurePosixPath
from typing import Any, Protocol, cast

import yaml
from pydantic import BaseModel, ValidationError

from nodalarc.catalog_paths import CatalogPathError, CatalogRoots, resolve_catalog_reference
from nodalarc.catalog_refs import (
    CatalogFamily,
    CatalogRef,
    CatalogReferenceError,
    catalog_reference_namespace,
)
from nodalarc.catalog_registry import (
    catalog_family_spec,
    validate_referenced_configuration_document,
)
from nodalarc.configuration_yaml import load_configuration_yaml


class CatalogClosureErrorCode(StrEnum):
    INVALID_ROOT_YAML = "catalog_closure.invalid_root_yaml"
    INVALID_SESSION_ROOT = "catalog_closure.invalid_session_root"
    INVALID_REFERENCE = "catalog_closure.invalid_reference"
    REFERENCE_PATH_REJECTED = "catalog_closure.reference_path_rejected"
    REFERENCE_FAMILY_MISMATCH = "catalog_closure.reference_family_mismatch"
    DANGLING_REFERENCE = "catalog_closure.dangling_reference"
    READ_FAILED = "catalog_closure.read_failed"
    READ_VIEW_CONTRACT = "catalog_closure.read_view_contract"
    INVALID_DEPENDENCY_YAML = "catalog_closure.invalid_dependency_yaml"
    INVALID_DEPENDENCY_DOCUMENT = "catalog_closure.invalid_dependency_document"
    FAMILY_WRAPPER_MISMATCH = "catalog_closure.family_wrapper_mismatch"
    REFERENCE_CYCLE = "catalog_closure.reference_cycle"
    PRESERVED_PATH_COLLISION = "catalog_closure.preserved_path_collision"


@dataclass(frozen=True)
class CatalogClosureErrorEvidence:
    code: CatalogClosureErrorCode
    message: str
    ref: str | None = None
    family: str | None = None
    preserved_path: str | None = None
    dependency_chain: tuple[str, ...] = ()
    conflicting_ref: str | None = None
    cause_type: str | None = None


class CatalogClosureError(ValueError):
    """One typed, actionable catalog-closure refusal."""

    def __init__(self, evidence: CatalogClosureErrorEvidence) -> None:
        super().__init__(evidence.message)
        self.evidence = evidence

    @property
    def code(self) -> CatalogClosureErrorCode:
        return self.evidence.code


@dataclass(frozen=True)
class CatalogReadDocument:
    family: CatalogFamily
    preserved_path: str
    yaml_bytes: bytes


class CatalogReadView(Protocol):
    """A bounded source of exact YAML bytes for validated catalog refs."""

    def read(self, ref: CatalogRef) -> CatalogReadDocument: ...


def preserved_catalog_path(ref: CatalogRef) -> str:
    """Return the namespace-preserving deployment path for one catalog ref."""
    return PurePosixPath("catalog", ref.namespace, *ref.relative_path.parts).as_posix()


@dataclass(frozen=True)
class FilesystemCatalogReadView:
    """Read exact bytes through the existing contained ``CatalogRoots`` policy."""

    roots: CatalogRoots

    def read(self, ref: CatalogRef) -> CatalogReadDocument:
        family = ref.family
        if family is None:
            raise CatalogReferenceError(f"catalog reference {ref!r} has no family directory")
        catalog_family_spec(family)
        path = resolve_catalog_reference(ref, self.roots, label="catalog closure reference")
        return CatalogReadDocument(
            family=cast(CatalogFamily, family),
            preserved_path=preserved_catalog_path(ref),
            yaml_bytes=path.read_bytes(),
        )


@dataclass(frozen=True)
class CatalogClosureEntry:
    ref: CatalogRef
    family: CatalogFamily
    preserved_path: str
    yaml_bytes: bytes
    document_digest: str
    size_bytes: int


@dataclass(frozen=True)
class CatalogDependencyGraph:
    entries: tuple[CatalogClosureEntry, ...]
    closure_digest: str
    file_count: int
    total_bytes: int


@dataclass(frozen=True)
class CatalogClosure:
    root_yaml: bytes
    entries: tuple[CatalogClosureEntry, ...]
    document_digest: str
    closure_digest: str
    file_count: int
    total_bytes: int

    @property
    def deployment_file_count(self) -> int:
        return 1 + self.file_count

    @property
    def deployment_total_bytes(self) -> int:
        return len(self.root_yaml) + self.total_bytes


def _sha256(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def _error(
    code: CatalogClosureErrorCode,
    message: str,
    *,
    ref: str | None = None,
    family: str | None = None,
    preserved_path: str | None = None,
    dependency_chain: tuple[str, ...] = (),
    conflicting_ref: str | None = None,
    cause: BaseException | None = None,
) -> CatalogClosureError:
    return CatalogClosureError(
        CatalogClosureErrorEvidence(
            code=code,
            message=message,
            ref=ref,
            family=family,
            preserved_path=preserved_path,
            dependency_chain=dependency_chain,
            conflicting_ref=conflicting_ref,
            cause_type=type(cause).__name__ if cause is not None else None,
        )
    )


def _reference_from_validation(error: BaseException) -> str | None:
    if not isinstance(error, ValidationError):
        return None
    for item in error.errors():
        value = item.get("input")
        if isinstance(value, str) and catalog_reference_namespace(value) is not None:
            return value
    return None


def _validation_error_code(
    error: BaseException,
    *,
    root: bool,
) -> CatalogClosureErrorCode:
    message = str(error)
    if "requires wrapper" in message or "exactly one top-level object wrapper" in message:
        return CatalogClosureErrorCode.FAMILY_WRAPPER_MISMATCH
    if "catalog family" in message:
        return CatalogClosureErrorCode.REFERENCE_FAMILY_MISMATCH
    path_evidence = (
        "path traversal",
        "must not be absolute",
        "backslash path separators",
        "path separators",
        "path must be YAML",
        "directory must contain only",
        "filename must contain only",
    )
    if any(token in message for token in path_evidence):
        return CatalogClosureErrorCode.REFERENCE_PATH_REJECTED
    if "must be a nodalarc:<path> or user:<path> reference" in message:
        return CatalogClosureErrorCode.INVALID_REFERENCE
    return (
        CatalogClosureErrorCode.INVALID_SESSION_ROOT
        if root
        else CatalogClosureErrorCode.INVALID_DEPENDENCY_DOCUMENT
    )


def _parse_yaml(
    yaml_bytes: bytes,
    *,
    ref: CatalogRef | None,
    dependency_chain: tuple[str, ...],
) -> Any:
    try:
        return load_configuration_yaml(yaml_bytes)
    except (UnicodeError, yaml.YAMLError) as exc:
        code = (
            CatalogClosureErrorCode.INVALID_ROOT_YAML
            if ref is None
            else CatalogClosureErrorCode.INVALID_DEPENDENCY_YAML
        )
        subject = "root session" if ref is None else f"catalog dependency {ref}"
        raise _error(
            code,
            f"Invalid YAML in {subject}: {exc}",
            ref=str(ref) if ref is not None else None,
            family=ref.family if ref is not None else "sessions",
            dependency_chain=dependency_chain,
            cause=exc,
        ) from exc


def _validate_document(
    family: str,
    data: Any,
    *,
    ref: CatalogRef | None,
    dependency_chain: tuple[str, ...],
) -> BaseModel:
    root = ref is None
    try:
        if ref is None:
            model = catalog_family_spec(family).validate_document(data)
        else:
            _wrapper, model = validate_referenced_configuration_document(ref, data)
        return model
    except (ValidationError, ValueError, TypeError) as exc:
        offending_ref = _reference_from_validation(exc)
        code = _validation_error_code(exc, root=root)
        subject = "persisted session root" if root else f"catalog dependency {ref}"
        raise _error(
            code,
            f"Invalid {subject}: {exc}",
            ref=offending_ref or (str(ref) if ref is not None else None),
            family=family,
            dependency_chain=dependency_chain,
            cause=exc,
        ) from exc


def catalog_document_references(value: Any) -> tuple[CatalogRef, ...]:
    """Return only grammar-typed catalog references contained in a model.

    Plain strings that merely contain ``nodalarc:`` or ``user:`` text are not
    references. Callers must validate persisted input into the executable
    grammar models before using this helper.
    """

    found: list[CatalogRef] = []

    def walk(item: Any) -> None:
        if isinstance(item, CatalogRef):
            found.append(item)
            return
        if isinstance(item, BaseModel):
            for field_name in type(item).model_fields:
                walk(getattr(item, field_name))
            return
        if isinstance(item, Mapping):
            for key, nested in item.items():
                walk(key)
                walk(nested)
            return
        if isinstance(item, tuple | list | set | frozenset):
            for nested in item:
                walk(nested)

    walk(value)
    return tuple(found)


def catalog_closure_digest(entries: Iterable[CatalogClosureEntry]) -> str:
    """Return the canonical identity of an ordered catalog dependency closure."""
    ordered_entries = tuple(entries)
    inventory = {
        "schema": "nodalarc.catalog-dependency-closure.v1",
        "entries": [
            {
                "ref": str(entry.ref),
                "family": entry.family,
                "preserved_path": entry.preserved_path,
                "document_digest": entry.document_digest,
                "size_bytes": entry.size_bytes,
            }
            for entry in ordered_entries
        ],
    }
    payload = json.dumps(
        inventory,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return _sha256(payload)


class _CollectionState:
    def __init__(self, read_view: CatalogReadView) -> None:
        self.read_view = read_view
        self.entries: dict[str, CatalogClosureEntry] = {}
        self.preserved_path_owners: dict[str, str] = {}
        self.active: list[str] = []

    def visit(self, ref: CatalogRef) -> None:
        key = str(ref)
        if key in self.active:
            start = self.active.index(key)
            cycle = (*self.active[start:], key)
            raise _error(
                CatalogClosureErrorCode.REFERENCE_CYCLE,
                "Catalog dependency cycle detected: " + " -> ".join(cycle),
                ref=key,
                family=ref.family,
                dependency_chain=cycle,
            )
        if key in self.entries:
            return

        family = ref.family
        if family is None:
            raise _error(
                CatalogClosureErrorCode.REFERENCE_FAMILY_MISMATCH,
                f"Catalog dependency {ref} has no registered family",
                ref=key,
                dependency_chain=(*self.active, key),
            )
        try:
            catalog_family_spec(family)
        except ValueError as exc:
            raise _error(
                CatalogClosureErrorCode.REFERENCE_FAMILY_MISMATCH,
                f"Catalog dependency {ref} uses unknown family {family!r}",
                ref=key,
                family=family,
                dependency_chain=(*self.active, key),
                cause=exc,
            ) from exc

        chain = (*self.active, key)
        self.active.append(key)
        try:
            document = self._read(ref, chain)
            expected_path = preserved_catalog_path(ref)
            if document.family != family:
                raise _error(
                    CatalogClosureErrorCode.REFERENCE_FAMILY_MISMATCH,
                    f"Read view returned family {document.family!r} for {ref}; expected {family!r}",
                    ref=key,
                    family=family,
                    preserved_path=document.preserved_path,
                    dependency_chain=chain,
                )
            if document.preserved_path != expected_path:
                raise _error(
                    CatalogClosureErrorCode.READ_VIEW_CONTRACT,
                    f"Read view returned preserved path {document.preserved_path!r} for {ref}; "
                    f"expected {expected_path!r}",
                    ref=key,
                    family=family,
                    preserved_path=document.preserved_path,
                    dependency_chain=chain,
                )
            if not isinstance(document.yaml_bytes, bytes):
                raise _error(
                    CatalogClosureErrorCode.READ_VIEW_CONTRACT,
                    f"Read view returned non-bytes YAML for {ref}",
                    ref=key,
                    family=family,
                    preserved_path=expected_path,
                    dependency_chain=chain,
                )

            path_owner = self.preserved_path_owners.get(expected_path)
            if path_owner is not None and path_owner != key:
                raise _error(
                    CatalogClosureErrorCode.PRESERVED_PATH_COLLISION,
                    f"Catalog refs {path_owner!r} and {key!r} map to the same preserved path "
                    f"{expected_path!r}",
                    ref=key,
                    family=family,
                    preserved_path=expected_path,
                    dependency_chain=chain,
                    conflicting_ref=path_owner,
                )
            self.preserved_path_owners[expected_path] = key

            data = _parse_yaml(document.yaml_bytes, ref=ref, dependency_chain=chain)
            model = _validate_document(
                family,
                data,
                ref=ref,
                dependency_chain=chain,
            )
            for dependency in sorted(catalog_document_references(model), key=str):
                self.visit(dependency)

            self.entries[key] = CatalogClosureEntry(
                ref=ref,
                family=cast(CatalogFamily, family),
                preserved_path=expected_path,
                yaml_bytes=document.yaml_bytes,
                document_digest=_sha256(document.yaml_bytes),
                size_bytes=len(document.yaml_bytes),
            )
        finally:
            self.active.pop()

    def _read(
        self,
        ref: CatalogRef,
        dependency_chain: tuple[str, ...],
    ) -> CatalogReadDocument:
        try:
            return self.read_view.read(ref)
        except CatalogClosureError:
            raise
        except (CatalogPathError, CatalogReferenceError) as exc:
            raise _error(
                CatalogClosureErrorCode.REFERENCE_PATH_REJECTED,
                f"Catalog dependency path rejected for {ref}: {exc}",
                ref=str(ref),
                family=ref.family,
                dependency_chain=dependency_chain,
                cause=exc,
            ) from exc
        except (FileNotFoundError, KeyError) as exc:
            raise _error(
                CatalogClosureErrorCode.DANGLING_REFERENCE,
                f"Catalog dependency not found for {ref}: {exc}",
                ref=str(ref),
                family=ref.family,
                dependency_chain=dependency_chain,
                cause=exc,
            ) from exc
        except OSError as exc:
            raise _error(
                CatalogClosureErrorCode.READ_FAILED,
                f"Could not read catalog dependency {ref}: {exc}",
                ref=str(ref),
                family=ref.family,
                dependency_chain=dependency_chain,
                cause=exc,
            ) from exc


class CatalogClosureCollector:
    """Collect one exact, typed, transitive dependency closure."""

    @staticmethod
    def collect_references(
        refs: Iterable[str | CatalogRef],
        read_view: CatalogReadView,
    ) -> CatalogDependencyGraph:
        """Validate and collect the union graph reachable from catalog refs."""

        state = _CollectionState(read_view)
        parsed_refs: list[CatalogRef] = []
        for ref in refs:
            try:
                parsed_refs.append(ref if isinstance(ref, CatalogRef) else CatalogRef(ref))
            except (CatalogReferenceError, TypeError, ValueError) as exc:
                raise _error(
                    CatalogClosureErrorCode.INVALID_REFERENCE,
                    f"Invalid catalog graph root {ref!r}: {exc}",
                    ref=str(ref),
                    cause=exc,
                ) from exc

        for ref in sorted(parsed_refs, key=str):
            state.visit(ref)

        entries = tuple(sorted(state.entries.values(), key=lambda entry: str(entry.ref)))
        return CatalogDependencyGraph(
            entries=entries,
            closure_digest=catalog_closure_digest(entries),
            file_count=len(entries),
            total_bytes=sum(entry.size_bytes for entry in entries),
        )

    @staticmethod
    def collect(root_yaml: bytes, read_view: CatalogReadView) -> CatalogClosure:
        if not isinstance(root_yaml, bytes):
            raise _error(
                CatalogClosureErrorCode.INVALID_ROOT_YAML,
                "Root session YAML must be bytes",
                cause=TypeError(type(root_yaml).__name__),
            )
        data = _parse_yaml(root_yaml, ref=None, dependency_chain=())
        root_model = _validate_document(
            "sessions",
            data,
            ref=None,
            dependency_chain=(),
        )

        graph = CatalogClosureCollector.collect_references(
            catalog_document_references(root_model),
            read_view,
        )
        return CatalogClosure(
            root_yaml=root_yaml,
            entries=graph.entries,
            document_digest=_sha256(root_yaml),
            closure_digest=graph.closure_digest,
            file_count=graph.file_count,
            total_bytes=graph.total_bytes,
        )
