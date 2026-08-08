"""Bounded transport for ordinary session and catalog YAML files."""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from enum import StrEnum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from nodalarc.catalog_closure import (
    CatalogClosureCollector,
    CatalogClosureEntry,
    CatalogClosureError,
    CatalogReadDocument,
    catalog_closure_digest,
)
from nodalarc.catalog_refs import CatalogRef
from nodalarc.content_identity import Sha256Digest, sha256_digest
from nodalarc.prepared_session import PreparedSessionFiles

_UPLOAD_ID_PATTERN = r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$"

UploadId = Annotated[str, StringConstraints(pattern=_UPLOAD_ID_PATTERN)]


class CatalogUploadLimits(BaseModel):
    """Hard bounds for one ordinary-YAML deployment upload."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    max_root_yaml_bytes: int = Field(gt=0)
    max_file_bytes: int = Field(gt=0)
    max_file_count: int = Field(gt=0)
    max_aggregate_bytes: int = Field(gt=0)


DEFAULT_CATALOG_UPLOAD_LIMITS = CatalogUploadLimits(
    max_root_yaml_bytes=256 * 1024,
    max_file_bytes=512 * 1024,
    max_file_count=4096,
    max_aggregate_bytes=16 * 1024 * 1024,
)


class CatalogUploadSelection(BaseModel):
    """Small CR selection for one namespaced set of ordinary YAML files."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    upload_id: UploadId
    closure_digest: Sha256Digest
    file_count: int = Field(ge=0)


@dataclass(frozen=True, slots=True)
class CatalogUpload:
    """Exact root YAML and every referenced ordinary catalog YAML file."""

    selection: CatalogUploadSelection
    root_yaml: bytes
    catalog_files: tuple[CatalogClosureEntry, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.selection, CatalogUploadSelection):
            raise TypeError("selection must be a CatalogUploadSelection")
        if not isinstance(self.root_yaml, bytes):
            raise TypeError("root_yaml must be bytes")
        if not self.root_yaml:
            raise ValueError("root_yaml must not be empty")
        if not isinstance(self.catalog_files, tuple) or not all(
            isinstance(entry, CatalogClosureEntry) for entry in self.catalog_files
        ):
            raise TypeError("catalog_files must be a tuple of CatalogClosureEntry values")

    @property
    def upload_id(self) -> str:
        return self.selection.upload_id

    @property
    def file_count(self) -> int:
        return len(self.catalog_files)

    @property
    def total_bytes(self) -> int:
        return len(self.root_yaml) + sum(entry.size_bytes for entry in self.catalog_files)


class CatalogUploadErrorCode(StrEnum):
    LIMIT_EXCEEDED = "catalog_upload.limit_exceeded"
    INVALID_UPLOAD = "catalog_upload.invalid_upload"
    CLOSURE_MISMATCH = "catalog_upload.closure_mismatch"


@dataclass(frozen=True, slots=True)
class CatalogUploadErrorEvidence:
    code: CatalogUploadErrorCode
    message: str
    upload_id: str | None = None
    ref: str | None = None
    limit_name: str | None = None
    maximum: int | None = None
    actual: int | None = None
    expected: str | int | None = None
    observed: str | int | None = None
    cause_type: str | None = None


class CatalogUploadError(ValueError):
    """Typed refusal for invalid or over-limit ordinary YAML uploads."""

    def __init__(self, evidence: CatalogUploadErrorEvidence) -> None:
        super().__init__(evidence.message)
        self.evidence = evidence

    @property
    def code(self) -> CatalogUploadErrorCode:
        return self.evidence.code


def _error(
    code: CatalogUploadErrorCode,
    message: str,
    *,
    upload_id: str | None = None,
    ref: str | None = None,
    limit_name: str | None = None,
    maximum: int | None = None,
    actual: int | None = None,
    expected: str | int | None = None,
    observed: str | int | None = None,
    cause: BaseException | None = None,
) -> CatalogUploadError:
    return CatalogUploadError(
        CatalogUploadErrorEvidence(
            code=code,
            message=message,
            upload_id=upload_id,
            ref=ref,
            limit_name=limit_name,
            maximum=maximum,
            actual=actual,
            expected=expected,
            observed=observed,
            cause_type=type(cause).__name__ if cause is not None else None,
        )
    )


def _check_limit(name: str, actual: int, maximum: int, *, upload_id: str | None = None) -> None:
    if actual <= maximum:
        return
    raise _error(
        CatalogUploadErrorCode.LIMIT_EXCEEDED,
        f"Catalog upload exceeds {name}: {actual} > {maximum}",
        upload_id=upload_id,
        limit_name=name,
        maximum=maximum,
        actual=actual,
    )


def _check_bounds(
    root_yaml: bytes,
    catalog_files: tuple[CatalogClosureEntry, ...],
    limits: CatalogUploadLimits,
    *,
    upload_id: str | None = None,
) -> None:
    if not isinstance(limits, CatalogUploadLimits):
        raise TypeError("limits must be a CatalogUploadLimits instance")
    _check_limit(
        "max_root_yaml_bytes",
        len(root_yaml),
        limits.max_root_yaml_bytes,
        upload_id=upload_id,
    )
    _check_limit(
        "max_file_count",
        len(catalog_files),
        limits.max_file_count,
        upload_id=upload_id,
    )
    _check_limit(
        "max_aggregate_bytes",
        len(root_yaml) + sum(entry.size_bytes for entry in catalog_files),
        limits.max_aggregate_bytes,
        upload_id=upload_id,
    )
    for entry in catalog_files:
        if entry.size_bytes != len(entry.yaml_bytes):
            raise _error(
                CatalogUploadErrorCode.INVALID_UPLOAD,
                f"Catalog file {entry.ref} size does not match its exact YAML bytes",
                upload_id=upload_id,
                ref=str(entry.ref),
                expected=entry.size_bytes,
                observed=len(entry.yaml_bytes),
            )
        _check_limit(
            "max_file_bytes",
            entry.size_bytes,
            limits.max_file_bytes,
            upload_id=upload_id,
        )


@dataclass(frozen=True, slots=True)
class _UploadReadView:
    entries: dict[CatalogRef, CatalogClosureEntry]

    def read(self, ref: CatalogRef) -> CatalogReadDocument:
        entry = self.entries[ref]
        return CatalogReadDocument(
            family=entry.family,
            preserved_path=entry.preserved_path,
            yaml_bytes=entry.yaml_bytes,
        )


def _new_upload_id() -> str:
    return f"upload-{secrets.token_hex(12)}"


def encode_catalog_upload(
    prepared: PreparedSessionFiles,
    *,
    upload_id: str | None = None,
    limits: CatalogUploadLimits = DEFAULT_CATALOG_UPLOAD_LIMITS,
) -> CatalogUpload:
    """Create one fresh bounded upload without changing any YAML bytes."""

    if not isinstance(prepared, PreparedSessionFiles):
        raise TypeError("prepared must be a PreparedSessionFiles instance")
    selected_id = CatalogUploadSelection.model_validate(
        {
            "upload_id": upload_id or _new_upload_id(),
            "closure_digest": prepared.closure_digest,
            "file_count": len(prepared.catalog_files),
        },
        strict=True,
    )
    _check_bounds(
        prepared.root_yaml,
        prepared.catalog_files,
        limits,
        upload_id=selected_id.upload_id,
    )
    observed_digest = catalog_closure_digest(prepared.catalog_files)
    if observed_digest != prepared.closure_digest:
        raise _error(
            CatalogUploadErrorCode.CLOSURE_MISMATCH,
            "Prepared catalog files do not match the prepared closure digest",
            upload_id=selected_id.upload_id,
            expected=prepared.closure_digest,
            observed=observed_digest,
        )
    return verify_catalog_upload(
        CatalogUpload(
            selection=selected_id,
            root_yaml=prepared.root_yaml,
            catalog_files=prepared.catalog_files,
        ),
        limits=limits,
    )


def verify_catalog_upload(
    upload: CatalogUpload,
    *,
    limits: CatalogUploadLimits = DEFAULT_CATALOG_UPLOAD_LIMITS,
) -> CatalogUpload:
    """Prove that the supplied ordinary files are exactly the root's closure."""

    if not isinstance(upload, CatalogUpload):
        raise TypeError("upload must be a CatalogUpload")
    _check_bounds(
        upload.root_yaml,
        upload.catalog_files,
        limits,
        upload_id=upload.upload_id,
    )

    entries_by_ref: dict[CatalogRef, CatalogClosureEntry] = {}
    for entry in upload.catalog_files:
        if entry.ref in entries_by_ref:
            raise _error(
                CatalogUploadErrorCode.CLOSURE_MISMATCH,
                f"Catalog upload contains duplicate ref {entry.ref}",
                upload_id=upload.upload_id,
                ref=str(entry.ref),
            )
        if sha256_digest(entry.yaml_bytes) != entry.document_digest:
            raise _error(
                CatalogUploadErrorCode.INVALID_UPLOAD,
                f"Catalog file {entry.ref} digest does not match its exact YAML bytes",
                upload_id=upload.upload_id,
                ref=str(entry.ref),
                expected=entry.document_digest,
                observed=sha256_digest(entry.yaml_bytes),
            )
        entries_by_ref[entry.ref] = entry

    try:
        closure = CatalogClosureCollector.collect(
            upload.root_yaml,
            _UploadReadView(entries_by_ref),
        )
    except (CatalogClosureError, KeyError) as exc:
        raise _error(
            CatalogUploadErrorCode.CLOSURE_MISMATCH,
            f"Catalog upload does not satisfy the root session closure: {exc}",
            upload_id=upload.upload_id,
            cause=exc,
        ) from exc

    expected_refs = frozenset(entries_by_ref)
    observed_refs = frozenset(entry.ref for entry in closure.entries)
    if observed_refs != expected_refs:
        missing = sorted(str(ref) for ref in observed_refs - expected_refs)
        extra = sorted(str(ref) for ref in expected_refs - observed_refs)
        details = []
        if missing:
            details.append("missing: " + ", ".join(missing))
        if extra:
            details.append("extra: " + ", ".join(extra))
        raise _error(
            CatalogUploadErrorCode.CLOSURE_MISMATCH,
            "Catalog upload reference set mismatch (" + "; ".join(details) + ")",
            upload_id=upload.upload_id,
        )
    if closure.closure_digest != upload.selection.closure_digest:
        raise _error(
            CatalogUploadErrorCode.CLOSURE_MISMATCH,
            "Catalog upload closure digest does not match its selection",
            upload_id=upload.upload_id,
            expected=upload.selection.closure_digest,
            observed=closure.closure_digest,
        )
    if len(closure.entries) != upload.selection.file_count:
        raise _error(
            CatalogUploadErrorCode.CLOSURE_MISMATCH,
            "Catalog upload file count does not match its selection",
            upload_id=upload.upload_id,
            expected=upload.selection.file_count,
            observed=len(closure.entries),
        )
    return upload
