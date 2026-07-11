"""VS-API ownership boundary for the currently selected catalog scope."""

from __future__ import annotations

import hashlib
import threading
from dataclasses import dataclass
from pathlib import Path

from nodalarc.catalog_repository import CatalogRepository, CatalogScope
from nodalarc.filesystem_catalog_repository import FilesystemCatalogRepository
from nodalarc.platform_config import get_platform_config

_CATALOG_REPOSITORY_DIRECTORY = "catalog-repository"
_LOCAL_SCOPE_DIRECTORY = "local-default"


def _opaque_scope_binding(scope_root: Path) -> str:
    """Derive a stable, non-semantic identifier for the server-selected scope."""

    material = f"nodalarc.filesystem-scope.v1\0{scope_root.absolute()}".encode()
    return "scope_" + hashlib.sha256(material).hexdigest()[:32]


def local_catalog_scope_binding(session_data_root: str | Path | None = None) -> str:
    """Return the stable binding for the current local server-selected scope."""

    configured_session_root = (
        Path(get_platform_config().session_data_root)
        if session_data_root is None
        else Path(session_data_root)
    )
    return _opaque_scope_binding(
        configured_session_root / _CATALOG_REPOSITORY_DIRECTORY / _LOCAL_SCOPE_DIRECTORY
    )


@dataclass(frozen=True, slots=True)
class CatalogContext:
    """Server-selected repository and opaque scope for one request context."""

    repository: CatalogRepository
    scope: CatalogScope
    scope_binding: str = "scope_00000000000000000000000000000000"


def create_catalog_context(
    *,
    session_data_root: str | Path | None = None,
    shipped_root: str | Path | None = None,
    scope: CatalogScope | None = None,
) -> CatalogContext:
    """Create the local single-writer catalog context.

    The filesystem adapter is intentionally a single-process writer. This
    factory does not imply cross-process coordination or multi-tenant identity;
    future storage and authentication layers can select a different repository
    and opaque scope without changing Builder application contracts.
    """

    configured_session_root = (
        Path(get_platform_config().session_data_root)
        if session_data_root is None
        else Path(session_data_root)
    )
    configured_shipped_root = (
        Path("catalog/nodalarc") if shipped_root is None else Path(shipped_root)
    )
    selected_scope = CatalogScope() if scope is None else scope
    scope_root = configured_session_root / _CATALOG_REPOSITORY_DIRECTORY / _LOCAL_SCOPE_DIRECTORY
    repository = FilesystemCatalogRepository(
        shipped_root=configured_shipped_root,
        scope_roots={selected_scope: scope_root},
    )
    return CatalogContext(
        repository=repository,
        scope=selected_scope,
        scope_binding=local_catalog_scope_binding(configured_session_root),
    )


_catalog_context: CatalogContext | None = None
_catalog_context_lock = threading.Lock()


def get_catalog_context() -> CatalogContext:
    """Return the lazily created process-local catalog context."""

    global _catalog_context
    if _catalog_context is not None:
        return _catalog_context
    with _catalog_context_lock:
        if _catalog_context is None:
            _catalog_context = create_catalog_context()
        return _catalog_context


def override_catalog_context_for_testing(context: CatalogContext) -> None:
    """Replace the process-local context for an isolated test."""

    if not isinstance(context, CatalogContext):
        raise TypeError("catalog context override must be a CatalogContext")
    global _catalog_context
    with _catalog_context_lock:
        _catalog_context = context


def reset_catalog_context_for_testing() -> None:
    """Clear the process-local context for an isolated test."""

    global _catalog_context
    with _catalog_context_lock:
        _catalog_context = None
