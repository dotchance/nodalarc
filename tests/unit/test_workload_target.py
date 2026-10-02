"""The primary workload target: published by the Operator, read one way."""

from __future__ import annotations

from typing import Any

import pytest
from nodalarc.workload_target import (
    NODE_ID_LABEL,
    PRIMARY_CONTAINER_ANNOTATION,
    WorkloadTargetError,
    select_live_pod,
    validate_node_id,
    workload_target_from_pod,
)


def pod_document(
    node_id: str = "leo-sat-p00s00",
    *,
    name: str | None = None,
    uid: str = "uid-1",
    primary: str | None = "custom-router",
    containers: tuple[str, ...] = ("observer", "custom-router"),
    deleting: bool = False,
    labels: dict[str, str] | None = None,
) -> dict[str, Any]:
    """One session pod in Kubernetes API JSON form; the sidecar is listed first."""

    metadata: dict[str, Any] = {
        "name": name or node_id,
        "namespace": "nodalarc",
        "uid": uid,
        "labels": {NODE_ID_LABEL: node_id} if labels is None else labels,
        "annotations": {PRIMARY_CONTAINER_ANNOTATION: primary} if primary is not None else {},
    }
    if deleting:
        metadata["deletionTimestamp"] = "2026-09-07T00:00:00Z"
    return {
        "metadata": metadata,
        "spec": {"containers": [{"name": container} for container in containers]},
    }


def test_renamed_profile_with_a_sidecar_targets_the_annotated_container() -> None:
    target = workload_target_from_pod(pod_document())

    assert target.container == "custom-router"
    assert target.pod_name == "leo-sat-p00s00"
    assert target.pod_uid == "uid-1"
    assert target.namespace == "nodalarc"
    assert target.node_id == "leo-sat-p00s00"


@pytest.mark.parametrize(
    ("pod", "fragment"),
    [
        (pod_document(primary=None), "carries no nodalarc.io/primary-container annotation"),
        (pod_document(primary=""), "carries no nodalarc.io/primary-container annotation"),
        (pod_document(primary="frr"), "annotates primary container 'frr' but declares"),
        (pod_document(labels={}), "carries no nodalarc.io/node-id label"),
        (pod_document(uid=""), "lacks a uid"),
    ],
)
def test_missing_or_invalid_target_metadata_is_refused(pod: dict[str, Any], fragment: str) -> None:
    with pytest.raises(WorkloadTargetError) as raised:
        workload_target_from_pod(pod)

    assert fragment in str(raised.value)


def test_live_pod_selection_skips_the_pod_being_replaced() -> None:
    old = pod_document(uid="uid-old", deleting=True)
    new = pod_document(uid="uid-new")

    assert select_live_pod([old, new], "leo-sat-p00s00") is new
    with pytest.raises(WorkloadTargetError, match="found 0"):
        select_live_pod([old], "leo-sat-p00s00")
    with pytest.raises(WorkloadTargetError, match="found 2"):
        select_live_pod([new, pod_document(uid="uid-other", name="dup")], "leo-sat-p00s00")


def test_node_id_validation_rejects_label_unsafe_values() -> None:
    assert validate_node_id("leo-sat-p00s00") == "leo-sat-p00s00"
    for value in ("", "Leo-Sat", "leo sat", "a=b", None, "x" * 64):
        with pytest.raises(ValueError, match="invalid node id"):
            validate_node_id(value)
