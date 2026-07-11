"""Scoped catalog-session listing and exact YAML retrieval."""

from __future__ import annotations

from nodalarc.catalog_closure import CatalogClosureCollector
from nodalarc.catalog_refs import SessionRef
from nodalarc.catalog_repository import CatalogNotFoundError
from nodalarc.models.session_sources import (
    CatalogSessionBlocker,
    CatalogSessionSourceId,
    CatalogSessionSummary,
)
from nodalarc.prepared_session import (
    PreparedSessionSource,
    prepare_session_files,
)
from nodalarc.runtime_support import UnsupportedFeatureError

from .catalog_context import CatalogContext
from .resolved_runtime_views import constellation_label, routing_label


def _safe_blocker(exc: Exception) -> CatalogSessionBlocker:
    if isinstance(exc, UnsupportedFeatureError):
        return CatalogSessionBlocker(
            code="catalog_session.unsupported",
            message=str(exc)[:1024],
            cause_type=type(exc).__name__[:160],
        )
    raw_code = getattr(getattr(exc, "code", None), "value", None)
    code = raw_code if isinstance(raw_code, str) else "catalog_session.invalid"
    if code.startswith(("prepared_session.", "session_deployment.")):
        raw_message = getattr(getattr(exc, "evidence", None), "message", None)
        message = raw_message if isinstance(raw_message, str) else "Session preflight failed"
    else:
        message = "The session does not satisfy the published configuration contract"
    return CatalogSessionBlocker(
        code=code[:160],
        message=message[:1024],
        cause_type=type(exc).__name__[:160],
    )


class CatalogSessionService:
    """Read deployable sessions from one server-selected catalog scope."""

    def __init__(self, context: CatalogContext) -> None:
        if not isinstance(context, CatalogContext):
            raise TypeError("context must be a CatalogContext")
        self._context = context

    def list_sessions(
        self,
        *,
        active_session_ref: str | None,
        available_node_count: int,
    ) -> tuple[CatalogSessionSummary, ...]:
        snapshot = self._context.repository.snapshot(self._context.scope)
        summaries: list[CatalogSessionSummary] = []
        for entry in snapshot.list(family="sessions"):
            session_ref = SessionRef(str(entry.ref))
            document = snapshot.get(session_ref)
            source = CatalogSessionSourceId(session_ref=session_ref)
            document_digest: str | None = None
            closure_digest: str | None = None
            try:
                closure = CatalogClosureCollector.collect(document.content, snapshot)
                document_digest = closure.document_digest
                closure_digest = closure.closure_digest
                prepared = prepare_session_files(
                    document.content,
                    snapshot,
                    source=PreparedSessionSource(
                        logical_id=session_ref,
                        origin="vs-api.catalog-session-list",
                    ),
                    source_revision=str(document.revision),
                    expected_source_revision=str(document.revision),
                    expected_document_digest=document_digest,
                    expected_closure_digest=closure_digest,
                    available_node_count=available_node_count,
                )
                resolved = prepared.resolution.resolved
                summary = CatalogSessionSummary(
                    source_id=source,
                    name=resolved.session.name,
                    source=entry.namespace,
                    constellation=constellation_label(resolved),
                    routing_stack=routing_label(resolved),
                    deploy_allowed=True,
                    source_revision=str(document.revision),
                    document_digest=document_digest,
                    dependency_digest=closure_digest,
                    active=str(session_ref) == active_session_ref,
                )
            except Exception as exc:
                summary = CatalogSessionSummary(
                    source_id=source,
                    name=session_ref.relative_path.stem,
                    source=entry.namespace,
                    constellation="",
                    routing_stack="",
                    deploy_allowed=False,
                    source_revision=str(document.revision),
                    document_digest=document_digest,
                    dependency_digest=closure_digest,
                    blockers=(_safe_blocker(exc),),
                    active=str(session_ref) == active_session_ref,
                )
            summaries.append(summary)
        return tuple(summaries)

    def read_session_yaml(self, session_ref: str) -> bytes:
        parsed_ref = SessionRef(session_ref)
        snapshot = self._context.repository.snapshot(self._context.scope)
        try:
            document = snapshot.get(parsed_ref)
        except CatalogNotFoundError:
            raise
        if document.family != "sessions":
            raise CatalogNotFoundError(f"catalog document is not a session: {parsed_ref}")
        return document.content
