"""The Operator's one owner of session-pod state: classification, derivation, fenced deletion."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import create_autospec

import kubernetes.client
import pytest
from nodalarc_operator.session_pods import (
    DeletionOutcome,
    OwnerIdentity,
    PodClass,
    SessionPodIdentity,
    SessionPodStateError,
    delete_all_owned_pods,
    delete_conflicting_pod,
    delete_ineligible_pods,
    observe_owned_session_pods,
    observe_session_pods,
    owned_session_run_ids,
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


def _v1(pods) -> kubernetes.client.CoreV1Api:
    v1 = create_autospec(kubernetes.client.CoreV1Api, instance=True)
    v1.list_namespaced_pod.return_value = kubernetes.client.V1PodList(items=list(pods))
    return v1


def _current_set():
    return [_pod(node_id) for node_id in EXPECTED]


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

    def test_node_identity_comes_from_the_label_not_the_name(self):
        # A pod named for an expected node but labelled for another is surplus.
        pod = _pod("sat-p99s99", name="sat-p00s00")
        view = observe_session_pods(_v1([pod]), "nodalarc", _identity())
        assert view.pods[0].pod_class is PodClass.SURPLUS
        assert "sat-p00s00" in view.missing_node_ids

    def test_current_does_not_require_scheduling_or_running(self):
        pending = _pod("sat-p00s00", k8s_node=None, pod_ip=None, phase="Pending", running=0)
        assert _identity().classify(pending) is PodClass.CURRENT


class TestObservation:
    def test_one_list_per_observation(self):
        v1 = _v1(_current_set())
        view = observe_session_pods(v1, "nodalarc", _identity())
        view.placement()
        view.pod_ips()
        _ = (view.running_count, view.provisioned_count, view.missing_node_ids)
        v1.list_namespaced_pod.assert_called_once_with(
            "nodalarc", label_selector="nodalarc.io/node-id"
        )

    def test_complete_session(self):
        view = observe_session_pods(_v1(_current_set()), "nodalarc", _identity())
        assert view.complete
        assert view.current_node_ids == frozenset(EXPECTED)
        assert view.running_count == 3
        assert view.provisioned_count == 3

    def test_foreign_pod_is_never_counted(self):
        pods = [*_current_set(), _pod("sat-p00s00", name="other", owner_uid="other-uid")]
        view = observe_session_pods(_v1(pods), "nodalarc", _identity())
        assert len(view.foreign) == 1
        assert view.running_count == 3
        assert not view.complete
        assert "other (owner: current-session/other-uid)" in view.describe_foreign()

    def test_duplicate_current_pods_for_one_node_are_refused(self):
        pods = [_pod("sat-p00s00"), _pod("sat-p00s00", name="sat-p00s00-copy", uid="uid-copy")]
        with pytest.raises(SessionPodStateError, match="more than one current session pod"):
            observe_session_pods(_v1(pods), "nodalarc", _identity())

    def test_provisioned_needs_a_scheduled_pod_with_an_ip(self):
        pods = [
            _pod("sat-p00s00", phase="Pending", running=0),
            _pod("sat-p00s01", pod_ip=None, phase="Pending", running=0),
            _pod("gs-denver", k8s_node=None, pod_ip=None, phase="Pending", running=0),
        ]
        view = observe_session_pods(_v1(pods), "nodalarc", _identity())
        assert view.provisioned_count == 1
        assert view.running_count == 0

    def test_running_requires_every_authored_container(self):
        pods = [
            _pod("sat-p00s00", containers=2, running=1),
            _pod("sat-p00s01", containers=2),
            _pod("gs-denver"),
        ]
        view = observe_session_pods(_v1(pods), "nodalarc", _identity())
        assert view.provisioned_count == 3
        assert view.running_count == 2

    def test_placement_excludes_stale_and_terminating_pods(self):
        pods = [
            _pod("sat-p00s00", k8s_node="node02"),
            _pod("sat-p00s01", k8s_node="node03"),
            _pod("gs-denver", k8s_node="node01"),
            _pod("sat-p00s00", name="old-a", uid="old-a", run="run-old", k8s_node="node09"),
            _pod("sat-p00s01", name="old-b", uid="old-b", terminating=True, k8s_node="node09"),
        ]
        view = observe_session_pods(_v1(pods), "nodalarc", _identity())
        assert view.placement() == {
            "sat-p00s00": "node02",
            "sat-p00s01": "node03",
            "gs-denver": "node01",
        }

    def test_placement_refuses_an_unscheduled_expected_node(self):
        pods = [_pod("sat-p00s00"), _pod("sat-p00s01"), _pod("gs-denver", k8s_node=None)]
        view = observe_session_pods(_v1(pods), "nodalarc", _identity())
        with pytest.raises(SessionPodStateError, match="missing session pod placement.*gs-denver"):
            view.placement()

    def test_pod_ips_come_from_current_pods_only(self):
        pods = [
            _pod("sat-p00s00", pod_ip="10.42.0.1"),
            _pod("sat-p00s01", pod_ip=None, phase="Pending", running=0),
            _pod("gs-denver", run="run-old", pod_ip="10.42.9.9"),
        ]
        view = observe_session_pods(_v1(pods), "nodalarc", _identity())
        assert view.pod_ips() == {"sat-p00s00": "10.42.0.1"}


def _delete_call(v1):
    args, kwargs = v1.delete_namespaced_pod.call_args
    return args[0], args[1], kwargs["body"].preconditions.uid


class TestFencedDeletion:
    def test_current_pods_are_preserved_and_stale_and_surplus_are_deleted_by_uid(self):
        pods = [
            _pod("sat-p00s00"),
            _pod("sat-p00s01", run="run-old", uid="uid-stale"),
            _pod("sat-p99s99", uid="uid-surplus"),
            _pod("gs-denver"),
        ]
        v1 = _v1(pods)
        view = observe_session_pods(v1, "nodalarc", _identity())
        deletions = delete_ineligible_pods(v1, "nodalarc", view)
        assert [(d.pod_name, d.pod_uid, d.outcome) for d in deletions] == [
            ("sat-p00s01", "uid-stale", DeletionOutcome.ACCEPTED),
            ("sat-p99s99", "uid-surplus", DeletionOutcome.ACCEPTED),
        ]
        deleted = [call.args[0] for call in v1.delete_namespaced_pod.call_args_list]
        assert deleted == ["sat-p00s01", "sat-p99s99"]
        for call in v1.delete_namespaced_pod.call_args_list:
            assert call.kwargs["body"].preconditions.uid.startswith("uid-")

    def test_foreign_and_terminating_pods_are_never_deleted(self):
        pods = [
            *_current_set(),
            _pod("sat-p00s00", name="foreign", owner_uid="other-uid"),
            _pod("sat-p99s99", name="leaving", terminating=True),
        ]
        v1 = _v1(pods)
        view = observe_session_pods(v1, "nodalarc", _identity())
        assert delete_ineligible_pods(v1, "nodalarc", view) == ()
        v1.delete_namespaced_pod.assert_not_called()

    def test_same_name_replacement_between_observation_and_delete_is_not_deleted(self):
        """The API refuses the UID precondition with 409: a replacement pod survives."""
        v1 = _v1([_pod("sat-p99s99", uid="uid-observed")])
        view = observe_session_pods(v1, "nodalarc", _identity())
        v1.delete_namespaced_pod.side_effect = kubernetes.client.rest.ApiException(
            status=409, reason="Precondition failed: UID in precondition: uid-observed"
        )
        (deletion,) = delete_ineligible_pods(v1, "nodalarc", view)
        assert deletion.outcome is DeletionOutcome.CONFLICT
        assert "Precondition failed" in deletion.reason
        assert _delete_call(v1) == ("sat-p99s99", "nodalarc", "uid-observed")

    def test_conflict_keeps_the_api_servers_explanation_from_the_body(self, caplog):
        explanation = (
            "Precondition failed: UID in precondition: uid-observed, "
            "UID in object meta: uid-replacement"
        )
        v1 = _v1([_pod("sat-p99s99", uid="uid-observed")])
        view = observe_session_pods(v1, "nodalarc", _identity())
        error = kubernetes.client.rest.ApiException(status=409, reason="Conflict")
        error.body = (
            '{"kind":"Status","apiVersion":"v1","status":"Failure",'
            f'"message":"{explanation}","reason":"Conflict","code":409}}'
        )
        v1.delete_namespaced_pod.side_effect = error
        with caplog.at_level("WARNING", logger="nodalarc_operator.session_pods"):
            (deletion,) = delete_ineligible_pods(v1, "nodalarc", view)
        assert deletion.outcome is DeletionOutcome.CONFLICT
        assert deletion.reason == "Conflict"
        assert deletion.detail == explanation
        message = " ".join(record.getMessage() for record in caplog.records)
        assert explanation in message
        assert "nodalarc/sat-p99s99 uid=uid-observed" in message

    def test_conflict_with_a_non_json_body_keeps_the_raw_text(self):
        v1 = _v1([_pod("sat-p99s99")])
        view = observe_session_pods(v1, "nodalarc", _identity())
        error = kubernetes.client.rest.ApiException(status=409, reason="Conflict")
        error.body = b"object was replaced"
        v1.delete_namespaced_pod.side_effect = error
        (deletion,) = delete_ineligible_pods(v1, "nodalarc", view)
        assert deletion.detail == "object was replaced"

    def test_first_conflict_ends_the_pass(self):
        pods = [_pod("sat-p98s98", uid="uid-1"), _pod("sat-p99s99", uid="uid-2")]
        v1 = _v1(pods)
        view = observe_session_pods(v1, "nodalarc", _identity())
        v1.delete_namespaced_pod.side_effect = kubernetes.client.rest.ApiException(status=409)
        deletions = delete_ineligible_pods(v1, "nodalarc", view)
        assert len(deletions) == 1
        v1.delete_namespaced_pod.assert_called_once()

    def test_absent_target_is_reported_as_absent(self):
        v1 = _v1([_pod("sat-p99s99")])
        view = observe_session_pods(v1, "nodalarc", _identity())
        v1.delete_namespaced_pod.side_effect = kubernetes.client.rest.ApiException(status=404)
        (deletion,) = delete_ineligible_pods(v1, "nodalarc", view)
        assert deletion.outcome is DeletionOutcome.ABSENT

    def test_other_api_failures_propagate_with_the_target(self):
        v1 = _v1([_pod("sat-p99s99", uid="uid-x")])
        view = observe_session_pods(v1, "nodalarc", _identity())
        v1.delete_namespaced_pod.side_effect = kubernetes.client.rest.ApiException(
            status=500, reason="Internal Server Error"
        )
        with pytest.raises(kubernetes.client.rest.ApiException) as raised:
            delete_ineligible_pods(v1, "nodalarc", view)
        assert raised.value.status == 500
        assert "delete session pod nodalarc/sat-p99s99 uid=uid-x" in raised.value.__notes__

    def test_transport_failures_propagate_unchanged(self):
        v1 = _v1([_pod("sat-p99s99", uid="uid-x")])
        view = observe_session_pods(v1, "nodalarc", _identity())
        v1.delete_namespaced_pod.side_effect = ConnectionResetError("reset by peer")
        with pytest.raises(ConnectionResetError, match="reset by peer"):
            delete_ineligible_pods(v1, "nodalarc", view)

    def test_a_pod_without_an_observed_uid_is_never_deleted(self):
        v1 = _v1([_pod("sat-p99s99", uid="")])
        pod = v1.list_namespaced_pod.return_value.items[0]
        pod.metadata.uid = None
        view = observe_session_pods(v1, "nodalarc", _identity())
        with pytest.raises(SessionPodStateError, match="observed uid"):
            delete_ineligible_pods(v1, "nodalarc", view)
        v1.delete_namespaced_pod.assert_not_called()


class TestCreateConflict:
    def test_replaceable_conflict_is_deleted_by_uid(self):
        v1 = _v1([])
        pod = _pod("sat-p00s00", selection=OTHER_SELECTION, uid="uid-old")
        deletion = delete_conflicting_pod(v1, "nodalarc", pod, _identity())
        assert deletion.outcome is DeletionOutcome.ACCEPTED
        assert _delete_call(v1) == ("sat-p00s00", "nodalarc", "uid-old")

    def test_foreign_conflict_is_refused_with_its_owner(self):
        v1 = _v1([])
        pod = _pod("sat-p00s00", owner_uid="other-uid")
        with pytest.raises(SessionPodStateError, match="not owned by the current.*other-uid"):
            delete_conflicting_pod(v1, "nodalarc", pod, _identity())
        v1.delete_namespaced_pod.assert_not_called()

    def test_terminating_conflict_is_refused(self):
        v1 = _v1([])
        with pytest.raises(SessionPodStateError, match="is deleting"):
            delete_conflicting_pod(
                v1, "nodalarc", _pod("sat-p00s00", terminating=True), _identity()
            )
        v1.delete_namespaced_pod.assert_not_called()


class TestFailedSelectionCleanup:
    """Owner-scoped: works without any prepared workload selection."""

    def test_deletes_every_owned_pod_and_counts_terminating_ones_as_remaining(self):
        pods = [
            _pod("sat-p00s00", uid="uid-a", selection=None, run=None),
            _pod("sat-p00s01", uid="uid-b", terminating=True),
            _pod("gs-denver", uid="uid-c", owner_uid="other-uid"),
        ]
        v1 = _v1(pods)
        remaining, deletions = delete_all_owned_pods(
            v1, "nodalarc", OwnerIdentity.from_owner_ref(OWNER_REF)
        )
        assert remaining == 2
        assert [(d.pod_name, d.pod_uid) for d in deletions] == [("sat-p00s00", "uid-a")]
        assert _delete_call(v1) == ("sat-p00s00", "nodalarc", "uid-a")

    def test_conflict_keeps_the_pod_counted(self):
        v1 = _v1([_pod("sat-p00s00", uid="uid-a")])
        v1.delete_namespaced_pod.side_effect = kubernetes.client.rest.ApiException(status=409)
        remaining, deletions = delete_all_owned_pods(
            v1, "nodalarc", OwnerIdentity.from_owner_ref(OWNER_REF)
        )
        assert remaining == 1
        assert deletions[0].outcome is DeletionOutcome.CONFLICT

    def test_owned_observation_separates_foreign_pods(self):
        pods = [_pod("sat-p00s00"), _pod("gs-denver", owner_uid="other-uid")]
        observation = observe_owned_session_pods(
            _v1(pods), "nodalarc", OwnerIdentity.from_owner_ref(OWNER_REF)
        )
        assert [pod.name for pod in observation.owned] == ["sat-p00s00"]
        assert [pod.name for pod in observation.foreign] == ["gs-denver"]


def _configmap(name: str, data: dict[str, str], owner_uid: str = "cr-uid") -> SimpleNamespace:
    return SimpleNamespace(
        metadata=SimpleNamespace(
            name=name,
            owner_references=[SimpleNamespace(name="current-session", uid=owner_uid)],
        ),
        data=data,
    )


class TestTeardownRunIds:
    def _v1(self, pods, configmaps):
        v1 = _v1(pods)

        def _read(name, namespace):
            if name in configmaps:
                return configmaps[name]
            raise kubernetes.client.rest.ApiException(status=404)

        v1.read_namespaced_config_map.side_effect = _read
        return v1

    def test_keeps_terminating_pod_and_both_configmap_run_ids(self):
        v1 = self._v1(
            [_pod("sat-p00s00", run="run-a", terminating=True, selection=None)],
            {
                "nodalarc-session": _configmap("nodalarc-session", {"session_run_id": "run-b"}),
                "nodalarc-topology-wiring": _configmap(
                    "nodalarc-topology-wiring", {"session_id": "run-c"}
                ),
            },
        )
        run_ids = owned_session_run_ids(v1, "nodalarc", OwnerIdentity.from_owner_ref(OWNER_REF))
        assert run_ids == ("run-a", "run-b", "run-c")

    def test_refuses_a_foreign_session_pod(self):
        v1 = self._v1([_pod("sat-p00s00", owner_uid="other-uid")], {})
        with pytest.raises(ValueError, match="not owned by ConstellationSpec"):
            owned_session_run_ids(v1, "nodalarc", OwnerIdentity.from_owner_ref(OWNER_REF))

    def test_refuses_a_foreign_configmap(self):
        v1 = self._v1(
            [],
            {"nodalarc-session": _configmap("nodalarc-session", {"session_run_id": "x"}, "o")},
        )
        with pytest.raises(ValueError, match="ConfigMap 'nodalarc-session' is not owned"):
            owned_session_run_ids(v1, "nodalarc", OwnerIdentity.from_owner_ref(OWNER_REF))
