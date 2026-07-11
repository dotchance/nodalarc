"""Typed FastAPI transport for backend-authoritative Builder authoring."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Annotated, Any, Protocol

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from nodalarc.catalog_closure import CatalogClosureError
from nodalarc.catalog_repository import (
    CatalogNotFoundError,
    CatalogReadSnapshot,
    CatalogRepositoryError,
)
from nodalarc.models.builder_api import (
    BuilderCatalogDocument,
    BuilderCompileRequest,
    BuilderCompileResult,
    BuilderSessionDeployAccepted,
    BuilderSessionDeployRefusal,
    BuilderSessionDeployRequest,
    BuilderSessionSaveRefusal,
    BuilderSessionSaveRequest,
    BuilderSessionSaveResult,
    WizardCompileRefusal,
    WizardCompileRequest,
)
from nodalarc.models.builder_catalog_api import (
    BuilderCatalogBootstrap,
    CatalogClosureImportRequest,
    CatalogComponentDraftEnvelope,
    CatalogDeleteRequest,
    CatalogDeleteResult,
    CatalogDependencyImpact,
    CatalogDependentsRequest,
    CatalogDocumentWriteRequest,
    CatalogDraftAddNodeEthernetPortRequest,
    CatalogDraftAddNodeTerminalMountRequest,
    CatalogDraftAddSiteNodeRequest,
    CatalogDraftCompileRequest,
    CatalogDraftCompileResult,
    CatalogDraftNewRequest,
    CatalogDraftOpenRequest,
    CatalogDraftPatchRequest,
    CatalogDraftReplaceObjectRequest,
    CatalogDraftSaveRequest,
    CatalogDraftSaveResult,
    CatalogForkRequest,
    CatalogForkResult,
    CatalogGetRequest,
    CatalogImportResult,
    CatalogListPage,
    CatalogListRequest,
    CatalogMutationResult,
    CatalogOperationRefusal,
    CatalogSessionExport,
    CatalogSessionExportRequest,
)
from nodalarc.models.builder_visual_api import (
    BuilderVisualCustomizeChainRequest,
    BuilderVisualCustomizeChainResult,
    BuilderVisualDraftAssemblyResult,
    BuilderVisualDraftCommandRequest,
    BuilderVisualDraftCommandResult,
    BuilderVisualDraftCompileRequest,
    BuilderVisualDraftCreateRequest,
    BuilderVisualDraftEnvelope,
    BuilderVisualDraftOpenRequest,
    BuilderVisualWalkerLayoutRequest,
    BuilderVisualWalkerLayoutResult,
    derive_walker_layout,
)
from starlette.concurrency import run_in_threadpool

from .builder_catalog_draft import BuilderCatalogDraftService
from .builder_catalog_service import BuilderCatalogAuthoringService, CatalogAuthoringError
from .builder_compiler import PreviewFactory, compile_builder_draft
from .builder_session_service import BuilderSessionSaveError, save_builder_session
from .builder_visual_draft import (
    BuilderVisualDraftCommandError,
    BuilderVisualDraftConflictError,
    BuilderVisualDraftService,
)
from .catalog_context import CatalogContext, get_catalog_context
from .wizard_builder import build_wizard_compile_request

BUILDER_API_PREFIX = "/api/v1/builder"


class CatalogService(Protocol):
    """Operations the transport requires from the catalog application service."""

    def bootstrap(self) -> BuilderCatalogBootstrap: ...

    def list_catalog(self, request: CatalogListRequest) -> CatalogListPage: ...

    def get_catalog(self, request: CatalogGetRequest) -> BuilderCatalogDocument: ...

    def dependents(self, request: CatalogDependentsRequest) -> CatalogDependencyImpact: ...

    def save_component(self, request: CatalogDocumentWriteRequest) -> CatalogMutationResult: ...

    def fork_component(self, request: CatalogForkRequest) -> CatalogForkResult: ...

    def delete_catalog(self, request: CatalogDeleteRequest) -> CatalogDeleteResult: ...

    def export_session(self, request: CatalogSessionExportRequest) -> CatalogSessionExport: ...

    def import_closure(self, request: CatalogClosureImportRequest) -> CatalogImportResult: ...


class VisualDraftService(Protocol):
    """Backend-owned visual draft operations required by the transport."""

    def create(self, request: BuilderVisualDraftCreateRequest) -> BuilderVisualDraftEnvelope: ...

    def open(self, request: BuilderVisualDraftOpenRequest) -> BuilderVisualDraftEnvelope: ...

    def customize_chain(
        self,
        request: BuilderVisualCustomizeChainRequest,
    ) -> BuilderVisualCustomizeChainResult: ...

    def apply_command(
        self,
        request: BuilderVisualDraftCommandRequest,
        *,
        available_node_count: int,
        preview_factory: PreviewFactory | None = None,
    ) -> BuilderVisualDraftCommandResult: ...

    def compile(
        self,
        request: BuilderVisualDraftCompileRequest,
        *,
        available_node_count: int,
        preview_factory: PreviewFactory | None = None,
    ) -> BuilderVisualDraftAssemblyResult: ...


class CatalogDraftService(Protocol):
    """Lossless component-draft operations required by the transport."""

    def new(self, request: CatalogDraftNewRequest) -> CatalogComponentDraftEnvelope: ...

    def open(self, request: CatalogDraftOpenRequest) -> CatalogComponentDraftEnvelope: ...

    def patch(self, request: CatalogDraftPatchRequest) -> CatalogComponentDraftEnvelope: ...

    def add_site_node(
        self,
        request: CatalogDraftAddSiteNodeRequest,
    ) -> CatalogComponentDraftEnvelope: ...

    def add_node_terminal_mount(
        self,
        request: CatalogDraftAddNodeTerminalMountRequest,
    ) -> CatalogComponentDraftEnvelope: ...

    def add_node_ethernet_port(
        self,
        request: CatalogDraftAddNodeEthernetPortRequest,
    ) -> CatalogComponentDraftEnvelope: ...

    def replace_object(
        self,
        request: CatalogDraftReplaceObjectRequest,
    ) -> CatalogComponentDraftEnvelope: ...

    def compile(self, request: CatalogDraftCompileRequest) -> CatalogDraftCompileResult: ...

    def save(self, request: CatalogDraftSaveRequest) -> CatalogDraftSaveResult: ...


CatalogServiceFactory = Callable[[CatalogContext], CatalogService]
VisualDraftServiceFactory = Callable[[CatalogContext], VisualDraftService]
CatalogDraftServiceFactory = Callable[[CatalogContext], CatalogDraftService]
CatalogOperation = Callable[[CatalogService], Any]
BuilderCompiler = Callable[..., BuilderCompileResult]
WizardRequestBuilder = Callable[..., BuilderCompileRequest]
BuilderSessionService = Callable[..., BuilderSessionSaveResult]
BuilderDeployCallback = Callable[
    [BuilderSessionDeployRequest, CatalogContext],
    Awaitable[BuilderSessionDeployAccepted],
]
CatalogContextProvider = Callable[..., CatalogContext | Awaitable[CatalogContext]]
AvailableNodeCountProvider = Callable[..., int | Awaitable[int]]


def _default_available_node_count() -> int:
    return 1


@dataclass(frozen=True, slots=True)
class BuilderRouterServices:
    """Injectable backend application services; no browser-selected authority."""

    context_provider: CatalogContextProvider = get_catalog_context
    available_node_count_provider: AvailableNodeCountProvider = _default_available_node_count
    compiler: BuilderCompiler = compile_builder_draft
    wizard_request_builder: WizardRequestBuilder = build_wizard_compile_request
    session_service: BuilderSessionService = save_builder_session
    catalog_service_factory: CatalogServiceFactory = BuilderCatalogAuthoringService
    visual_draft_service_factory: VisualDraftServiceFactory = BuilderVisualDraftService
    catalog_draft_service_factory: CatalogDraftServiceFactory = BuilderCatalogDraftService
    preview_factory: PreviewFactory | None = None
    deploy_callback: BuilderDeployCallback | None = None


_CATALOG_REFUSAL_STATUS = {
    "catalog_authoring.not_found": 404,
    "catalog_authoring.read_only": 403,
    "catalog_authoring.invalid_document": 422,
    "catalog_authoring.invalid_patch": 422,
    "catalog_authoring.invalid_graph": 422,
    "catalog_authoring.conflict": 409,
    "catalog_authoring.stale_revision": 409,
    "catalog_authoring.invalid_page_token": 400,
    "catalog_authoring.stale_page_token": 409,
    "catalog_authoring.impact_mismatch": 409,
    "catalog_authoring.dependents_exist": 409,
    "catalog_authoring.import_limit": 413,
    "catalog_authoring.import_digest_mismatch": 422,
    "catalog_authoring.import_incomplete": 422,
    "catalog_authoring.import_collision": 409,
    "catalog_authoring.persistence_failed": 503,
}
_SAVE_REFUSAL_STATUS = {
    "builder_session_save.save_blocked": 422,
    "builder_session_save.stale_write": 409,
    "builder_session_save.graph_invalid": 422,
    "builder_session_save.persistence_failed": 503,
    "builder_session_save.storage_verification_failed": 500,
}
_DEPLOY_REFUSAL_STATUS = {
    "builder_session_deploy.invalid_precondition": 422,
    "builder_session_deploy.source_not_found": 404,
    "builder_session_deploy.stale_source": 409,
    "builder_session_deploy.not_ready": 422,
    "builder_session_deploy.conflict": 409,
    "builder_session_deploy.repository_unavailable": 503,
    "builder_session_deploy.unsupported": 422,
    "builder_session_deploy.preparation_failed": 500,
}
_CATALOG_ERROR_RESPONSES = {
    status: {"model": CatalogOperationRefusal}
    for status in sorted(set(_CATALOG_REFUSAL_STATUS.values()))
}
_SAVE_ERROR_RESPONSES = {
    status: {"model": BuilderSessionSaveRefusal}
    for status in sorted(set(_SAVE_REFUSAL_STATUS.values()))
}
_DEPLOY_ERROR_RESPONSES = {
    status: {"model": BuilderSessionDeployRefusal}
    for status in sorted(set(_DEPLOY_REFUSAL_STATUS.values()))
}


class BuilderSessionDeployError(ValueError):
    """Transport-neutral typed refusal raised by the deployment application seam."""

    def __init__(self, refusal: BuilderSessionDeployRefusal) -> None:
        super().__init__(refusal.message)
        self.refusal = refusal


def _catalog_refusal_response(error: CatalogAuthoringError) -> JSONResponse:
    status = _CATALOG_REFUSAL_STATUS[error.refusal.code]
    return JSONResponse(
        status_code=status,
        content=error.refusal.model_dump(mode="json", exclude_none=True),
    )


def _catalog_unavailable_response(error: BaseException) -> JSONResponse:
    refusal = CatalogOperationRefusal(
        code="catalog_authoring.persistence_failed",
        message="Catalog storage is unavailable",
        cause_type=type(error).__name__,
    )
    return JSONResponse(
        status_code=503,
        content=refusal.model_dump(mode="json", exclude_none=True),
    )


def _save_refusal_response(error: BuilderSessionSaveError) -> JSONResponse:
    evidence = error.evidence
    refusal = BuilderSessionSaveRefusal(
        code=evidence.code.value,
        message=evidence.message,
        target_ref=evidence.target_ref,
        base_generation=evidence.base_generation,
        repository_committed=evidence.repository_committed,
        issues=evidence.issues,
        cause_type=evidence.cause_type,
        compile_result=error.compile_result,
    )
    return JSONResponse(
        status_code=_SAVE_REFUSAL_STATUS[refusal.code],
        content=refusal.model_dump(mode="json", exclude_none=True),
    )


def create_builder_router(
    services: BuilderRouterServices | None = None,
) -> APIRouter:
    """Create an auth-neutral router over server-selected Builder services.

    The caller owns authentication and may apply a guard with
    ``app.include_router(router, dependencies=[Depends(...)])``. Supplying the
    async deployment callback registers the deployment endpoint over the same
    server-selected context. No request model exposes a scope, filesystem path,
    or upload handle.
    """

    selected = services or BuilderRouterServices()
    router = APIRouter(prefix=BUILDER_API_PREFIX, tags=["builder"])
    Context = Annotated[CatalogContext, Depends(selected.context_provider)]
    NodeCount = Annotated[int, Depends(selected.available_node_count_provider)]

    async def invoke_catalog(
        context: CatalogContext,
        operation: CatalogOperation,
    ) -> Any | JSONResponse:
        def invoke() -> Any:
            service = selected.catalog_service_factory(context)
            return operation(service)

        try:
            return await run_in_threadpool(invoke)
        except CatalogAuthoringError as error:
            return _catalog_refusal_response(error)

    async def invoke_catalog_draft(
        context: CatalogContext,
        operation: Callable[[CatalogDraftService], Any],
    ) -> Any | JSONResponse:
        def invoke() -> Any:
            service = selected.catalog_draft_service_factory(context)
            return operation(service)

        try:
            return await run_in_threadpool(invoke)
        except CatalogAuthoringError as error:
            return _catalog_refusal_response(error)

    @router.get("/bootstrap", response_model=BuilderCatalogBootstrap)
    async def bootstrap(context: Context):
        return await invoke_catalog(context, lambda service: service.bootstrap())

    @router.post(
        "/defaults/walker-layout",
        response_model=BuilderVisualWalkerLayoutResult,
    )
    async def walker_layout(
        request: BuilderVisualWalkerLayoutRequest,
    ) -> BuilderVisualWalkerLayoutResult:
        return derive_walker_layout(request)

    @router.post(
        "/draft/new",
        response_model=BuilderVisualDraftEnvelope,
        responses={
            409: {"model": CatalogOperationRefusal},
            503: {"model": CatalogOperationRefusal},
        },
    )
    async def new_visual_draft(request: BuilderVisualDraftCreateRequest, context: Context):
        try:
            return await run_in_threadpool(
                selected.visual_draft_service_factory(context).create,
                request,
            )
        except BuilderVisualDraftConflictError as error:
            refusal = CatalogOperationRefusal(
                code="catalog_authoring.conflict",
                message=str(error),
                ref=error.ref,
                cause_type=type(error).__name__,
            )
            return JSONResponse(
                status_code=409,
                content=refusal.model_dump(mode="json", exclude_none=True),
            )
        except (CatalogRepositoryError, OSError) as error:
            return _catalog_unavailable_response(error)

    @router.post(
        "/draft/open",
        response_model=BuilderVisualDraftEnvelope,
        responses={
            404: {"model": CatalogOperationRefusal},
            409: {"model": CatalogOperationRefusal},
            503: {"model": CatalogOperationRefusal},
        },
    )
    async def open_visual_draft(request: BuilderVisualDraftOpenRequest, context: Context):
        try:
            return await run_in_threadpool(
                selected.visual_draft_service_factory(context).open,
                request,
            )
        except CatalogNotFoundError as error:
            refusal = CatalogOperationRefusal(
                code="catalog_authoring.not_found",
                message=f"Catalog document {request.source_ref} was not found",
                ref=request.source_ref,
                cause_type=type(error).__name__,
            )
            return JSONResponse(
                status_code=404,
                content=refusal.model_dump(mode="json", exclude_none=True),
            )
        except BuilderVisualDraftConflictError as error:
            refusal = CatalogOperationRefusal(
                code="catalog_authoring.conflict",
                message=str(error),
                ref=error.ref,
                cause_type=type(error).__name__,
            )
            return JSONResponse(
                status_code=409,
                content=refusal.model_dump(mode="json", exclude_none=True),
            )
        except (CatalogRepositoryError, OSError, UnicodeError) as error:
            return _catalog_unavailable_response(error)

    @router.post(
        "/draft/compile",
        response_model=BuilderVisualDraftAssemblyResult,
        responses={503: {"model": CatalogOperationRefusal}},
    )
    async def compile_visual_draft(
        request: BuilderVisualDraftCompileRequest,
        context: Context,
        available_node_count: NodeCount,
    ):
        def compile_request() -> BuilderVisualDraftAssemblyResult:
            return selected.visual_draft_service_factory(context).compile(
                request,
                available_node_count=available_node_count,
                preview_factory=selected.preview_factory,
            )

        try:
            return await run_in_threadpool(compile_request)
        except (CatalogRepositoryError, OSError) as error:
            return _catalog_unavailable_response(error)

    @router.post(
        "/draft/command",
        response_model=BuilderVisualDraftCommandResult,
        responses=_CATALOG_ERROR_RESPONSES,
    )
    async def apply_visual_draft_command(
        request: BuilderVisualDraftCommandRequest,
        context: Context,
        available_node_count: NodeCount,
    ):
        def apply_command() -> BuilderVisualDraftCommandResult:
            return selected.visual_draft_service_factory(context).apply_command(
                request,
                available_node_count=available_node_count,
                preview_factory=selected.preview_factory,
            )

        try:
            return await run_in_threadpool(apply_command)
        except BuilderVisualDraftCommandError as error:
            refusal = CatalogOperationRefusal(
                code=error.code,
                message=str(error),
                ref=str(error.ref),
                expected_revision=(
                    str(error.expected_revision) if error.expected_revision is not None else None
                ),
                current_revision=(
                    str(error.current_revision) if error.current_revision is not None else None
                ),
                cause_type=type(error).__name__,
            )
            return _catalog_refusal_response(CatalogAuthoringError(refusal))
        except (CatalogRepositoryError, OSError) as error:
            return _catalog_unavailable_response(error)

    @router.post(
        "/draft/customize-chain",
        response_model=BuilderVisualCustomizeChainResult,
        responses={503: {"model": CatalogOperationRefusal}},
    )
    async def customize_visual_draft_chain(
        request: BuilderVisualCustomizeChainRequest,
        context: Context,
    ):
        try:
            return await run_in_threadpool(
                selected.visual_draft_service_factory(context).customize_chain,
                request,
            )
        except (CatalogRepositoryError, OSError) as error:
            return _catalog_unavailable_response(error)

    @router.post(
        "/compile",
        response_model=BuilderCompileResult,
        responses={503: {"model": CatalogOperationRefusal}},
    )
    async def compile_draft(
        request: BuilderCompileRequest,
        context: Context,
        available_node_count: NodeCount,
    ):
        def compile_request() -> BuilderCompileResult:
            snapshot: CatalogReadSnapshot = context.repository.snapshot(context.scope)
            return selected.compiler(
                request,
                snapshot,
                available_node_count=available_node_count,
                preview_factory=selected.preview_factory,
            )

        try:
            return await run_in_threadpool(compile_request)
        except (CatalogRepositoryError, OSError) as error:
            return _catalog_unavailable_response(error)

    @router.post(
        "/wizard/compile",
        response_model=BuilderCompileResult,
        responses={
            422: {"model": WizardCompileRefusal},
            503: {"model": WizardCompileRefusal},
        },
    )
    async def compile_wizard(
        request: WizardCompileRequest,
        context: Context,
        available_node_count: NodeCount,
    ):
        def compile_request() -> BuilderCompileResult:
            snapshot: CatalogReadSnapshot = context.repository.snapshot(context.scope)
            builder_request = selected.wizard_request_builder(request, snapshot)
            return selected.compiler(
                builder_request,
                snapshot,
                available_node_count=available_node_count,
                preview_factory=selected.preview_factory,
            )

        try:
            return await run_in_threadpool(compile_request)
        except CatalogClosureError as error:
            refusal = WizardCompileRefusal(
                code="wizard_compile.reference_error",
                message=error.evidence.message,
                cause_type=type(error).__name__,
            )
            return JSONResponse(
                status_code=422,
                content=refusal.model_dump(mode="json", exclude_none=True),
            )
        except (TypeError, ValueError) as error:
            refusal = WizardCompileRefusal(
                code="wizard_compile.invalid_selection",
                message=str(error),
                cause_type=type(error).__name__,
            )
            return JSONResponse(
                status_code=422,
                content=refusal.model_dump(mode="json", exclude_none=True),
            )
        except (CatalogRepositoryError, OSError) as error:
            refusal = WizardCompileRefusal(
                code="wizard_compile.repository_unavailable",
                message="Catalog storage is unavailable",
                cause_type=type(error).__name__,
            )
            return JSONResponse(
                status_code=503,
                content=refusal.model_dump(mode="json", exclude_none=True),
            )

    @router.post(
        "/session/save",
        response_model=BuilderSessionSaveResult,
        responses=_SAVE_ERROR_RESPONSES,
    )
    async def save_session(
        request: BuilderSessionSaveRequest,
        context: Context,
        available_node_count: NodeCount,
    ):
        try:
            return await run_in_threadpool(
                selected.session_service,
                request,
                context,
                available_node_count=available_node_count,
                preview_factory=selected.preview_factory,
            )
        except BuilderSessionSaveError as error:
            return _save_refusal_response(error)

    if selected.deploy_callback is not None:

        @router.post(
            "/session/deploy",
            response_model=BuilderSessionDeployAccepted,
            status_code=202,
            responses=_DEPLOY_ERROR_RESPONSES,
        )
        async def deploy_session(
            request: BuilderSessionDeployRequest,
            context: Context,
        ):
            assert selected.deploy_callback is not None
            try:
                return await selected.deploy_callback(request, context)
            except BuilderSessionDeployError as error:
                return JSONResponse(
                    status_code=_DEPLOY_REFUSAL_STATUS[error.refusal.code],
                    content=error.refusal.model_dump(mode="json", exclude_none=True),
                )

    @router.post(
        "/catalog/list",
        response_model=CatalogListPage,
        responses=_CATALOG_ERROR_RESPONSES,
    )
    async def list_catalog(request: CatalogListRequest, context: Context):
        return await invoke_catalog(context, lambda service: service.list_catalog(request))

    @router.post(
        "/catalog/get",
        response_model=BuilderCatalogDocument,
        responses=_CATALOG_ERROR_RESPONSES,
    )
    async def get_catalog(request: CatalogGetRequest, context: Context):
        return await invoke_catalog(context, lambda service: service.get_catalog(request))

    @router.post(
        "/catalog/dependents",
        response_model=CatalogDependencyImpact,
        responses=_CATALOG_ERROR_RESPONSES,
    )
    async def dependents(request: CatalogDependentsRequest, context: Context):
        return await invoke_catalog(context, lambda service: service.dependents(request))

    @router.post(
        "/catalog/write",
        response_model=CatalogMutationResult,
        responses=_CATALOG_ERROR_RESPONSES,
    )
    async def write_catalog(request: CatalogDocumentWriteRequest, context: Context):
        return await invoke_catalog(context, lambda service: service.save_component(request))

    @router.post(
        "/catalog/draft/new",
        response_model=CatalogComponentDraftEnvelope,
        responses=_CATALOG_ERROR_RESPONSES,
    )
    async def new_catalog_draft(request: CatalogDraftNewRequest, context: Context):
        return await invoke_catalog_draft(context, lambda service: service.new(request))

    @router.post(
        "/catalog/draft/open",
        response_model=CatalogComponentDraftEnvelope,
        responses=_CATALOG_ERROR_RESPONSES,
    )
    async def open_catalog_draft(request: CatalogDraftOpenRequest, context: Context):
        return await invoke_catalog_draft(context, lambda service: service.open(request))

    @router.post(
        "/catalog/draft/patch",
        response_model=CatalogComponentDraftEnvelope,
        responses=_CATALOG_ERROR_RESPONSES,
    )
    async def patch_catalog_draft(request: CatalogDraftPatchRequest, context: Context):
        return await invoke_catalog_draft(context, lambda service: service.patch(request))

    @router.post(
        "/catalog/draft/site-node/add",
        response_model=CatalogComponentDraftEnvelope,
        responses=_CATALOG_ERROR_RESPONSES,
    )
    async def add_catalog_draft_site_node(
        request: CatalogDraftAddSiteNodeRequest,
        context: Context,
    ):
        return await invoke_catalog_draft(
            context,
            lambda service: service.add_site_node(request),
        )

    @router.post(
        "/catalog/draft/node-terminal/add",
        response_model=CatalogComponentDraftEnvelope,
        responses=_CATALOG_ERROR_RESPONSES,
    )
    async def add_catalog_draft_node_terminal(
        request: CatalogDraftAddNodeTerminalMountRequest,
        context: Context,
    ):
        return await invoke_catalog_draft(
            context,
            lambda service: service.add_node_terminal_mount(request),
        )

    @router.post(
        "/catalog/draft/node-ethernet/add",
        response_model=CatalogComponentDraftEnvelope,
        responses=_CATALOG_ERROR_RESPONSES,
    )
    async def add_catalog_draft_node_ethernet(
        request: CatalogDraftAddNodeEthernetPortRequest,
        context: Context,
    ):
        return await invoke_catalog_draft(
            context,
            lambda service: service.add_node_ethernet_port(request),
        )

    @router.post(
        "/catalog/draft/replace-object",
        response_model=CatalogComponentDraftEnvelope,
        responses=_CATALOG_ERROR_RESPONSES,
    )
    async def replace_catalog_draft_object(
        request: CatalogDraftReplaceObjectRequest,
        context: Context,
    ):
        return await invoke_catalog_draft(
            context,
            lambda service: service.replace_object(request),
        )

    @router.post(
        "/catalog/draft/compile",
        response_model=CatalogDraftCompileResult,
        responses=_CATALOG_ERROR_RESPONSES,
    )
    async def compile_catalog_draft(request: CatalogDraftCompileRequest, context: Context):
        return await invoke_catalog_draft(context, lambda service: service.compile(request))

    @router.post(
        "/catalog/draft/save",
        response_model=CatalogDraftSaveResult,
        responses=_CATALOG_ERROR_RESPONSES,
    )
    async def save_catalog_draft(request: CatalogDraftSaveRequest, context: Context):
        return await invoke_catalog_draft(context, lambda service: service.save(request))

    @router.post(
        "/catalog/fork",
        response_model=CatalogForkResult,
        responses=_CATALOG_ERROR_RESPONSES,
    )
    async def fork_catalog(request: CatalogForkRequest, context: Context):
        return await invoke_catalog(context, lambda service: service.fork_component(request))

    @router.post(
        "/catalog/delete",
        response_model=CatalogDeleteResult,
        responses=_CATALOG_ERROR_RESPONSES,
    )
    async def delete_catalog(request: CatalogDeleteRequest, context: Context):
        return await invoke_catalog(context, lambda service: service.delete_catalog(request))

    @router.post(
        "/session/export",
        response_model=CatalogSessionExport,
        responses=_CATALOG_ERROR_RESPONSES,
    )
    async def export_session(request: CatalogSessionExportRequest, context: Context):
        return await invoke_catalog(context, lambda service: service.export_session(request))

    @router.post(
        "/session/import",
        response_model=CatalogImportResult,
        responses=_CATALOG_ERROR_RESPONSES,
    )
    async def import_session(request: CatalogClosureImportRequest, context: Context):
        return await invoke_catalog(context, lambda service: service.import_closure(request))

    return router
