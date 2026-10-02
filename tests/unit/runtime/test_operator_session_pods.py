"""The Operator's one owner of session-pod state: classification, derivation, fenced deletion."""

from __future__ import annotations

import kubernetes.client
import pytest
from nodalarc_operator.session_pods import (
    PodClass,
    SessionPodIdentity,
    SessionPodStateError,
)

OWNER_REF = {
    "apiVersion": "nodalarc.io/v1alpha1",
    "kind": "ConstellationSpec",
    "name": "current-session",
    "uid": "cr-uid",
    "blockOwnerDeletion": True,
}
RUN = "run-test-0002"
SELECTION = "profiles@sha256:" + "a" * 64
OTHER_SELECTION = "profiles@sha256:" + "b" * 64
EXPECTED = ("sat-p00s00", "sat-p00s01", "gs-denver")


def _identity(node_ids=EXPECTED) -> SessionPodIdentity:
    return SessionPodIdentity.for_session(
        owner_ref=OWNER_REF,
        session_run_id=RUN,
        selection_identity=SELECTION,
        node_ids=node_ids,
    )


def _pod(
    node_id: str,
    *,
    name: str | None = None,
    uid: str | None = None,
    owner_name: str = "current-session",
    owner_uid: str | None = "cr-uid",
    run: str | None = RUN,
    owner_uid_label: str | None = "cr-uid",
    selection: str | None = SELECTION,
    terminating: bool = False,
    k8s_node: str | None = "node01",
    pod_ip: str | None = "10.42.0.5",
    phase: str = "Running",
    containers: int = 1,
    running: int | None = None,
) -> kubernetes.client.V1Pod:
    labels = {"nodalarc.io/session": "true", "nodalarc.io/node-id": node_id}
    if run is not None:
        labels["nodalarc.io/session-run-id"] = run
    if owner_uid_label is not None:
        labels["nodalarc.io/owner-uid"] = owner_uid_label
    running_count = containers if running is None else running
    return kubernetes.client.V1Pod(
        metadata=kubernetes.client.V1ObjectMeta(
            name=name or node_id.lower(),
            uid=uid or f"uid-{node_id.lower()}",
            labels=labels,
            annotations=(
                {"nodalarc.io/workload-selection": selection} if selection is not None else None
            ),
            owner_references=(
                [
                    kubernetes.client.V1OwnerReference(
                        api_version="nodalarc.io/v1alpha1",
                        kind="ConstellationSpec",
                        name=owner_name,
                        uid=owner_uid,
                    )
                ]
                if owner_uid is not None
                else None
            ),
            deletion_timestamp="2026-09-22T00:00:00Z" if terminating else None,
        ),
        spec=kubernetes.client.V1PodSpec(
            node_name=k8s_node,
            containers=[kubernetes.client.V1Container(name=f"c{i}") for i in range(containers)],
        ),
        status=kubernetes.client.V1PodStatus(
            phase=phase,
            pod_ip=pod_ip,
            container_statuses=[
                kubernetes.client.V1ContainerStatus(
                    name=f"c{i}",
                    image="test",
                    image_id="test",
                    ready=True,
                    restart_count=0,
                    state=kubernetes.client.V1ContainerState(
                        running=(
                            kubernetes.client.V1ContainerStateRunning()
                            if i < running_count
                            else None
                        ),
                        waiting=(
                            None
                            if i < running_count
                            else kubernetes.client.V1ContainerStateWaiting(reason="Starting")
                        ),
                    ),
                )
                for i in range(containers)
            ],
        ),
    )


class TestIdentity:
    def test_empty_expected_set_is_refused(self):
        with pytest.raises(SessionPodStateError, match="0 nodes"):
            _identity(node_ids=())

    def test_canonicalization_collision_is_refused(self):
        with pytest.raises(SessionPodStateError, match="does not match the resolved node count"):
            _identity(node_ids=("sat-A", "sat-a"))

    def test_owner_without_uid_is_refused(self):
        with pytest.raises(SessionPodStateError, match="name and uid"):
            SessionPodIdentity.for_session(
                owner_ref={"name": "current-session"},
                session_run_id=RUN,
                selection_identity=SELECTION,
                node_ids=EXPECTED,
            )

    def test_missing_selection_identity_is_refused(self):
        with pytest.raises(SessionPodStateError, match="selection identity"):
            SessionPodIdentity.for_session(
                owner_ref=OWNER_REF,
                session_run_id=RUN,
                selection_identity="",
                node_ids=EXPECTED,
            )

    def test_expected_ids_are_canonical_and_count_derives_from_them(self):
        identity = _identity(node_ids=("SAT-P00S00", "gs-Denver"))
        assert identity.expected_node_ids == frozenset({"sat-p00s00", "gs-denver"})
        assert identity.expected_count == 2


class TestCurrencyClassification:
    """One rule decides both a Ready claim and a deletion."""

    @pytest.mark.parametrize(
        ("pod", "expected_class"),
        [
            (_pod("sat-p00s00"), PodClass.CURRENT),
            (_pod("SAT-P00S00"), PodClass.CURRENT),
            (_pod("sat-p00s00", run="run-old-0001"), PodClass.REPLACEABLE),
            (_pod("sat-p00s00", run=None), PodClass.REPLACEABLE),
            (_pod("sat-p00s00", owner_uid_label="old-uid"), PodClass.REPLACEABLE),
            (_pod("sat-p00s00", owner_uid_label=None), PodClass.REPLACEABLE),
            (_pod("sat-p00s00", selection=OTHER_SELECTION), PodClass.REPLACEABLE),
            (_pod("sat-p00s00", selection=None), PodClass.REPLACEABLE),
            (_pod("sat-p99s99"), PodClass.SURPLUS),
            (_pod("", name="unlabelled-value"), PodClass.SURPLUS),
            (_pod("sat-p00s00", terminating=True), PodClass.TERMINATING),
            (_pod("sat-p00s00", owner_uid="other-uid"), PodClass.FOREIGN),
            (_pod("sat-p00s00", owner_name="other-name"), PodClass.FOREIGN),
            (_pod("sat-p00s00", owner_uid=None), PodClass.FOREIGN),
            (_pod("sat-p00s00", owner_uid="other-uid", terminating=True), PodClass.FOREIGN),
        ],
    )
    def test_classification(self, pod, expected_class):
        assert _identity().classify(pod) is expected_class

    def test_current_does_not_require_scheduling_or_running(self):
        pending = _pod("sat-p00s00", k8s_node=None, pod_ip=None, phase="Pending", running=0)
        assert _identity().classify(pending) is PodClass.CURRENT
