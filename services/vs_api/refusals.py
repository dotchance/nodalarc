"""The one translation from a refusing exception to a client-visible response.

Every exception family whose message is public evidence by producer contract is
listed here once, with its HTTP status. Route handlers never build refusal
bodies from exception text themselves: they raise, or return
``refusal_response`` with a fixed message, and the handlers registered by
``install_refusal_handlers`` produce the ``ApiRefusal`` envelope.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from nodalarc.catalog_closure import (
    CatalogClosureError,
    CatalogClosureErrorCode,
    CatalogDocumentNotFound,
    CatalogReadError,
    CatalogReadFailed,
    CatalogReadRejected,
)
from nodalarc.catalog_refs import CatalogReferenceError
from nodalarc.catalog_repository import (
    CatalogConflictError,
    CatalogContainmentError,
    CatalogNotFoundError,
    CatalogReadOnlyError,
    CatalogRepositoryError,
    CatalogTransactionOrderError,
    CatalogTransactionStateError,
    CatalogValidationError,
    UnknownCatalogScopeError,
)
from nodalarc.models.api_refusal import ApiRefusal
from nodalarc.prepared_session import PreparedSessionError, PreparedSessionErrorCode
from nodalarc.resolve_session import SessionResolutionError
from nodalarc.runtime_support import UnsupportedFeatureError
from nodalarc.workload_target import WorkloadTargetError
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from vs_api.continuous_tracer import UntraceableNodeError
from vs_api.introspect import IntrospectExecError
from vs_api.session_context import SessionInactiveError
from vs_api.session_deployment import (
    SessionDeploymentPreparationError,
    SessionDeploymentPreparationErrorCode,
)

log = logging.getLogger(__name__)

INTERNAL_ERROR_CODE = "vs_api.internal_error"
INTERNAL_ERROR_MESSAGE = "Request failed"

SESSION_DEPLOYMENT_STATUS: dict[SessionDeploymentPreparationErrorCode, int] = {
    SessionDeploymentPreparationErrorCode.INVALID_PRECONDITION: 422,
    SessionDeploymentPreparationErrorCode.SOURCE_NOT_FOUND: 404,
    SessionDeploymentPreparationErrorCode.STALE_REPOSITORY: 409,
    SessionDeploymentPreparationErrorCode.STALE_SOURCE: 409,
}
PREPARED_SESSION_STATUS: dict[PreparedSessionErrorCode, int] = {
    PreparedSessionErrorCode.INVALID_DIGEST: 422,
    PreparedSessionErrorCode.STALE_SOURCE_REVISION: 409,
    PreparedSessionErrorCode.STALE_DOCUMENT_DIGEST: 409,
    PreparedSessionErrorCode.STALE_CLOSURE_DIGEST: 409,
    PreparedSessionErrorCode.MATERIALIZATION_FAILED: 500,
    PreparedSessionErrorCode.NOT_READY: 422,
}
CATALOG_CLOSURE_STATUS: dict[CatalogClosureErrorCode, int] = {
    code: (503 if code is CatalogClosureErrorCode.READ_FAILED else 422)
    for code in CatalogClosureErrorCode
}
CATALOG_REPOSITORY_UNAVAILABLE_MESSAGE = "Catalog storage is unavailable"


@dataclass(frozen=True, slots=True)
class RefusalOutcome:
    status_code: int
    refusal: ApiRefusal

    def response(self) -> JSONResponse:
        return JSONResponse(
            status_code=self.status_code,
            content=self.refusal.model_dump(mode="json", exclude_none=True),
        )


def _outcome(status_code: int, code: str, message: str, cause_type: str | None = None):
    return RefusalOutcome(
        status_code=status_code,
        refusal=ApiRefusal(code=code, message=message, cause_type=cause_type),
    )


def _catalog_repository_outcome(exc: CatalogRepositoryError) -> RefusalOutcome:
    if isinstance(exc, CatalogNotFoundError):
        return _outcome(404, "catalog_repository.not_found", str(exc))
    if isinstance(exc, CatalogConflictError):
        return _outcome(409, "catalog_repository.conflict", str(exc))
    if isinstance(exc, CatalogReadOnlyError):
        return _outcome(403, "catalog_repository.read_only", str(exc))
    if isinstance(exc, CatalogValidationError):
        return _outcome(422, "catalog_repository.invalid_document", str(exc))
    if isinstance(exc, CatalogContainmentError):
        return _outcome(400, "catalog_repository.containment", str(exc))
    if isinstance(exc, UnknownCatalogScopeError):
        return _outcome(400, "catalog_repository.unknown_scope", str(exc))
    if isinstance(exc, CatalogTransactionStateError | CatalogTransactionOrderError):
        return _outcome(409, "catalog_repository.transaction", str(exc))
    # A storage failure: the message is fixed because the base class is raised
    # by every adapter for every kind of I/O failure.
    return _outcome(
        503,
        "catalog_repository.unavailable",
        CATALOG_REPOSITORY_UNAVAILABLE_MESSAGE,
        type(exc).__name__,
    )


def _catalog_read_outcome(exc: CatalogReadError) -> RefusalOutcome:
    if isinstance(exc, CatalogDocumentNotFound):
        return _outcome(404, "catalog_read.not_found", str(exc))
    if isinstance(exc, CatalogReadRejected):
        return _outcome(400, "catalog_read.rejected", str(exc))
    if isinstance(exc, CatalogReadFailed):
        return _outcome(503, "catalog_read.failed", str(exc))
    raise TypeError(f"unmapped catalog read error {type(exc).__name__}")


def refusal_from_exception(exc: BaseException) -> RefusalOutcome | None:
    """Return the status and envelope for a refusing exception, else ``None``.

    ``None`` means the exception is not a refusal: it is a failure the caller
    reports as an internal error, never as the exception's own text.
    """
    if isinstance(exc, SessionDeploymentPreparationError):
        evidence = exc.evidence
        return _outcome(
            SESSION_DEPLOYMENT_STATUS[exc.code],
            exc.code.value,
            evidence.message,
            evidence.cause_type,
        )
    if isinstance(exc, PreparedSessionError):
        evidence = exc.evidence
        return _outcome(
            PREPARED_SESSION_STATUS[exc.code],
            exc.code.value,
            evidence.message,
            evidence.cause_type,
        )
    if isinstance(exc, CatalogClosureError):
        evidence = exc.evidence
        return _outcome(
            CATALOG_CLOSURE_STATUS[exc.code],
            exc.code.value,
            evidence.message,
            evidence.cause_type,
        )
    if isinstance(exc, UnsupportedFeatureError):
        return _outcome(422, "runtime_support.unsupported", str(exc))
    if isinstance(exc, SessionResolutionError):
        return _outcome(422, "session_resolution.invalid", str(exc))
    if isinstance(exc, CatalogReferenceError):
        return _outcome(400, exc.code.value, str(exc))
    if isinstance(exc, CatalogReadError):
        return _catalog_read_outcome(exc)
    if isinstance(exc, CatalogRepositoryError):
        return _catalog_repository_outcome(exc)
    if isinstance(exc, WorkloadTargetError):
        return _outcome(503, "workload_target.unavailable", str(exc))
    if isinstance(exc, IntrospectExecError):
        return _outcome(502, "introspect.exec_failed", str(exc))
    if isinstance(exc, SessionInactiveError):
        return _outcome(503, "session.inactive", str(exc))
    if isinstance(exc, UntraceableNodeError):
        return _outcome(400, "trace.untraceable_node", str(exc))
    return None


def internal_error_refusal(message: str = INTERNAL_ERROR_MESSAGE) -> ApiRefusal:
    """The envelope for a failure that is not a refusal; it names no cause."""
    return ApiRefusal(code=INTERNAL_ERROR_CODE, message=message)


def refusal_response(status_code: int, code: str, message: str) -> JSONResponse:
    """A refusal a route states itself, with a message it fixed in source."""
    return RefusalOutcome(status_code, ApiRefusal(code=code, message=message)).response()


REFUSAL_FAMILIES: tuple[type[BaseException], ...] = (
    SessionDeploymentPreparationError,
    PreparedSessionError,
    CatalogClosureError,
    UnsupportedFeatureError,
    SessionResolutionError,
    CatalogReferenceError,
    CatalogReadError,
    CatalogRepositoryError,
    WorkloadTargetError,
    IntrospectExecError,
    SessionInactiveError,
    UntraceableNodeError,
)


class UnhandledFailureMiddleware:
    """Answer a failure no handler translated with the internal-error envelope.

    It sits inside the CORS and security-header middlewares, so the envelope
    carries the same headers as every other response. Starlette's outer error
    middleware would answer outside them. A failure after the response started
    cannot be answered and propagates.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        started = False

        async def send_tracking(message: Message) -> None:
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
            await send(message)

        try:
            await self.app(scope, receive, send_tracking)
        except Exception as exc:
            if started:
                raise
            log.error(
                "Unhandled failure on %s %s", scope.get("method"), scope.get("path"), exc_info=exc
            )
            response = JSONResponse(
                status_code=500,
                content=internal_error_refusal().model_dump(mode="json", exclude_none=True),
            )
            await response(scope, receive, send)


def install_refusal_handlers(app: FastAPI) -> None:
    """Register the translation once for every refusal family and for the rest.

    Call it before the header-owning middlewares are added: Starlette wraps
    later additions around earlier ones, and the unhandled-failure answer must
    pass through them.
    """

    async def refusal_handler(_request: Request, exc: Exception) -> JSONResponse:
        outcome = refusal_from_exception(exc)
        if outcome is None:
            raise TypeError(f"{type(exc).__name__} is registered but not translated")
        return outcome.response()

    handler: Callable = refusal_handler
    for family in REFUSAL_FAMILIES:
        app.add_exception_handler(family, handler)
    app.add_middleware(UnhandledFailureMiddleware)
