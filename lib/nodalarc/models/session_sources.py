# Copyright 2024-2026 .chance (dotchance)
# SPDX-License-Identifier: AGPL-3.0-only
"""Typed application identities for deployable catalog sessions."""

from __future__ import annotations

import re
from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from nodalarc.catalog_refs import SessionRef
from nodalarc.models.api_refusal import ApiRefusal
from nodalarc.models.builder_api import Sha256Digest


class _SessionSourceModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)


class CatalogSessionSourceId(_SessionSourceModel):
    """First-class catalog session selected inside a server-owned scope."""

    kind: Literal["catalog"] = "catalog"
    session_ref: SessionRef


SessionSourceId = CatalogSessionSourceId


class CatalogSessionSummary(_SessionSourceModel):
    """Revisioned browser listing for one scoped catalog session."""

    source_id: CatalogSessionSourceId
    name: str = Field(min_length=1)
    source: Literal["nodalarc", "user"]
    constellation: str
    routing_stack: str
    deploy_allowed: bool
    source_revision: Sha256Digest | None = None
    document_digest: Sha256Digest | None = None
    dependency_digest: Sha256Digest | None = None
    blockers: tuple[ApiRefusal, ...] = ()
    active: bool = False


class CatalogSessionSwitchRequest(_SessionSourceModel):
    """Deploy one reviewed session revision from the request catalog scope."""

    source: CatalogSessionSourceId
    expected_source_revision: Sha256Digest
    expected_document_digest: Sha256Digest
    expected_dependency_digest: Sha256Digest
    # Keep a history database for this session run.
    record_history: bool


class CatalogSessionSwitchAccepted(_SessionSourceModel):
    """Accepted catalog deployment operation."""

    status: Literal["accepted"] = "accepted"
    operation_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{15,127}$")
    source: CatalogSessionSourceId


class CatalogSessionYamlQuery(_SessionSourceModel):
    """The scoped catalog session whose stored YAML to download."""

    session_ref: str


class CatalogSessionYamlUploadRequest(_SessionSourceModel):
    """One standard persisted session document to save into the user catalog."""

    yaml: str = Field(min_length=1)
    # Keep a history database for the session run this upload deploys.
    record_history: bool


# The public view of one admitted session transition: what the transition routes answer.
# VS-API's store keeps server-only provenance beside it.

OPERATION_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{15,127}$")


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


class TransitionRecordModel(BaseModel):
    """The configuration every transition record model shares, public or server-only."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True, allow_inf_nan=False)


class TransitionOperationSource(TransitionRecordModel):
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


class TransitionOperationFacts(TransitionRecordModel):
    """Reviewed, browser-safe facts bound to one operation."""

    document_digest: Sha256Digest | None = None
    closure_digest: Sha256Digest | None = None
    resolved_semantic_digest: Sha256Digest | None = None
    file_count: int | None = Field(default=None, ge=0)
    total_bytes: int | None = Field(default=None, gt=0)
    release: str = Field(min_length=1, max_length=128)
    build: str = Field(min_length=1, max_length=128)


class TransitionOperationEvent(TransitionRecordModel):
    state: TransitionOperationState
    occurred_at: datetime
    detail: str | None = Field(default=None, min_length=1, max_length=512)


class TransitionOperationFailure(TransitionRecordModel):
    code: str = Field(min_length=1, max_length=128)
    message: str = Field(min_length=1, max_length=512)
    cause_type: str | None = Field(default=None, min_length=1, max_length=128)


class TransitionRuntimeResult(TransitionRecordModel):
    session_id: str = Field(min_length=1, max_length=128)
    generation: int = Field(gt=0)


class TransitionOperation(TransitionRecordModel):
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
        if not OPERATION_ID_PATTERN.fullmatch(self.operation_id):
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
