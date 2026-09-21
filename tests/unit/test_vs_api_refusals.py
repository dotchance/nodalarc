"""One translation from refusing exceptions to the ApiRefusal envelope."""

from __future__ import annotations

import pytest
import vs_api.main as main
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from nodalarc.catalog_closure import (
    CatalogClosureError,
    CatalogClosureErrorCode,
    CatalogClosureErrorEvidence,
    CatalogDocumentNotFound,
    CatalogReadFailed,
    CatalogReadRejected,
)
from nodalarc.catalog_refs import CatalogRef, CatalogReferenceError, CatalogReferenceErrorCode
from nodalarc.catalog_repository import (
    CatalogConflictError,
    CatalogNotFoundError,
    CatalogRepositoryError,
    CatalogValidationError,
)
from nodalarc.prepared_session import (
    PreparedSessionError,
    PreparedSessionErrorCode,
    PreparedSessionErrorEvidence,
)
from nodalarc.resolve_session import SessionResolutionError
from nodalarc.runtime_support import UnsupportedFeature, UnsupportedFeatureError
from nodalarc.workload_target import WorkloadTargetError
from vs_api.introspect import IntrospectExecError
from vs_api.refusals import (
    CATALOG_CLOSURE_STATUS,
    INTERNAL_ERROR_CODE,
    PREPARED_SESSION_STATUS,
    SESSION_DEPLOYMENT_STATUS,
    install_refusal_handlers,
    refusal_from_exception,
    refusal_response,
)
from vs_api.session_deployment import (
    SessionDeploymentPreparationError,
    SessionDeploymentPreparationErrorCode,
    SessionDeploymentPreparationErrorEvidence,
)

from tests.asgi_client import ASGITestClient as TestClient

REF = CatalogRef("user:sessions/demo.yaml")
PRIVATE = "PRIVATE_DIAGNOSTIC_TEXT"


def _cases() -> list[tuple[BaseException, int, str]]:
    return [
        (
            SessionDeploymentPreparationError(
                SessionDeploymentPreparationErrorEvidence(
                    code=SessionDeploymentPreparationErrorCode.STALE_SOURCE,
                    message="Saved session revision changed after review",
                    session_ref=str(REF),
                )
            ),
            409,
            "session_deployment.stale_source",
        ),
        (
            PreparedSessionError(
                PreparedSessionErrorEvidence(
                    code=PreparedSessionErrorCode.NOT_READY,
                    message="Prepared session is not deploy-ready: [x] y",
                )
            ),
            422,
            "prepared_session.not_ready",
        ),
        (
            CatalogClosureError(
                CatalogClosureErrorEvidence(
                    code=CatalogClosureErrorCode.READ_FAILED,
                    message="Could not read catalog dependency user:nodes/n.yaml",
                )
            ),
            503,
            "catalog_closure.read_failed",
        ),
        (
            UnsupportedFeatureError(
                [UnsupportedFeature(category="central_body", value="mars", message="gated")]
            ),
            422,
            "runtime_support.unsupported",
        ),
        (
            SessionResolutionError("link rule matches no endpoint"),
            422,
            "session_resolution.invalid",
        ),
        (
            CatalogReferenceError("bad token", code=CatalogReferenceErrorCode.PATH_REJECTED),
            400,
            "catalog_reference.path_rejected",
        ),
        (
            CatalogDocumentNotFound(REF, f"no catalog document for {REF}"),
            404,
            "catalog_read.not_found",
        ),
        (
            CatalogReadRejected(REF, "catalog path contains symlink sessions/demo.yaml"),
            400,
            "catalog_read.rejected",
        ),
        (
            CatalogReadFailed(REF, f"could not read {REF} (PermissionError)"),
            503,
            "catalog_read.failed",
        ),
        (
            CatalogNotFoundError("catalog document does not exist: sessions/demo.yaml"),
            404,
            "catalog_repository.not_found",
        ),
        (
            CatalogConflictError("catalog session already exists"),
            409,
            "catalog_repository.conflict",
        ),
        (
            CatalogValidationError("catalog document is invalid"),
            422,
            "catalog_repository.invalid_document",
        ),
        (
            WorkloadTargetError("sat-p00s00", "expected one live session pod, found 0: []"),
            503,
            "workload_target.unavailable",
        ),
        (IntrospectExecError("Kubernetes exec failed"), 502, "introspect.exec_failed"),
    ]


@pytest.mark.parametrize(
    "exc, status, code",
    _cases(),
    ids=lambda value: (
        getattr(value, "__class__", type(value)).__name__
        if isinstance(value, BaseException)
        else str(value)
    ),
)
def test_each_family_translates_to_its_own_code_and_status(exc, status, code) -> None:
    outcome = refusal_from_exception(exc)

    assert outcome is not None
    assert outcome.status_code == status
    assert outcome.refusal.code == code
    assert outcome.refusal.message == str(exc)


def test_a_storage_failure_keeps_a_fixed_message_and_names_its_class() -> None:
    outcome = refusal_from_exception(CatalogRepositoryError(PRIVATE))

    assert outcome is not None
    assert outcome.status_code == 503
    assert outcome.refusal.code == "catalog_repository.unavailable"
    assert PRIVATE not in outcome.refusal.message
    assert outcome.refusal.cause_type == "CatalogRepositoryError"


def test_a_failure_that_is_not_a_refusal_is_not_translated() -> None:
    assert refusal_from_exception(RuntimeError(PRIVATE)) is None
    assert refusal_from_exception(ValueError(PRIVATE)) is None
    assert refusal_from_exception(OSError(PRIVATE)) is None


def test_every_enum_member_has_a_status() -> None:
    assert set(SESSION_DEPLOYMENT_STATUS) == set(SessionDeploymentPreparationErrorCode)
    assert set(PREPARED_SESSION_STATUS) == set(PreparedSessionErrorCode)
    assert set(CATALOG_CLOSURE_STATUS) == set(CatalogClosureErrorCode)


def _application() -> FastAPI:
    app = FastAPI()
    install_refusal_handlers(app)
    app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"])

    @app.get("/refused")
    def refused() -> dict:
        raise WorkloadTargetError("sat-p00s00", "expected one live session pod, found 0: []")

    @app.get("/failed")
    def failed() -> dict:
        raise RuntimeError(f"{PRIVATE} token=abc123 path=/var/run/secrets/key")

    @app.get("/stated")
    def stated():
        return refusal_response(409, "session_switch.conflict", "Switch already in progress")

    return app


def test_registered_handler_answers_a_refusal_with_the_envelope() -> None:
    response = TestClient(_application()).get("/refused")

    assert response.status_code == 503
    assert response.json() == {
        "code": "workload_target.unavailable",
        "message": "sat-p00s00: expected one live session pod, found 0: []",
    }


def test_an_unhandled_failure_is_a_500_envelope_without_its_text_or_class() -> None:
    response = TestClient(_application()).get("/failed", headers={"Origin": "https://ui.example"})

    assert response.status_code == 500
    assert response.json() == {"code": INTERNAL_ERROR_CODE, "message": "Request failed"}
    assert PRIVATE not in response.text
    assert "abc123" not in response.text
    assert "/var/run/secrets" not in response.text
    assert "RuntimeError" not in response.text
    # The answer passes through the header-owning middlewares like any other.
    assert response.headers["access-control-allow-origin"] == "*"


def test_an_unhandled_failure_on_the_service_keeps_its_security_headers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def broken(node_id: str, command: str):
        raise RuntimeError(f"{PRIVATE} /var/run/secrets/key")

    monkeypatch.setattr(main, "_API_KEY", "")
    monkeypatch.setattr(main, "run_vtysh", broken)

    response = TestClient(main.app).post(
        "/api/v1/introspect",
        json={"node_id": "sat-p00s00", "command": "show isis neighbor"},
        headers={"Origin": "https://ui.example"},
    )

    assert response.status_code == 500
    assert response.json() == {"code": INTERNAL_ERROR_CODE, "message": "Request failed"}
    assert PRIVATE not in response.text
    assert response.headers["access-control-allow-origin"] == "*"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["content-security-policy"] == "default-src 'self'"


def test_a_route_stated_refusal_uses_the_same_envelope() -> None:
    response = TestClient(_application()).get("/stated")

    assert response.status_code == 409
    assert response.json() == {
        "code": "session_switch.conflict",
        "message": "Switch already in progress",
    }


@pytest.mark.parametrize(
    "path, method",
    [
        ("/api/v1/sessions/switch", "post"),
        ("/api/v1/session/deploy-from-yaml", "post"),
        ("/api/v1/session/preview-coverage", "post"),
        ("/api/v1/introspect", "post"),
        ("/api/v1/sessions/yaml", "get"),
    ],
)
def test_refusing_routes_declare_the_envelope_in_openapi(path: str, method: str) -> None:
    document = main.app.openapi()
    operation = document["paths"][path][method]
    refusal = {"$ref": "#/components/schemas/ApiRefusal"}
    validation = {"$ref": "#/components/schemas/HTTPValidationError"}

    for status in ("400", "404", "409", "500", "502", "503"):
        schema = operation["responses"][status]["content"]["application/json"]["schema"]
        assert schema == refusal
    # A 422 is a refusal, or FastAPI's own request-validation body.
    schema = operation["responses"]["422"]["content"]["application/json"]["schema"]
    assert schema == {"anyOf": [refusal, validation]}
    assert "HTTPValidationError" in document["components"]["schemas"]


def test_a_request_that_fails_validation_answers_with_the_declared_validation_body(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(main, "_API_KEY", "")

    response = TestClient(main.app).post("/api/v1/introspect", json={"node_id": "sat-p00s00"})

    assert response.status_code == 422
    assert list(response.json()) == ["detail"]


def test_introspect_route_hides_the_kubernetes_client_reason_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import kubernetes.client
    from kubernetes.client.rest import ApiException

    class _DeniedCore:
        def list_namespaced_pod(self, namespace, *, label_selector):
            raise ApiException(
                status=0, reason=f"SSLError\n{PRIVATE} /private/review-only/client.pem"
            )

    monkeypatch.setattr(main, "_API_KEY", "")
    monkeypatch.setattr(kubernetes.config, "load_incluster_config", lambda: None)
    monkeypatch.setattr(kubernetes.client, "CoreV1Api", lambda: _DeniedCore())

    response = TestClient(main.app).post(
        "/api/v1/introspect", json={"node_id": "sat-p00s00", "command": "show isis neighbor"}
    )

    assert response.status_code == 503
    assert response.json() == {
        "code": "workload_target.unavailable",
        "message": "sat-p00s00: pod listing failed: HTTP 0",
    }
    assert PRIVATE not in response.text
    assert "/private" not in response.text


def test_introspect_route_answers_a_missing_target_with_the_envelope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def no_target(node_id: str, command: str):
        raise WorkloadTargetError(node_id, "expected one live session pod, found 0: []")

    monkeypatch.setattr(main, "_API_KEY", "")
    monkeypatch.setattr(main, "run_vtysh", no_target)

    response = TestClient(main.app).post(
        "/api/v1/introspect", json={"node_id": "sat-p99s99", "command": "show isis neighbor"}
    )

    assert response.status_code == 503
    assert response.json() == {
        "code": "workload_target.unavailable",
        "message": "sat-p99s99: expected one live session pod, found 0: []",
    }
