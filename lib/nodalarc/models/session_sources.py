"""Typed application identities for deployable catalog sessions."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from nodalarc.catalog_refs import SessionRef
from nodalarc.models.builder_api import Sha256Digest


class _SessionSourceModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)


class CatalogSessionSourceId(_SessionSourceModel):
    """First-class catalog session selected inside a server-owned scope."""

    kind: Literal["catalog"] = "catalog"
    session_ref: SessionRef


SessionSourceId = CatalogSessionSourceId


class CatalogSessionBlocker(_SessionSourceModel):
    """Safe typed reason one catalog session cannot deploy."""

    code: str = Field(min_length=1, max_length=160)
    message: str = Field(min_length=1, max_length=1024)
    cause_type: str | None = Field(default=None, min_length=1, max_length=160)


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
    blockers: tuple[CatalogSessionBlocker, ...] = ()
    active: bool = False


class CatalogSessionSwitchRequest(_SessionSourceModel):
    """Deploy one reviewed session revision from the request catalog scope."""

    source: CatalogSessionSourceId
    expected_source_revision: Sha256Digest
    expected_document_digest: Sha256Digest
    expected_dependency_digest: Sha256Digest


class CatalogSessionSwitchAccepted(_SessionSourceModel):
    """Accepted catalog deployment operation."""

    status: Literal["accepted"] = "accepted"
    operation_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{15,127}$")
    source: CatalogSessionSourceId


class CatalogSessionYamlUploadRequest(_SessionSourceModel):
    """One standard persisted session document to save into the user catalog."""

    yaml: str = Field(min_length=1)
