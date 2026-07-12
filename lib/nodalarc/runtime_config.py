"""Authoritative runtime loading from one verified ordinary-file catalog upload."""

from __future__ import annotations

import os
import shutil
import tempfile
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path, PurePosixPath
from typing import Annotated, Final, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from nodalarc.catalog_closure import CatalogClosureEntry
from nodalarc.catalog_paths import CatalogPathError, CatalogRoots, resolve_catalog_reference
from nodalarc.catalog_upload import (
    DEFAULT_CATALOG_UPLOAD_LIMITS,
    CatalogUpload,
    CatalogUploadLimits,
    UploadId,
    sha256_digest,
    verify_catalog_upload,
)
from nodalarc.configuration_yaml import load_configuration_yaml
from nodalarc.models.resolved_session import SourceContext
from nodalarc.resolve_session import SessionResolution, resolve_session_with_assets
from nodalarc.semantic_projection import resolved_session_semantic_digest

RUNTIME_CONFIG_PROOF_SCHEMA: Final[Literal["nodalarc.runtime-config-proof.v3"]] = (
    "nodalarc.runtime-config-proof.v3"
)
RUNTIME_DEPLOYMENT_CONTEXT_SCHEMA: Final[Literal["nodalarc.runtime-deployment-context.v2"]] = (
    "nodalarc.runtime-deployment-context.v2"
)
RUNTIME_DEPLOYMENT_CONTEXT_FILENAME = "deployment-context.json"
UNBOUND_RUNTIME_IDENTITY = "unbound:predeployment"

_SHA256_PATTERN = r"^sha256:[0-9a-f]{64}$"
Sha256Digest = Annotated[str, StringConstraints(pattern=_SHA256_PATTERN)]


class RuntimeDeploymentContext(BaseModel):
    """Immutable deployment identity mounted with one selected upload."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    schema_name: Literal["nodalarc.runtime-deployment-context.v2"] = (
        RUNTIME_DEPLOYMENT_CONTEXT_SCHEMA
    )
    cr_uid: str = Field(min_length=1)
    cr_generation: int = Field(gt=0)
    session_run_id: str = Field(min_length=1)
    upload_id: UploadId
    document_digest: Sha256Digest
    closure_digest: Sha256Digest
    resolved_semantic_digest: Sha256Digest
    release: str = Field(min_length=1)
    build: str = Field(min_length=1)


class RuntimeConfigProof(BaseModel):
    """Closed evidence that exact uploaded files produced one resolution."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    schema_name: Literal["nodalarc.runtime-config-proof.v3"] = RUNTIME_CONFIG_PROOF_SCHEMA
    source_origin: str = Field(min_length=1)
    run_id: str | None = Field(default=None, min_length=1)
    cr_uid: str = Field(default=UNBOUND_RUNTIME_IDENTITY, min_length=1)
    cr_generation: int = Field(default=0, ge=0)
    pod_uid: str = Field(default=UNBOUND_RUNTIME_IDENTITY, min_length=1)
    upload_id: UploadId
    release: str = Field(default=UNBOUND_RUNTIME_IDENTITY, min_length=1)
    build: str = Field(default=UNBOUND_RUNTIME_IDENTITY, min_length=1)
    document_digest: Sha256Digest
    closure_digest: Sha256Digest
    resolved_semantic_digest: Sha256Digest
    file_count: int = Field(ge=0)
    total_bytes: int = Field(gt=0)
    resolved_node_count: int = Field(ge=0)

    @model_validator(mode="after")
    def _deployment_identity(self) -> RuntimeConfigProof:
        bound_values = (self.cr_uid, self.pod_uid, self.release, self.build)
        is_bound = tuple(value != UNBOUND_RUNTIME_IDENTITY for value in bound_values)
        if any(is_bound) and not all(is_bound):
            raise ValueError("runtime proof deployment identity must be wholly bound or unbound")
        if all(is_bound):
            if self.cr_generation <= 0:
                raise ValueError("bound runtime proof requires a positive CR generation")
            if self.run_id is None:
                raise ValueError("bound runtime proof requires a session run ID")
        elif self.cr_generation != 0:
            raise ValueError("unbound runtime proof must use CR generation zero")
        return self

    @property
    def deployment_identity_bound(self) -> bool:
        return self.cr_uid != UNBOUND_RUNTIME_IDENTITY

    def bind_deployment_identity(
        self,
        context: RuntimeDeploymentContext,
        *,
        pod_uid: str,
    ) -> RuntimeConfigProof:
        """Fence this exact content proof to one CR generation and pod."""
        if self.deployment_identity_bound:
            raise ValueError("runtime configuration proof is already deployment-bound")
        if not isinstance(context, RuntimeDeploymentContext):
            raise TypeError("context must be a RuntimeDeploymentContext")
        if not isinstance(pod_uid, str) or not pod_uid.strip():
            raise ValueError("pod_uid must be a non-empty string")
        expected = {
            "run_id": self.run_id,
            "upload_id": self.upload_id,
            "document_digest": self.document_digest,
            "closure_digest": self.closure_digest,
            "resolved_semantic_digest": self.resolved_semantic_digest,
        }
        observed = {
            "run_id": context.session_run_id,
            "upload_id": context.upload_id,
            "document_digest": context.document_digest,
            "closure_digest": context.closure_digest,
            "resolved_semantic_digest": context.resolved_semantic_digest,
        }
        if observed != expected:
            raise ValueError("deployment context differs from the resolved runtime proof")
        return RuntimeConfigProof.model_validate(
            {
                **self.model_dump(mode="json"),
                "cr_uid": context.cr_uid,
                "cr_generation": context.cr_generation,
                "pod_uid": pod_uid,
                "release": context.release,
                "build": context.build,
            }
        )


@dataclass(frozen=True, slots=True)
class ResolvedRuntimeConfig:
    resolution: SessionResolution
    proof: RuntimeConfigProof
    catalog_roots: CatalogRoots
    session_path: Path


class RuntimeConfigErrorCode(StrEnum):
    INVALID_SOURCE_CONTEXT = "runtime_config.invalid_source_context"
    DESTINATION_REJECTED = "runtime_config.destination_rejected"
    MATERIALIZATION_FAILED = "runtime_config.materialization_failed"
    SHIPPED_ASSET_MISMATCH = "runtime_config.shipped_asset_mismatch"


@dataclass(frozen=True, slots=True)
class RuntimeConfigErrorEvidence:
    code: RuntimeConfigErrorCode
    message: str
    ref: str | None = None
    expected: str | int | None = None
    observed: str | int | None = None
    cause_type: str | None = None


class RuntimeConfigError(ValueError):
    """Typed runtime configuration boundary refusal."""

    def __init__(self, evidence: RuntimeConfigErrorEvidence) -> None:
        super().__init__(evidence.message)
        self.evidence = evidence

    @property
    def code(self) -> RuntimeConfigErrorCode:
        return self.evidence.code


def _error(
    code: RuntimeConfigErrorCode,
    message: str,
    *,
    ref: str | None = None,
    expected: str | int | None = None,
    observed: str | int | None = None,
    cause: BaseException | None = None,
) -> RuntimeConfigError:
    return RuntimeConfigError(
        RuntimeConfigErrorEvidence(
            code=code,
            message=message,
            ref=ref,
            expected=expected,
            observed=observed,
            cause_type=type(cause).__name__ if cause is not None else None,
        )
    )


def _validated_source_context(source_context: SourceContext) -> SourceContext:
    if not isinstance(source_context, SourceContext):
        raise _error(
            RuntimeConfigErrorCode.INVALID_SOURCE_CONTEXT,
            "runtime source_context must be a SourceContext instance",
        )
    if source_context.session_path is not None:
        raise _error(
            RuntimeConfigErrorCode.INVALID_SOURCE_CONTEXT,
            "runtime source_context must use logical origin/run identity, not a filesystem path",
        )
    return source_context


def _destination_path(destination: str | Path) -> Path:
    path = Path(destination)
    if path.name in {"", ".", ".."}:
        raise _error(
            RuntimeConfigErrorCode.DESTINATION_REJECTED,
            "runtime materialization destination must name a new directory",
        )
    try:
        parent = path.parent.resolve(strict=True)
    except OSError as exc:
        raise _error(
            RuntimeConfigErrorCode.DESTINATION_REJECTED,
            "runtime materialization destination parent does not exist",
            cause=exc,
        ) from exc
    if not parent.is_dir():
        raise _error(
            RuntimeConfigErrorCode.DESTINATION_REJECTED,
            "runtime materialization destination parent must be a directory",
        )
    resolved = parent / path.name
    if resolved.exists():
        raise _error(
            RuntimeConfigErrorCode.DESTINATION_REJECTED,
            "runtime materialization destination already exists",
        )
    return resolved


def _contained_target(root: Path, preserved_path: str) -> Path:
    relative = PurePosixPath(preserved_path)
    if (
        relative.is_absolute()
        or ".." in relative.parts
        or len(relative.parts) < 3
        or relative.parts[0] != "catalog"
        or relative.parts[1] not in {"nodalarc", "user"}
    ):
        raise _error(
            RuntimeConfigErrorCode.MATERIALIZATION_FAILED,
            f"runtime catalog preserved path is not contained: {preserved_path!r}",
        )
    target = root.joinpath(*relative.parts)
    try:
        target.resolve(strict=False).relative_to(root.resolve(strict=True))
    except ValueError as exc:
        raise _error(
            RuntimeConfigErrorCode.MATERIALIZATION_FAILED,
            f"runtime catalog preserved path escapes its materialization root: {preserved_path!r}",
            cause=exc,
        ) from exc
    return target


def _write_exact(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    if path.read_bytes() != content:
        raise OSError(f"exact-byte verification failed for {path.name}")


def _write_uploaded_tree(
    stage: Path,
    root_yaml: bytes,
    entries: tuple[CatalogClosureEntry, ...],
) -> CatalogRoots:
    _write_exact(stage / "session.yaml", root_yaml)
    shipped_root = stage / "catalog" / "nodalarc"
    user_root = stage / "catalog" / "user"
    shipped_root.mkdir(parents=True, exist_ok=False)
    user_root.mkdir(parents=True, exist_ok=False)
    for entry in entries:
        _write_exact(_contained_target(stage, entry.preserved_path), entry.yaml_bytes)
    return CatalogRoots.from_catalog_root(shipped_root, user_root=user_root)


def _assert_shipped_assets(upload: CatalogUpload, installed_shipped_root: Path) -> None:
    roots = CatalogRoots.from_catalog_root(installed_shipped_root)
    for entry in upload.catalog_files:
        if entry.ref.namespace != "nodalarc":
            continue
        try:
            installed_path = resolve_catalog_reference(
                entry.ref,
                roots,
                label="uploaded shipped catalog asset",
            )
            installed_bytes = installed_path.read_bytes()
        except (CatalogPathError, OSError) as exc:
            raise _error(
                RuntimeConfigErrorCode.SHIPPED_ASSET_MISMATCH,
                f"Uploaded shipped asset {entry.ref} is not present in the installed catalog",
                ref=str(entry.ref),
                cause=exc,
            ) from exc
        if installed_bytes != entry.yaml_bytes:
            raise _error(
                RuntimeConfigErrorCode.SHIPPED_ASSET_MISMATCH,
                f"Uploaded shipped asset {entry.ref} differs from the installed read-only asset",
                ref=str(entry.ref),
                expected=entry.document_digest,
                observed="different exact bytes",
            )


def _raw_session(root_yaml: bytes) -> dict:
    try:
        raw = load_configuration_yaml(root_yaml)
    except (UnicodeError, yaml.YAMLError) as exc:
        raise _error(
            RuntimeConfigErrorCode.MATERIALIZATION_FAILED,
            f"runtime root session is not valid YAML: {exc}",
            cause=exc,
        ) from exc
    if not isinstance(raw, dict):
        raise _error(
            RuntimeConfigErrorCode.MATERIALIZATION_FAILED,
            "runtime root session must parse to a mapping",
        )
    return raw


def _stage_directory(destination: Path) -> Path:
    try:
        return Path(tempfile.mkdtemp(prefix=".nodalarc-runtime-", dir=destination.parent))
    except OSError as exc:
        raise _error(
            RuntimeConfigErrorCode.MATERIALIZATION_FAILED,
            f"could not create bounded runtime materialization stage: {exc}",
            cause=exc,
        ) from exc


def _activate_stage(stage: Path, destination: Path) -> None:
    try:
        os.replace(stage, destination)
    except OSError as exc:
        raise _error(
            RuntimeConfigErrorCode.MATERIALIZATION_FAILED,
            f"could not activate verified runtime configuration: {exc}",
            cause=exc,
        ) from exc


def _resolve_once(
    root_yaml: bytes,
    roots: CatalogRoots,
    source_context: SourceContext,
) -> SessionResolution:
    return resolve_session_with_assets(
        _raw_session(root_yaml),
        catalog_roots=roots,
        source_context=source_context,
    )


def load_runtime_config(
    upload: CatalogUpload,
    *,
    destination: str | Path,
    installed_shipped_root: str | Path,
    source_context: SourceContext,
    limits: CatalogUploadLimits = DEFAULT_CATALOG_UPLOAD_LIMITS,
) -> ResolvedRuntimeConfig:
    """Verify, materialize, and resolve one exact ordinary-file upload."""
    if not isinstance(upload, CatalogUpload):
        raise TypeError("upload must be a CatalogUpload")
    context = _validated_source_context(source_context)
    target = _destination_path(destination)
    verified = verify_catalog_upload(upload, limits=limits)
    try:
        installed_root = Path(installed_shipped_root).resolve(strict=True)
    except OSError as exc:
        raise _error(
            RuntimeConfigErrorCode.SHIPPED_ASSET_MISMATCH,
            "installed shipped catalog root is not available",
            cause=exc,
        ) from exc
    _assert_shipped_assets(verified, installed_root)

    stage = _stage_directory(target)
    activated = False
    try:
        stage_roots = _write_uploaded_tree(stage, verified.root_yaml, verified.catalog_files)
        resolution = _resolve_once(verified.root_yaml, stage_roots, context)
        proof = RuntimeConfigProof(
            source_origin=str(context.origin),
            run_id=str(context.run_id) if context.run_id is not None else None,
            upload_id=verified.upload_id,
            document_digest=sha256_digest(verified.root_yaml),
            closure_digest=verified.selection.closure_digest,
            resolved_semantic_digest=resolved_session_semantic_digest(resolution.resolved),
            file_count=verified.file_count,
            total_bytes=verified.total_bytes,
            resolved_node_count=len(resolution.resolved.nodes),
        )
        _activate_stage(stage, target)
        activated = True
    except OSError as exc:
        raise _error(
            RuntimeConfigErrorCode.MATERIALIZATION_FAILED,
            f"could not materialize exact runtime configuration files: {exc}",
            cause=exc,
        ) from exc
    finally:
        if not activated:
            shutil.rmtree(stage, ignore_errors=True)

    final_roots = CatalogRoots.from_catalog_root(
        target / "catalog" / "nodalarc",
        user_root=target / "catalog" / "user",
    )
    return ResolvedRuntimeConfig(
        resolution=resolution,
        proof=proof,
        catalog_roots=final_roots,
        session_path=target / "session.yaml",
    )
