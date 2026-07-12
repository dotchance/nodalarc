"""Application contract for scoped, versioned catalog persistence."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import NewType, Self

from nodalarc.catalog_closure import CatalogReadDocument
from nodalarc.catalog_refs import CatalogFamily, CatalogNamespace, CatalogRef

CatalogRevision = NewType("CatalogRevision", str)
CatalogGeneration = NewType("CatalogGeneration", str)


class CatalogScope:
    """Opaque server-selected capability identifying one user catalog."""

    __slots__ = ()

    def __repr__(self) -> str:
        return "<CatalogScope opaque>"


@dataclass(frozen=True, slots=True)
class CatalogEntry:
    """Immutable metadata for one document in a read snapshot."""

    ref: CatalogRef
    namespace: CatalogNamespace
    family: CatalogFamily
    revision: CatalogRevision
    size_bytes: int


@dataclass(frozen=True, slots=True)
class CatalogDocument:
    """One exact-byte catalog document and its content revision."""

    ref: CatalogRef
    namespace: CatalogNamespace
    family: CatalogFamily
    content: bytes
    revision: CatalogRevision


class CatalogRepositoryError(RuntimeError):
    """Base error for repository contract failures."""


class UnknownCatalogScopeError(CatalogRepositoryError):
    """Raised when a scope was not injected into the repository."""


class CatalogNotFoundError(CatalogRepositoryError):
    """Raised when an exact catalog reference does not exist."""


class CatalogConflictError(CatalogRepositoryError):
    """Raised when compare-and-swap or generation preconditions are stale."""


class CatalogReadOnlyError(CatalogRepositoryError):
    """Raised when a mutation targets the shipped catalog."""


class CatalogValidationError(CatalogRepositoryError):
    """Raised when a reference or persisted document is invalid."""


class CatalogContainmentError(CatalogRepositoryError):
    """Raised when a filesystem path is unsafe or escapes an injected root."""


class CatalogTransactionStateError(CatalogRepositoryError):
    """Raised when a unit of work is empty, closed, or ambiguously mutated."""


class CatalogTransactionOrderError(CatalogRepositoryError):
    """Raised when a session publication is not the final logical mutation."""


class CatalogReadSnapshot(ABC):
    """Immutable read view pinned to one user-catalog generation."""

    @property
    @abstractmethod
    def scope(self) -> CatalogScope:
        """Return the opaque scope capability used to open this snapshot."""

    @property
    @abstractmethod
    def generation(self) -> CatalogGeneration:
        """Return the exact pinned user-catalog generation."""

    @abstractmethod
    def read_bytes(self, ref: str | CatalogRef) -> bytes:
        """Read the exact stored bytes for closure collection or parsing."""

    @abstractmethod
    def read(self, ref: CatalogRef) -> CatalogReadDocument:
        """Return the typed exact-byte view required by closure collection."""

    @abstractmethod
    def get(self, ref: str | CatalogRef) -> CatalogDocument:
        """Return one validated exact-byte document."""

    @abstractmethod
    def list(
        self,
        *,
        namespace: CatalogNamespace | None = None,
        family: CatalogFamily | None = None,
    ) -> tuple[CatalogEntry, ...]:
        """List document metadata in deterministic reference order."""

    @abstractmethod
    def export_documents(
        self,
        *,
        namespace: CatalogNamespace | None = None,
        family: CatalogFamily | None = None,
    ) -> tuple[CatalogDocument, ...]:
        """Return a deterministic exact-byte export of this snapshot."""


class CatalogUnitOfWork(ABC):
    """One compare-and-swap mutation set based on a pinned generation."""

    @property
    @abstractmethod
    def base_generation(self) -> CatalogGeneration:
        """Return the generation against which this transaction will commit."""

    @abstractmethod
    def write_bytes(
        self,
        ref: str | CatalogRef,
        content: bytes,
        *,
        expected_revision: CatalogRevision | str | None,
    ) -> None:
        """Create when expected_revision is None, otherwise replace by CAS."""

    @abstractmethod
    def delete(
        self,
        ref: str | CatalogRef,
        *,
        expected_revision: CatalogRevision | str,
    ) -> None:
        """Delete one user document by exact content revision."""

    @abstractmethod
    def commit(self) -> CatalogReadSnapshot:
        """Validate and atomically publish the complete mutation set."""

    @abstractmethod
    def abort(self) -> None:
        """Close without publishing any mutation."""

    def __enter__(self) -> Self:
        return self

    def __exit__(self, _exc_type: object, _exc: object, _traceback: object) -> None:
        self.abort()


class CatalogRepository(ABC):
    """Repository boundary for server-selected catalog scopes.

    Implementations may promise only the writer coordination they actually
    provide. In particular, the filesystem implementation serializes writers
    inside one process and does not claim cross-process or HA coordination.
    """

    @abstractmethod
    def snapshot(self, scope: CatalogScope) -> CatalogReadSnapshot:
        """Open an immutable read snapshot for an injected scope."""

    @abstractmethod
    def begin(
        self,
        scope: CatalogScope,
        *,
        base_generation: CatalogGeneration | None = None,
    ) -> CatalogUnitOfWork:
        """Begin a unit of work pinned to an explicit or current generation."""
