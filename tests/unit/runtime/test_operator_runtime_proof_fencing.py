"""Operator readiness fencing for OME and Scheduler runtime proofs."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from unittest.mock import patch

from kubernetes.client import (
    V1Deployment,
    V1DeploymentList,
    V1DeploymentSpec,
    V1DeploymentStatus,
    V1LabelSelector,
    V1ObjectMeta,
    V1Pod,
    V1PodList,
    V1PodTemplateSpec,
)
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


def _deployment(service: str, *, replicas: int = 1) -> V1Deployment:
    return V1Deployment(
        metadata=V1ObjectMeta(name=service, generation=4),
        spec=V1DeploymentSpec(
            replicas=1,
            selector=V1LabelSelector(match_labels={"app": service}),
            template=V1PodTemplateSpec(
                metadata=V1ObjectMeta(annotations={"nodalarc.io/config-hash": RUNTIME_HASH})
            ),
        ),
        status=V1DeploymentStatus(
            observed_generation=4,
            replicas=replicas,
            updated_replicas=1,
            ready_replicas=1,
            available_replicas=1,
            unavailable_replicas=0,
            terminating_replicas=0,
        ),
    )


def _pod(service: str) -> V1Pod:
    return V1Pod(
        metadata=V1ObjectMeta(
            name=f"{service}-pod",
            uid=f"{service}-pod-uid",
            annotations={"nodalarc.io/config-hash": RUNTIME_HASH},
        )
    )


def _retired_pod(service: str) -> V1Pod:
    return V1Pod(
        metadata=V1ObjectMeta(
            name=f"{service}-retired-pod",
            uid=f"{service}-retired-pod-uid",
            annotations={"nodalarc.io/config-hash": "a" * 64},
            deletion_timestamp=datetime(2026, 7, 10, tzinfo=UTC),
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


class _AppsV1:
    """The Deployments the cluster holds, listed one service at a time: OME, then Scheduler."""

    def __init__(self, *deployments: V1Deployment) -> None:
        self._answers = [V1DeploymentList(items=[deployment]) for deployment in deployments]

    def list_namespaced_deployment(self, *_args: object, **_kwargs: object) -> V1DeploymentList:
        return self._answers.pop(0)


class _HttpResponse:
    """A pod-proxy response read without preloading, as the Operator reads it."""

    def __init__(self, data: bytes) -> None:
        self.data = data


class _CoreV1:
    """The pods the cluster holds and the readiness document each one serves."""

    def __init__(self, pods: list[list[V1Pod]], proofs: dict[str, RuntimeConfigProof]) -> None:
        self._answers = [V1PodList(items=items) for items in pods]
        self._proofs = proofs
        self.pod_lists = 0
        self.readiness_requests = 0

    def list_namespaced_pod(self, *_args: object, **_kwargs: object) -> V1PodList:
        self.pod_lists += 1
        return self._answers.pop(0)

    def connect_get_namespaced_pod_proxy_with_path(
        self, name: str, _namespace: str, _path: str, **kwargs: object
    ) -> _HttpResponse:
        assert kwargs == {"_preload_content": False, "_request_timeout": 5}
        self.readiness_requests += 1
        proof = self._proofs["ome" if name.startswith("ome-") else "scheduler"]
        return _HttpResponse(
            json.dumps(
                {"status": "ready", "detail": "verified", "proof": proof.model_dump(mode="json")}
            ).encode("utf-8")
        )


def _clients(
    content_proof: RuntimeConfigProof,
    context: RuntimeDeploymentContext,
    *,
    stale_generation: bool = False,
    stale_pod_uid: bool = False,
    surplus_replicas: bool = False,
    retired_pod: bool = False,
) -> tuple[_AppsV1, _CoreV1]:
    apps_v1 = _AppsV1(
        _deployment("ome", replicas=2 if surplus_replicas else 1), _deployment("scheduler")
    )
    proofs = {
        service: _bound_service_proof(content_proof, context, service=service)
        for service in ("ome", "scheduler")
    }
    if stale_generation:
        proofs["scheduler"] = RuntimeConfigProof.model_validate(
            {
                **proofs["scheduler"].model_dump(mode="json"),
                "cr_generation": context.cr_generation - 1,
            },
            strict=True,
        )
    if stale_pod_uid:
        proofs["scheduler"] = RuntimeConfigProof.model_validate(
            {**proofs["scheduler"].model_dump(mode="json"), "pod_uid": "retired-scheduler-pod-uid"},
            strict=True,
        )
    ome_pods = [_pod("ome"), *([_retired_pod("ome")] if retired_pod else [])]
    return apps_v1, _CoreV1([ome_pods, [_pod("scheduler")]], proofs)


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

    assert v1.readiness_requests == 2


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
    assert v1.pod_lists == 0 and v1.readiness_requests == 0


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
