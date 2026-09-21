"""Scoped catalog-session listing and exact YAML retrieval."""

from __future__ import annotations

import logging

from nodalarc.catalog_closure import CatalogClosureCollector
from nodalarc.catalog_refs import SessionRef
from nodalarc.catalog_repository import CatalogNotFoundError
from nodalarc.models.api_refusal import ApiRefusal
from nodalarc.models.session_sources import (
    CatalogSessionSourceId,
    CatalogSessionSummary,
)
from nodalarc.prepared_session import (
    PreparedSessionSource,
    prepare_collected_session,
)

from .catalog_context import CatalogContext
from .refusals import internal_error_refusal, refusal_from_exception
from .resolved_runtime_views import constellation_label, routing_label

log = logging.getLogger(__name__)


def _blocker(session_ref: SessionRef, exc: Exception) -> ApiRefusal:
    """The listing row's reason: the typed refusal, or an internal error.

    A failure that is not a refusal is reported as one, never as a contract
    violation the session did not commit.
    """
    outcome = refusal_from_exception(exc)
    if outcome is not None:
        return outcome.refusal
    log.error("Catalog session %s could not be prepared for listing", session_ref, exc_info=exc)
    return internal_error_refusal("Session preparation failed")


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
                # One collection per entry: its digests survive whatever
                # preparation then refuses, so a blocked row still shows them.
                closure = CatalogClosureCollector.collect(document.content, snapshot)
                document_digest = closure.document_digest
                closure_digest = closure.closure_digest
                prepared = prepare_collected_session(
                    closure,
                    source=PreparedSessionSource(
                        logical_id=session_ref,
                        origin="vs-api.catalog-session-list",
                    ),
                    source_revision=str(document.revision),
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
                    blockers=(_blocker(session_ref, exc),),
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
