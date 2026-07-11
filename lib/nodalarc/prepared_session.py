"""Server-only preparation of exact session files before runtime mutation."""

from __future__ import annotations

import re
import tempfile
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path, PurePosixPath

from nodalarc.catalog_closure import (
    CatalogClosure,
    CatalogClosureCollector,
    CatalogClosureEntry,
    CatalogReadView,
)
from nodalarc.catalog_paths import CatalogRoots
from nodalarc.catalog_refs import SessionRef
from nodalarc.configuration_yaml import load_configuration_yaml
from nodalarc.models.events import ValidationReport, ValidationResult
from nodalarc.models.resolved_session import SourceContext
from nodalarc.resolve_session import SessionResolution, resolve_session_with_assets
from nodalarc.semantic_projection import resolved_session_semantic_digest
from nodalarc.session_validator import build_validation_report, validate_session_readiness

_SHA256_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")
_OPAQUE_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")


@dataclass(frozen=True, slots=True)
class PreparedSessionSource:
    """Logical server-selected source identity with no filesystem authority."""

    logical_id: SessionRef
    origin: str

    def __post_init__(self) -> None:
        if not isinstance(self.origin, str) or not _OPAQUE_ID_PATTERN.fullmatch(self.origin):
            raise ValueError(
                "prepared session source origin must be a non-whitespace logical token"
            )
        object.__setattr__(self, "logical_id", SessionRef(str(self.logical_id)))


class PreparedSessionErrorCode(StrEnum):
    INVALID_DIGEST = "prepared_session.invalid_digest"
    STALE_SOURCE_REVISION = "prepared_session.stale_source_revision"
    STALE_DOCUMENT_DIGEST = "prepared_session.stale_document_digest"
    STALE_CLOSURE_DIGEST = "prepared_session.stale_closure_digest"
    MATERIALIZATION_FAILED = "prepared_session.materialization_failed"
    NOT_READY = "prepared_session.not_ready"


@dataclass(frozen=True, slots=True)
class PreparedSessionErrorEvidence:
    code: PreparedSessionErrorCode
    message: str
    expected: str | None = None
    actual: str | None = None
    readiness_errors: tuple[ValidationResult, ...] = ()
    cause_type: str | None = None


class PreparedSessionError(ValueError):
    """Typed stale, materialization, or readiness refusal."""

    def __init__(
        self,
        evidence: PreparedSessionErrorEvidence,
        *,
        validation_report: ValidationReport | None = None,
    ) -> None:
        super().__init__(evidence.message)
        self.evidence = evidence
        self.validation_report = validation_report

    @property
    def code(self) -> PreparedSessionErrorCode:
        return self.evidence.code


@dataclass(frozen=True, slots=True)
class PreparedSessionFiles:
    """Exact immutable inputs proven safe to carry across teardown."""

    source: PreparedSessionSource
    source_revision: str
    root_yaml: bytes
    catalog_files: tuple[CatalogClosureEntry, ...]
    document_digest: str
    closure_digest: str
    resolved_semantic_digest: str
    resolution: SessionResolution
    validation_report: ValidationReport
    warnings: tuple[ValidationResult, ...]
    file_count: int
    total_bytes: int

    def __post_init__(self) -> None:
        for label, digest in (
            ("source_revision", self.source_revision),
            ("document_digest", self.document_digest),
            ("closure_digest", self.closure_digest),
            ("resolved_semantic_digest", self.resolved_semantic_digest),
        ):
            if not _SHA256_PATTERN.fullmatch(digest):
                raise ValueError(f"prepared {label} must be sha256:<64 lowercase hex>")
        if not isinstance(self.resolution, SessionResolution):
            raise TypeError("prepared resolution must be one SessionResolution")
        if self.resolution.resolved.source_context.session_path is not None:
            raise ValueError("prepared resolution must not retain a filesystem session path")
        if self.file_count != 1 + len(self.catalog_files):
            raise ValueError("prepared file_count must include root YAML and every catalog file")
        expected_bytes = len(self.root_yaml) + sum(entry.size_bytes for entry in self.catalog_files)
        if self.total_bytes != expected_bytes:
            raise ValueError("prepared total_bytes must equal exact root and catalog file bytes")
        if self.validation_report.errors or not self.validation_report.dispatchable:
            raise ValueError("prepared validation report must be deploy-ready")
        if self.warnings != self.validation_report.warnings:
            raise ValueError("prepared warnings must match the validation report")


def _error(
    code: PreparedSessionErrorCode,
    message: str,
    *,
    expected: str | None = None,
    actual: str | None = None,
    readiness_errors: tuple[ValidationResult, ...] = (),
    cause: BaseException | None = None,
    validation_report: ValidationReport | None = None,
) -> PreparedSessionError:
    return PreparedSessionError(
        PreparedSessionErrorEvidence(
            code=code,
            message=message,
            expected=expected,
            actual=actual,
            readiness_errors=readiness_errors,
            cause_type=type(cause).__name__ if cause is not None else None,
        ),
        validation_report=validation_report,
    )


def _validated_digest(value: str, *, label: str) -> str:
    if not isinstance(value, str) or not _SHA256_PATTERN.fullmatch(value):
        raise _error(
            PreparedSessionErrorCode.INVALID_DIGEST,
            f"{label} must be sha256:<64 lowercase hex>",
            actual=str(value),
        )
    return value


def _compare_precondition(
    expected: str | None,
    actual: str,
    *,
    code: PreparedSessionErrorCode,
    label: str,
) -> None:
    if expected is None:
        return
    if expected != actual:
        raise _error(
            code,
            f"Stale {label}: expected {expected}, current value is {actual}",
            expected=expected,
            actual=actual,
        )


def _materialization_target(root: Path, preserved_path: str) -> Path:
    relative = PurePosixPath(preserved_path)
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError(f"preserved path is not contained: {preserved_path!r}")
    if len(relative.parts) < 3 or relative.parts[0] != "catalog":
        raise ValueError(f"preserved path is not a catalog path: {preserved_path!r}")
    if relative.parts[1] not in {"nodalarc", "user"}:
        raise ValueError(f"preserved path has unknown namespace: {preserved_path!r}")
    target = root.joinpath(*relative.parts)
    try:
        target.resolve(strict=False).relative_to(root.resolve(strict=True))
    except ValueError as exc:
        raise ValueError(
            f"preserved path escapes materialization root: {preserved_path!r}"
        ) from exc
    return target


def _write_exact(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    if path.read_bytes() != content:
        raise OSError(f"exact-byte verification failed for {path.name}")


def _materialize(closure: CatalogClosure, root: Path) -> CatalogRoots:
    try:
        _write_exact(root / "session.yaml", closure.root_yaml)
        shipped_root = root / "catalog" / "nodalarc"
        user_root = root / "catalog" / "user"
        shipped_root.mkdir(parents=True, exist_ok=True)
        user_root.mkdir(parents=True, exist_ok=True)
        for entry in closure.entries:
            _write_exact(
                _materialization_target(root, entry.preserved_path),
                entry.yaml_bytes,
            )
        return CatalogRoots.from_catalog_root(shipped_root, user_root=user_root)
    except (OSError, ValueError) as exc:
        raise _error(
            PreparedSessionErrorCode.MATERIALIZATION_FAILED,
            f"Could not materialize exact prepared session files: {exc}",
            cause=exc,
        ) from exc


def prepare_session_files(
    root_yaml: bytes,
    read_view: CatalogReadView,
    *,
    source: PreparedSessionSource,
    source_revision: str,
    expected_source_revision: str | None = None,
    expected_document_digest: str | None = None,
    expected_closure_digest: str | None = None,
    available_node_count: int,
    run_id: str | None = None,
) -> PreparedSessionFiles:
    """Collect, precondition-check, resolve once, and gate one exact file set."""
    actual_source_revision = _validated_digest(source_revision, label="source_revision")
    expected_revision = (
        _validated_digest(expected_source_revision, label="expected_source_revision")
        if expected_source_revision is not None
        else None
    )
    expected_document = (
        _validated_digest(expected_document_digest, label="expected_document_digest")
        if expected_document_digest is not None
        else None
    )
    expected_closure = (
        _validated_digest(expected_closure_digest, label="expected_closure_digest")
        if expected_closure_digest is not None
        else None
    )
    if not isinstance(available_node_count, int) or isinstance(available_node_count, bool):
        raise TypeError("available_node_count must be an integer")
    if available_node_count < 0:
        raise ValueError("available_node_count must be non-negative")

    source_context = SourceContext(origin=source.origin, run_id=run_id)

    _compare_precondition(
        expected_revision,
        actual_source_revision,
        code=PreparedSessionErrorCode.STALE_SOURCE_REVISION,
        label="source revision",
    )

    closure = CatalogClosureCollector.collect(root_yaml, read_view)

    _compare_precondition(
        expected_document,
        closure.document_digest,
        code=PreparedSessionErrorCode.STALE_DOCUMENT_DIGEST,
        label="session document digest",
    )
    _compare_precondition(
        expected_closure,
        closure.closure_digest,
        code=PreparedSessionErrorCode.STALE_CLOSURE_DIGEST,
        label="dependency closure digest",
    )

    with tempfile.TemporaryDirectory(prefix="nodalarc-prepared-") as temp_dir:
        roots = _materialize(closure, Path(temp_dir))
        raw_session = load_configuration_yaml(closure.root_yaml)
        resolution = resolve_session_with_assets(
            raw_session,
            catalog_roots=roots,
            source_context=source_context,
        )

    readiness = validate_session_readiness(
        resolution.resolved,
        available_node_count=available_node_count,
    )
    report = build_validation_report(resolution.resolved, readiness)
    if report.errors:
        message = "; ".join(f"[{result.code}] {result.message}" for result in report.errors)
        raise _error(
            PreparedSessionErrorCode.NOT_READY,
            f"Prepared session is not deploy-ready: {message}",
            readiness_errors=report.errors,
            validation_report=report,
        )

    return PreparedSessionFiles(
        source=source,
        source_revision=actual_source_revision,
        root_yaml=closure.root_yaml,
        catalog_files=closure.entries,
        document_digest=closure.document_digest,
        closure_digest=closure.closure_digest,
        resolved_semantic_digest=resolved_session_semantic_digest(resolution.resolved),
        resolution=resolution,
        validation_report=report,
        warnings=report.warnings,
        file_count=closure.deployment_file_count,
        total_bytes=closure.deployment_total_bytes,
    )
