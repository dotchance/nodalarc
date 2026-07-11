"""Durable, queryable state for VS-API session transitions.

Transition records are operational evidence, not configuration grammar.  The
filesystem adapter is deliberately single-writer and replaceable; it stores
records on the existing VS-API PVC without making paths or catalog scopes part
of the browser contract.
"""

from __future__ import annotations

import contextlib
import os
import re
import tempfile
import threading
from abc import ABC, abstractmethod
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Any, Final, Literal

from nodalarc.catalog_refs import SessionRef
from nodalarc.catalog_upload import CatalogUploadSelection, sha256_digest
from pydantic import BaseModel, ConfigDict, Field, model_validator

_OPERATION_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{15,127}$")
_SHA256_PATTERN = r"^sha256:[0-9a-f]{64}$"
_KUBERNETES_NAME_PATTERN = r"^[a-z0-9](?:[-a-z0-9.]*[a-z0-9])?$"
_TYPED_FAILURE_CODE_PATTERN = re.compile(r"^[a-z][a-z0-9_.-]{0,127}$")
TRANSITION_OPERATION_SCHEMA: Final[Literal["nodalarc.transition-operation.v2"]] = (
    "nodalarc.transition-operation.v2"
)

Sha256Digest = Annotated[str, Field(pattern=_SHA256_PATTERN)]


class TransitionOperationState(StrEnum):
    """Persisted lifecycle states for one admitted session transition."""

    RESERVED = "reserved"
    COLLECTING = "collecting"
    UPLOADING = "uploading"
    VERIFYING = "verifying"
    SWITCHING = "switching"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"

    @property
    def terminal(self) -> bool:
        return self in {
            TransitionOperationState.SUCCEEDED,
            TransitionOperationState.FAILED,
            TransitionOperationState.CANCELLED,
        }


class TransitionOperationSourceKind(StrEnum):
    """Path-free source classes admitted by VS-API."""

    CATALOG_SESSION = "catalog_session"


class _OperationModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True, allow_inf_nan=False)


class TransitionOperationSource(_OperationModel):
    kind: TransitionOperationSourceKind
    logical_id: str = Field(min_length=1, max_length=512)

    @model_validator(mode="after")
    def _source_identity_matches_kind(self) -> TransitionOperationSource:
        try:
            canonical = str(SessionRef(self.logical_id))
        except (TypeError, ValueError) as exc:
            raise ValueError("catalog transition identity must be a SessionRef") from exc
        if canonical != self.logical_id:
            raise ValueError("catalog transition identity must be canonical")
        return self


class TransitionOperationFacts(_OperationModel):
    """Reviewed, browser-safe facts bound to one operation."""

    document_digest: Sha256Digest | None = None
    closure_digest: Sha256Digest | None = None
    resolved_semantic_digest: Sha256Digest | None = None
    file_count: int | None = Field(default=None, ge=0)
    total_bytes: int | None = Field(default=None, gt=0)
    release: str = Field(min_length=1, max_length=128)
    build: str = Field(min_length=1, max_length=128)


class TransitionOperationEvent(_OperationModel):
    state: TransitionOperationState
    occurred_at: datetime
    detail: str | None = Field(default=None, min_length=1, max_length=512)


class TransitionOperationFailure(_OperationModel):
    code: str = Field(min_length=1, max_length=128)
    message: str = Field(min_length=1, max_length=512)
    cause_type: str | None = Field(default=None, min_length=1, max_length=128)


class TransitionRuntimeResult(_OperationModel):
    session_id: str = Field(min_length=1, max_length=128)
    generation: int = Field(gt=0)


class TransitionRuntimePlan(_OperationModel):
    """Server-selected runtime resource identity, never supplied by the browser."""

    namespace: str = Field(min_length=1, max_length=253, pattern=_KUBERNETES_NAME_PATTERN)
    name: str = Field(min_length=1, max_length=253, pattern=_KUBERNETES_NAME_PATTERN)


class TransitionRuntimeStatusProof(_OperationModel):
    """Operator status proof observed on one selected ConstellationSpec."""

    observed_generation: int | None = Field(default=None, gt=0)
    phase: str | None = Field(default=None, min_length=1, max_length=64)
    session_id: str | None = Field(default=None, min_length=1, max_length=128)
    pod_count: int | None = Field(default=None, ge=0)
    ready_pods: int | None = Field(default=None, ge=0)
    wired_pods: int | None = Field(default=None, ge=0)
    document_digest: Sha256Digest | None = None
    closure_digest: Sha256Digest | None = None
    resolved_semantic_digest: Sha256Digest | None = None
    release: str | None = Field(default=None, min_length=1, max_length=128)
    build: str | None = Field(default=None, min_length=1, max_length=128)


class TransitionConstellationSpecObservation(_OperationModel):
    """Exact selected CR identity and its latest durably observed status proof."""

    namespace: str = Field(min_length=1, max_length=253, pattern=_KUBERNETES_NAME_PATTERN)
    name: str = Field(min_length=1, max_length=253, pattern=_KUBERNETES_NAME_PATTERN)
    generation: int = Field(gt=0)
    status: TransitionRuntimeStatusProof = TransitionRuntimeStatusProof()


class TransitionOperation(_OperationModel):
    """Public, path-free representation returned for an opaque operation ID."""

    operation_id: str = Field(min_length=16, max_length=128)
    state: TransitionOperationState
    source: TransitionOperationSource
    facts: TransitionOperationFacts
    created_at: datetime
    updated_at: datetime
    events: tuple[TransitionOperationEvent, ...] = Field(min_length=1)
    failure: TransitionOperationFailure | None = None
    runtime: TransitionRuntimeResult | None = None

    @model_validator(mode="after")
    def _state_matches_evidence(self) -> TransitionOperation:
        if not _OPERATION_ID_PATTERN.fullmatch(self.operation_id):
            raise ValueError("operation_id contains unsupported characters")
        if self.events[-1].state is not self.state:
            raise ValueError("the final operation event must match the current state")
        if (
            self.state
            in {
                TransitionOperationState.FAILED,
                TransitionOperationState.CANCELLED,
            }
            and self.failure is None
        ):
            raise ValueError("failed and cancelled transitions require failure evidence")
        if not self.state.terminal and (self.failure is not None or self.runtime is not None):
            raise ValueError("nonterminal transitions cannot carry terminal evidence")
        return self


class TransitionOperationProvenance(_OperationModel):
    """Server-only values used to match a live ConstellationSpec after restart."""

    source_revision: Sha256Digest | None = None
    repository_generation: str | None = Field(default=None, min_length=1, max_length=256)
    upload_id: str | None = Field(default=None, min_length=1, max_length=128)
    upload_resource_names: tuple[
        Annotated[str, Field(min_length=1, max_length=253, pattern=_KUBERNETES_NAME_PATTERN)],
        ...,
    ] = ()
    runtime_plan: TransitionRuntimePlan | None = None
    constellation_spec: TransitionConstellationSpecObservation | None = None

    @model_validator(mode="after")
    def _internal_provenance_is_consistent(self) -> TransitionOperationProvenance:
        if len(set(self.upload_resource_names)) != len(self.upload_resource_names):
            raise ValueError("created upload resource names must be unique")
        if self.upload_resource_names and self.upload_id is None:
            raise ValueError("created upload resource names require an upload id")
        if self.constellation_spec is not None:
            if self.runtime_plan is None:
                raise ValueError("observed ConstellationSpec requires a runtime plan")
            if (
                self.constellation_spec.namespace != self.runtime_plan.namespace
                or self.constellation_spec.name != self.runtime_plan.name
            ):
                raise ValueError("observed ConstellationSpec must match the runtime plan")
        return self


class TransitionOperationProvenancePatch(_OperationModel):
    """One atomic internal evidence update for a nonterminal operation."""

    upload_resource_name: str | None = Field(
        default=None,
        min_length=1,
        max_length=253,
        pattern=_KUBERNETES_NAME_PATTERN,
    )
    constellation_spec: TransitionConstellationSpecObservation | None = None

    @model_validator(mode="after")
    def _contains_one_update(self) -> TransitionOperationProvenancePatch:
        if (self.upload_resource_name is None) == (self.constellation_spec is None):
            raise ValueError("a provenance patch must contain exactly one evidence update")
        return self


class StoredTransitionOperation(TransitionOperation):
    """Durable internal record; provenance is never returned by the query API."""

    schema_name: Literal["nodalarc.transition-operation.v2"] = TRANSITION_OPERATION_SCHEMA
    version: int = Field(gt=0)
    provenance: TransitionOperationProvenance

    def public_view(self) -> TransitionOperation:
        return TransitionOperation.model_validate(
            self.model_dump(exclude={"schema_name", "version", "provenance"}),
            strict=True,
        )


class TransitionOperationReservation(_OperationModel):
    """Trusted server input used to create one reserved operation."""

    source: TransitionOperationSource
    facts: TransitionOperationFacts
    provenance: TransitionOperationProvenance = TransitionOperationProvenance()


class TransitionOperationStoreError(RuntimeError):
    """Base failure for durable operation persistence."""


class TransitionOperationNotFoundError(TransitionOperationStoreError):
    """Raised when an opaque operation ID has no record."""


class TransitionOperationConflictError(TransitionOperationStoreError):
    """Raised when another nonterminal transition already owns admission."""

    def __init__(self, active_operation_id: str) -> None:
        super().__init__("a session transition is already active")
        self.active_operation_id = active_operation_id


class TransitionOperationStateError(TransitionOperationStoreError):
    """Raised for an invalid or stale lifecycle transition."""


class TransitionOperationStore(ABC):
    """Replaceable persistence boundary for single-active transition state."""

    @property
    @abstractmethod
    def blocking_io(self) -> bool:
        """Whether callers must execute this adapter outside the event loop."""

    @abstractmethod
    def reserve(
        self,
        operation_id: str,
        reservation: TransitionOperationReservation,
        *,
        now: datetime | None = None,
    ) -> StoredTransitionOperation:
        """Atomically reserve admission or raise ``TransitionOperationConflictError``."""

    @abstractmethod
    def get_operation(self, operation_id: str) -> StoredTransitionOperation:
        """Read one exact operation record."""

    @abstractmethod
    def active(self) -> StoredTransitionOperation | None:
        """Return the sole nonterminal record, if one exists."""

    @abstractmethod
    def advance(
        self,
        operation_id: str,
        state: TransitionOperationState,
        *,
        detail: str | None = None,
        failure: TransitionOperationFailure | None = None,
        runtime: TransitionRuntimeResult | None = None,
        now: datetime | None = None,
    ) -> StoredTransitionOperation:
        """Atomically advance one operation and append durable evidence."""

    @abstractmethod
    def update_provenance(
        self,
        operation_id: str,
        patch: TransitionOperationProvenancePatch,
        *,
        now: datetime | None = None,
    ) -> StoredTransitionOperation:
        """Atomically journal one server-only observation without changing state."""


_STATE_ORDER = {
    TransitionOperationState.RESERVED: 0,
    TransitionOperationState.COLLECTING: 1,
    TransitionOperationState.UPLOADING: 2,
    TransitionOperationState.VERIFYING: 3,
    TransitionOperationState.SWITCHING: 4,
}


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _validated_operation_id(operation_id: str) -> str:
    if not isinstance(operation_id, str) or not _OPERATION_ID_PATTERN.fullmatch(operation_id):
        raise ValueError("operation_id must be an opaque 16-128 character token")
    return operation_id


def _new_record(
    operation_id: str,
    reservation: TransitionOperationReservation,
    *,
    now: datetime,
) -> StoredTransitionOperation:
    return StoredTransitionOperation(
        operation_id=_validated_operation_id(operation_id),
        state=TransitionOperationState.RESERVED,
        source=reservation.source,
        facts=reservation.facts,
        created_at=now,
        updated_at=now,
        events=(
            TransitionOperationEvent(
                state=TransitionOperationState.RESERVED,
                occurred_at=now,
                detail="Transition reserved",
            ),
        ),
        version=1,
        provenance=reservation.provenance,
    )


def _advanced_record(
    current: StoredTransitionOperation,
    state: TransitionOperationState,
    *,
    detail: str | None,
    failure: TransitionOperationFailure | None,
    runtime: TransitionRuntimeResult | None,
    now: datetime,
) -> StoredTransitionOperation:
    state = TransitionOperationState(state)
    if current.state.terminal:
        if current.state is state:
            return current
        raise TransitionOperationStateError("terminal transition operations are immutable")
    if not state.terminal and _STATE_ORDER[state] < _STATE_ORDER[current.state]:
        raise TransitionOperationStateError(
            f"operation state cannot regress from {current.state.value} to {state.value}"
        )
    if state is TransitionOperationState.SUCCEEDED:
        if failure is not None:
            raise TransitionOperationStateError("success cannot carry failure evidence")
    elif state in {TransitionOperationState.FAILED, TransitionOperationState.CANCELLED}:
        if failure is None or runtime is not None:
            raise TransitionOperationStateError(
                "failed or cancelled transitions require failure evidence only"
            )
    elif failure is not None or runtime is not None:
        raise TransitionOperationStateError("nonterminal transition cannot carry terminal evidence")

    event = TransitionOperationEvent(state=state, occurred_at=now, detail=detail)
    return StoredTransitionOperation(
        operation_id=current.operation_id,
        state=state,
        source=current.source,
        facts=current.facts,
        created_at=current.created_at,
        updated_at=now,
        events=(*current.events, event),
        failure=failure,
        runtime=runtime,
        version=current.version + 1,
        provenance=current.provenance,
    )


def _patched_provenance(
    current: StoredTransitionOperation,
    patch: TransitionOperationProvenancePatch,
    *,
    now: datetime,
) -> StoredTransitionOperation:
    if current.state.terminal:
        raise TransitionOperationStateError("terminal transition operations are immutable")
    provenance = current.provenance
    if patch.upload_resource_name is not None:
        if provenance.upload_id is None:
            raise TransitionOperationStateError("upload resource evidence requires an upload id")
        if patch.upload_resource_name in provenance.upload_resource_names:
            return current
        provenance = TransitionOperationProvenance.model_validate(
            {
                **provenance.model_dump(mode="python"),
                "upload_resource_names": (
                    *provenance.upload_resource_names,
                    patch.upload_resource_name,
                ),
            },
            strict=True,
        )
    else:
        observation = patch.constellation_spec
        if observation is None:
            raise AssertionError("validated provenance patch is empty")
        existing = provenance.constellation_spec
        if existing is not None and (
            existing.namespace != observation.namespace
            or existing.name != observation.name
            or existing.generation != observation.generation
        ):
            raise TransitionOperationStateError(
                "selected ConstellationSpec identity cannot change after observation"
            )
        if existing == observation:
            return current
        provenance = TransitionOperationProvenance.model_validate(
            {
                **provenance.model_dump(mode="python"),
                "constellation_spec": observation,
            },
            strict=True,
        )
    return StoredTransitionOperation(
        operation_id=current.operation_id,
        state=current.state,
        source=current.source,
        facts=current.facts,
        created_at=current.created_at,
        updated_at=now,
        events=current.events,
        failure=current.failure,
        runtime=current.runtime,
        version=current.version + 1,
        provenance=provenance,
    )


class FilesystemTransitionOperationStore(TransitionOperationStore):
    """Atomic process-local single-writer adapter for the current VS-API PVC.

    The threading lock coordinates callers in this process only. Deployments
    must run one VS-API writer; multi-replica/HA operation requires replacing
    this adapter with transactional shared storage.
    """

    def __init__(self, root: str | Path) -> None:
        self._root = Path(root).absolute()
        self._lock = threading.Lock()
        if self._root.is_symlink():
            raise TransitionOperationStoreError("operation store root must not be a symlink")
        self._root.mkdir(mode=0o700, parents=True, exist_ok=True)
        if not self._root.is_dir():
            raise TransitionOperationStoreError("operation store root is not a directory")
        os.chmod(self._root, 0o700)

    @property
    def blocking_io(self) -> bool:
        return True

    def _path(self, operation_id: str) -> Path:
        return self._root / f"{_validated_operation_id(operation_id)}.json"

    def _read_path(self, path: Path) -> StoredTransitionOperation:
        if path.is_symlink():
            raise TransitionOperationStoreError("operation record must not be a symlink")
        try:
            return StoredTransitionOperation.model_validate_json(path.read_bytes(), strict=True)
        except (OSError, ValueError) as exc:
            raise TransitionOperationStoreError(
                f"could not read transition operation record {path.name}"
            ) from exc

    def _read_active(self) -> StoredTransitionOperation | None:
        active: list[StoredTransitionOperation] = []
        for path in sorted(self._root.glob("*.json")):
            record = self._read_path(path)
            if not record.state.terminal:
                active.append(record)
        if len(active) > 1:
            raise TransitionOperationStoreError(
                "operation store contains multiple nonterminal transition records"
            )
        return active[0] if active else None

    def _write(self, record: StoredTransitionOperation) -> None:
        target = self._path(record.operation_id)
        payload = record.model_dump_json(indent=2).encode("utf-8") + b"\n"
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{record.operation_id}.",
            suffix=".tmp",
            dir=self._root,
        )
        try:
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "wb", closefd=True) as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary_name, target)
            if target.read_bytes() != payload:
                raise TransitionOperationStoreError(
                    "transition operation exact-byte verification failed"
                )
            directory_descriptor = os.open(self._root, os.O_RDONLY)
            try:
                os.fsync(directory_descriptor)
            finally:
                os.close(directory_descriptor)
        except Exception as exc:
            with contextlib.suppress(OSError):
                os.close(descriptor)
            with contextlib.suppress(OSError):
                Path(temporary_name).unlink()
            if isinstance(exc, TransitionOperationStoreError):
                raise
            raise TransitionOperationStoreError(
                "could not persist transition operation record"
            ) from exc

    def reserve(
        self,
        operation_id: str,
        reservation: TransitionOperationReservation,
        *,
        now: datetime | None = None,
    ) -> StoredTransitionOperation:
        with self._lock:
            active = self._read_active()
            if active is not None:
                raise TransitionOperationConflictError(active.operation_id)
            target = self._path(operation_id)
            if target.exists() or target.is_symlink():
                raise TransitionOperationConflictError(operation_id)
            record = _new_record(operation_id, reservation, now=now or _utc_now())
            self._write(record)
            return record

    def get_operation(self, operation_id: str) -> StoredTransitionOperation:
        with self._lock:
            path = self._path(operation_id)
            if not path.is_file():
                raise TransitionOperationNotFoundError("transition operation was not found")
            return self._read_path(path)

    def active(self) -> StoredTransitionOperation | None:
        with self._lock:
            return self._read_active()

    def advance(
        self,
        operation_id: str,
        state: TransitionOperationState,
        *,
        detail: str | None = None,
        failure: TransitionOperationFailure | None = None,
        runtime: TransitionRuntimeResult | None = None,
        now: datetime | None = None,
    ) -> StoredTransitionOperation:
        with self._lock:
            path = self._path(operation_id)
            if not path.is_file():
                raise TransitionOperationNotFoundError("transition operation was not found")
            current = self._read_path(path)
            updated = _advanced_record(
                current,
                state,
                detail=detail,
                failure=failure,
                runtime=runtime,
                now=now or _utc_now(),
            )
            if updated is not current:
                self._write(updated)
            return updated

    def update_provenance(
        self,
        operation_id: str,
        patch: TransitionOperationProvenancePatch,
        *,
        now: datetime | None = None,
    ) -> StoredTransitionOperation:
        with self._lock:
            path = self._path(operation_id)
            if not path.is_file():
                raise TransitionOperationNotFoundError("transition operation was not found")
            current = self._read_path(path)
            updated = _patched_provenance(current, patch, now=now or _utc_now())
            if updated is not current:
                self._write(updated)
            return updated


class InMemoryTransitionOperationStore(TransitionOperationStore):
    """Deterministic adapter for unit tests and embedded callers."""

    def __init__(self) -> None:
        self._records: dict[str, StoredTransitionOperation] = {}
        self._lock = threading.Lock()

    @property
    def blocking_io(self) -> bool:
        return False

    def reserve(
        self,
        operation_id: str,
        reservation: TransitionOperationReservation,
        *,
        now: datetime | None = None,
    ) -> StoredTransitionOperation:
        with self._lock:
            active = next(
                (item for item in self._records.values() if not item.state.terminal), None
            )
            if active is not None:
                raise TransitionOperationConflictError(active.operation_id)
            if operation_id in self._records:
                raise TransitionOperationConflictError(operation_id)
            record = _new_record(operation_id, reservation, now=now or _utc_now())
            self._records[operation_id] = record
            return record

    def get_operation(self, operation_id: str) -> StoredTransitionOperation:
        with self._lock:
            try:
                return self._records[_validated_operation_id(operation_id)]
            except KeyError as exc:
                raise TransitionOperationNotFoundError(
                    "transition operation was not found"
                ) from exc

    def active(self) -> StoredTransitionOperation | None:
        with self._lock:
            return next((item for item in self._records.values() if not item.state.terminal), None)

    def advance(
        self,
        operation_id: str,
        state: TransitionOperationState,
        *,
        detail: str | None = None,
        failure: TransitionOperationFailure | None = None,
        runtime: TransitionRuntimeResult | None = None,
        now: datetime | None = None,
    ) -> StoredTransitionOperation:
        with self._lock:
            try:
                current = self._records[_validated_operation_id(operation_id)]
            except KeyError as exc:
                raise TransitionOperationNotFoundError(
                    "transition operation was not found"
                ) from exc
            updated = _advanced_record(
                current,
                TransitionOperationState(state),
                detail=detail,
                failure=failure,
                runtime=runtime,
                now=now or _utc_now(),
            )
            self._records[operation_id] = updated
            return updated

    def update_provenance(
        self,
        operation_id: str,
        patch: TransitionOperationProvenancePatch,
        *,
        now: datetime | None = None,
    ) -> StoredTransitionOperation:
        with self._lock:
            try:
                current = self._records[_validated_operation_id(operation_id)]
            except KeyError as exc:
                raise TransitionOperationNotFoundError(
                    "transition operation was not found"
                ) from exc
            updated = _patched_provenance(current, patch, now=now or _utc_now())
            self._records[operation_id] = updated
            return updated


class TransitionReconciliationDisposition(StrEnum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    STILL_SWITCHING = "still_switching"


class TransitionReconciliation(_OperationModel):
    disposition: TransitionReconciliationDisposition
    detail: str = Field(min_length=1, max_length=512)
    runtime: TransitionRuntimeResult | None = None
    failure: TransitionOperationFailure | None = None


def transition_failure_from_exception(exc: BaseException) -> TransitionOperationFailure:
    """Preserve trusted typed refusal codes without exposing internal resources."""

    evidence = getattr(exc, "evidence", None)
    raw_code = getattr(evidence, "code", None)
    code = getattr(raw_code, "value", raw_code)
    if isinstance(code, str) and _TYPED_FAILURE_CODE_PATTERN.fullmatch(code):
        if code.startswith("catalog_upload_store."):
            message = "Catalog upload could not be persisted or verified"
        elif code.startswith("catalog_upload."):
            message = "Catalog upload does not satisfy deployment transport requirements"
        elif code.startswith(("prepared_session.", "session_deployment.")):
            raw_message = getattr(evidence, "message", None)
            message = (
                raw_message.strip()[:512]
                if isinstance(raw_message, str) and raw_message.strip()
                else "Session deployment preflight failed"
            )
        else:
            message = "Session transition failed"
        cause_type = getattr(evidence, "cause_type", None)
        return TransitionOperationFailure(
            code=code,
            message=message,
            cause_type=(
                cause_type[:128]
                if isinstance(cause_type, str) and cause_type.strip()
                else type(exc).__name__[:128]
            ),
        )
    if isinstance(exc, TimeoutError):
        return TransitionOperationFailure(
            code="transition.runtime.timeout",
            message="Session transition timed out",
            cause_type=type(exc).__name__[:128],
        )
    return TransitionOperationFailure(
        code="transition.worker.failed",
        message="Session transition failed",
        cause_type=type(exc).__name__[:128],
    )


def _positive_int(value: Any) -> int | None:
    try:
        result = int(value)
    except TypeError, ValueError:
        return None
    return result if result > 0 else None


def reconcile_transition_operation(
    operation: StoredTransitionOperation,
    constellation_spec: dict[str, Any] | None,
) -> TransitionReconciliation:
    """Reconcile one interrupted record using only trusted live CR evidence."""

    if operation.state.terminal:
        raise TransitionOperationStateError("terminal operations do not require reconciliation")
    if constellation_spec is None:
        return TransitionReconciliation(
            disposition=TransitionReconciliationDisposition.CANCELLED,
            detail="No live ConstellationSpec matches the interrupted transition",
            failure=TransitionOperationFailure(
                code="transition.recovery.runtime_absent",
                message="Transition was interrupted before runtime selection completed",
            ),
        )

    metadata = constellation_spec.get("metadata") or {}
    annotations = metadata.get("annotations") or {}
    spec = constellation_spec.get("spec") or {}
    status = constellation_spec.get("status") or {}
    session_yaml = spec.get("sessionYaml")
    observed_document_digest = (
        sha256_digest(session_yaml.encode("utf-8"))
        if isinstance(session_yaml, str) and session_yaml
        else None
    )
    try:
        upload = CatalogUploadSelection.model_validate(spec.get("catalogUpload"), strict=True)
    except TypeError, ValueError:
        upload = None
    expected_annotations = {
        "nodalarc.io/source-kind": "catalog_session",
        "nodalarc.io/source-id": operation.source.logical_id,
        "nodalarc.io/source-revision": operation.provenance.source_revision,
        "nodalarc.io/document-digest": operation.facts.document_digest,
        "nodalarc.io/closure-digest": operation.facts.closure_digest,
    }
    expected_generation = operation.provenance.repository_generation
    generation_matches = (
        annotations.get("nodalarc.io/catalog-generation") == expected_generation
        if expected_generation is not None
        else "nodalarc.io/catalog-generation" not in annotations
    )
    runtime_plan = operation.provenance.runtime_plan
    observed_runtime = operation.provenance.constellation_spec
    runtime_identity_matches = True
    if runtime_plan is not None:
        runtime_identity_matches = (
            metadata.get("namespace") == runtime_plan.namespace
            and metadata.get("name") == runtime_plan.name
        )
    if observed_runtime is not None:
        runtime_identity_matches = runtime_identity_matches and (
            _positive_int(metadata.get("generation")) == observed_runtime.generation
        )
    if (
        any(
            expected is None or annotations.get(key) != expected
            for key, expected in expected_annotations.items()
        )
        or not generation_matches
        or not runtime_identity_matches
        or observed_document_digest != operation.facts.document_digest
        or upload is None
        or upload.upload_id != operation.provenance.upload_id
        or upload.closure_digest != operation.facts.closure_digest
        or (
            operation.facts.file_count is not None
            and upload.file_count + 1 != operation.facts.file_count
        )
    ):
        return TransitionReconciliation(
            disposition=TransitionReconciliationDisposition.CANCELLED,
            detail="Live ConstellationSpec belongs to a different transition source",
            failure=TransitionOperationFailure(
                code="transition.recovery.runtime_mismatch",
                message="Interrupted transition is not selected by the live runtime",
            ),
        )

    generation = _positive_int(metadata.get("generation"))
    observed_generation = _positive_int(status.get("observedGeneration"))
    phase = str(status.get("phase") or "")
    if generation is None or observed_generation != generation:
        return TransitionReconciliation(
            disposition=TransitionReconciliationDisposition.STILL_SWITCHING,
            detail="Waiting for the Operator to observe the selected runtime",
        )
    if phase in {"Pending", "Creating", "Wiring", ""}:
        return TransitionReconciliation(
            disposition=TransitionReconciliationDisposition.STILL_SWITCHING,
            detail=f"Selected runtime remains {phase or 'pending'}",
        )
    if phase == "Error":
        return TransitionReconciliation(
            disposition=TransitionReconciliationDisposition.FAILED,
            detail="Operator reported an error for the selected runtime",
            failure=TransitionOperationFailure(
                code="transition.runtime.error",
                message="Session transition failed",
            ),
        )
    if phase != "Ready":
        return TransitionReconciliation(
            disposition=TransitionReconciliationDisposition.STILL_SWITCHING,
            detail=f"Selected runtime is in phase {phase}",
        )

    pod_count = _positive_int(status.get("podCount"))
    ready_pods = _positive_int(status.get("readyPods"))
    wired_pods = _positive_int(status.get("wiredPods"))
    if pod_count is None or ready_pods != pod_count or wired_pods != pod_count:
        return TransitionReconciliation(
            disposition=TransitionReconciliationDisposition.STILL_SWITCHING,
            detail="Waiting for complete Ready pod and wiring proof",
        )

    expected_status = {
        "documentDigest": operation.facts.document_digest,
        "closureDigest": operation.facts.closure_digest,
        "resolvedSemanticDigest": operation.facts.resolved_semantic_digest,
        "runtimeRelease": operation.facts.release,
        "runtimeBuild": operation.facts.build,
    }
    if any(status.get(key) != expected for key, expected in expected_status.items()):
        return TransitionReconciliation(
            disposition=TransitionReconciliationDisposition.FAILED,
            detail="Ready runtime proof does not match the admitted transition",
            failure=TransitionOperationFailure(
                code="transition.runtime.proof_mismatch",
                message="Runtime proof does not match the admitted transition",
            ),
        )
    session_id = str(status.get("sessionRunId") or "")
    if not session_id:
        return TransitionReconciliation(
            disposition=TransitionReconciliationDisposition.FAILED,
            detail="Ready runtime is missing its session identity",
            failure=TransitionOperationFailure(
                code="transition.runtime.identity_missing",
                message="Runtime proof is incomplete",
            ),
        )
    return TransitionReconciliation(
        disposition=TransitionReconciliationDisposition.SUCCEEDED,
        detail="Runtime reached Ready with matching provenance and proof",
        runtime=TransitionRuntimeResult(session_id=session_id, generation=generation),
    )
