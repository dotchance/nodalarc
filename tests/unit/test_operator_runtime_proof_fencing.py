"""Operator readiness fencing for OME and Scheduler runtime proofs."""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from nodalarc.runtime_config import RuntimeConfigProof, RuntimeDeploymentContext
from nodalarc_operator.session_deployer import check_platform_runtime_ready

NAMESPACE = "nodalarc-test"
RUNTIME_HASH = "b" * 64


def _digest(character: str) -> str:
    return f"sha256:{character * 64}"


def _content_proof() -> RuntimeConfigProof:
    return RuntimeConfigProof(
        source_origin="operator.reconcile",
        run_id="run-runtime-proof-0001",
        upload_id="operator-test-upload",
        document_digest=_digest("2"),
        closure_digest=_digest("3"),
        resolved_semantic_digest=_digest("4"),
        file_count=7,
        total_bytes=4096,
        resolved_node_count=12,
    )


def _deployment_context() -> RuntimeDeploymentContext:
    return RuntimeDeploymentContext(
        cr_uid="cr-runtime-proof-0001",
        cr_generation=6,
        session_run_id="run-runtime-proof-0001",
        upload_id="operator-test-upload",
        document_digest=_digest("2"),
        closure_digest=_digest("3"),
        resolved_semantic_digest=_digest("4"),
        release="nodalarc-test",
        build="test-build",
    )


def _deployment(service: str) -> object:
    return SimpleNamespace(
        metadata=SimpleNamespace(name=service, generation=4),
        spec=SimpleNamespace(
            replicas=1,
            template=SimpleNamespace(
                metadata=SimpleNamespace(annotations={"nodalarc.io/config-hash": RUNTIME_HASH})
            ),
        ),
        status=SimpleNamespace(
            observed_generation=4,
            replicas=1,
            updated_replicas=1,
            ready_replicas=1,
            available_replicas=1,
            unavailable_replicas=0,
            terminating_replicas=0,
        ),
    )


def _pod(service: str) -> object:
    return SimpleNamespace(
        metadata=SimpleNamespace(
            name=f"{service}-pod",
            uid=f"{service}-pod-uid",
            annotations={"nodalarc.io/config-hash": RUNTIME_HASH},
            deletion_timestamp=None,
        )
    )


def _retired_pod(service: str) -> object:
    return SimpleNamespace(
        metadata=SimpleNamespace(
            name=f"{service}-retired-pod",
            uid=f"{service}-retired-pod-uid",
            annotations={"nodalarc.io/config-hash": "a" * 64},
            deletion_timestamp="2026-07-10T00:00:00Z",
        )
    )


def _bound_service_proof(
    content_proof: RuntimeConfigProof,
    context: RuntimeDeploymentContext,
    *,
    service: str,
) -> RuntimeConfigProof:
    origin = "ome" if service == "ome" else "scheduler"
    service_proof = RuntimeConfigProof.model_validate(
        {**content_proof.model_dump(mode="json"), "source_origin": origin},
        strict=True,
    )
    return service_proof.bind_deployment_identity(
        context,
        pod_uid=f"{service}-pod-uid",
    )


def _clients(
    content_proof: RuntimeConfigProof,
    context: RuntimeDeploymentContext,
    *,
    stale_generation: bool = False,
    stale_pod_uid: bool = False,
    surplus_replicas: bool = False,
    retired_pod: bool = False,
) -> tuple[MagicMock, MagicMock]:
    apps_v1 = MagicMock()
    ome_deployment = _deployment("ome")
    if surplus_replicas:
        ome_deployment.status.replicas = 2
    apps_v1.list_namespaced_deployment.side_effect = [
        SimpleNamespace(items=[ome_deployment]),
        SimpleNamespace(items=[_deployment("scheduler")]),
    ]
    v1 = MagicMock()
    ome_pods = [_pod("ome")]
    if retired_pod:
        ome_pods.append(_retired_pod("ome"))
    v1.list_namespaced_pod.side_effect = [
        SimpleNamespace(items=ome_pods),
        SimpleNamespace(items=[_pod("scheduler")]),
    ]

    def readiness(name: str, _namespace: str, path: str, **kwargs: object) -> object:
        assert kwargs == {"_preload_content": False, "_request_timeout": 5}
        service = "ome" if name.startswith("ome-") else "scheduler"
        proof = _bound_service_proof(content_proof, context, service=service)
        if stale_generation and service == "scheduler":
            proof = RuntimeConfigProof.model_validate(
                {**proof.model_dump(mode="json"), "cr_generation": context.cr_generation - 1},
                strict=True,
            )
        if stale_pod_uid and service == "scheduler":
            proof = RuntimeConfigProof.model_validate(
                {**proof.model_dump(mode="json"), "pod_uid": "retired-scheduler-pod-uid"},
                strict=True,
            )
        return SimpleNamespace(
            data=json.dumps(
                {"status": "ready", "detail": "verified", "proof": proof.model_dump(mode="json")}
            ).encode("utf-8")
        )

    v1.connect_get_namespaced_pod_proxy_with_path.side_effect = readiness
    return apps_v1, v1


def test_platform_readiness_accepts_only_exact_current_pod_proofs() -> None:
    content_proof = _content_proof()
    context = _deployment_context()
    apps_v1, v1 = _clients(content_proof, context)

    with (
        patch("nodalarc_operator.session_deployer._get_apps_v1", return_value=apps_v1),
        patch("nodalarc_operator.session_deployer._get_v1", return_value=v1),
    ):
        assert check_platform_runtime_ready(
            NAMESPACE,
            RUNTIME_HASH,
            content_proof,
            context,
        ) == (True, "OME and Scheduler runtime configuration verified")

    assert v1.connect_get_namespaced_pod_proxy_with_path.call_count == 2


def test_platform_readiness_rejects_stale_cr_generation_proof() -> None:
    content_proof = _content_proof()
    context = _deployment_context()
    apps_v1, v1 = _clients(content_proof, context, stale_generation=True)

    with (
        patch("nodalarc_operator.session_deployer._get_apps_v1", return_value=apps_v1),
        patch("nodalarc_operator.session_deployer._get_v1", return_value=v1),
    ):
        ready, detail = check_platform_runtime_ready(
            NAMESPACE,
            RUNTIME_HASH,
            content_proof,
            context,
        )

    assert ready is False
    assert "Scheduler runtime proof" in detail


def test_platform_readiness_rejects_retired_pod_uid_proof() -> None:
    content_proof = _content_proof()
    context = _deployment_context()
    apps_v1, v1 = _clients(content_proof, context, stale_pod_uid=True)

    with (
        patch("nodalarc_operator.session_deployer._get_apps_v1", return_value=apps_v1),
        patch("nodalarc_operator.session_deployer._get_v1", return_value=v1),
    ):
        ready, detail = check_platform_runtime_ready(
            NAMESPACE,
            RUNTIME_HASH,
            content_proof,
            context,
        )

    assert ready is False
    assert "Scheduler runtime proof" in detail


def test_platform_readiness_rejects_surplus_deployment_replicas() -> None:
    content_proof = _content_proof()
    context = _deployment_context()
    apps_v1, v1 = _clients(content_proof, context, surplus_replicas=True)

    with (
        patch("nodalarc_operator.session_deployer._get_apps_v1", return_value=apps_v1),
        patch("nodalarc_operator.session_deployer._get_v1", return_value=v1),
    ):
        ready, detail = check_platform_runtime_ready(
            NAMESPACE,
            RUNTIME_HASH,
            content_proof,
            context,
        )

    assert ready is False
    assert "OME proof-gated readiness" in detail
    v1.list_namespaced_pod.assert_not_called()


def test_platform_readiness_waits_for_retired_pod_deletion() -> None:
    content_proof = _content_proof()
    context = _deployment_context()
    apps_v1, v1 = _clients(content_proof, context, retired_pod=True)

    with (
        patch("nodalarc_operator.session_deployer._get_apps_v1", return_value=apps_v1),
        patch("nodalarc_operator.session_deployer._get_v1", return_value=v1),
    ):
        ready, detail = check_platform_runtime_ready(
            NAMESPACE,
            RUNTIME_HASH,
            content_proof,
            context,
        )

    assert ready is False
    assert "retired OME runtime pod deletion" in detail
