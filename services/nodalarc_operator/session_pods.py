# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""The Operator's one owner of session-pod state.

A session pod is any pod carrying ``nodalarc.io/node-id``. This module decides
which of them belong to the active ConstellationSpec, which are current for
its run and workload selection, what membership, placement and addresses
follow from one observation, and which may be deleted. Every Operator
consumer reads pod state through it; nothing else lists, classifies, counts
or deletes session pods.

One observation is one Kubernetes LIST. Everything derived from it describes
that instant; a claim that a mutation completed needs a later observation.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

import kubernetes
from nodalarc.nats_channels import sanitize_session_id
from nodalarc.runtime_service_config import SESSION_RUN_ID_FILENAME
from nodalarc.substrate.manifest_contract import POD_OWNER_UID_LABEL, POD_SESSION_RUN_LABEL
from nodalarc.workload_target import NODE_ID_LABEL

from nodalarc_operator.workloads.materializer import WORKLOAD_SELECTION_ANNOTATION

log = logging.getLogger(__name__)


class SessionPodStateError(ValueError):
    """An expected set or an observation the Operator refuses to derive from."""


class PodClass(StrEnum):
    """Where one observed session pod stands against the desired session."""

    CURRENT = "current"
    REPLACEABLE = "replaceable"
    SURPLUS = "surplus"
    TERMINATING = "terminating"
    FOREIGN = "foreign"


DELETABLE_CLASSES = frozenset({PodClass.REPLACEABLE, PodClass.SURPLUS})


class DeletionOutcome(StrEnum):
    """What one DELETE request established. Only observation proves absence."""

    ACCEPTED = "accepted"
    ABSENT = "absent"
    CONFLICT = "conflict"


def _metadata(obj: Any) -> Any:
    return getattr(obj, "metadata", None)


def _labels(obj: Any) -> dict[str, str]:
    return dict(getattr(_metadata(obj), "labels", None) or {})


def _ref_field(ref: Any, field: str) -> str:
    if isinstance(ref, Mapping):
        return str(ref.get(field) or "")
    return str(getattr(ref, field, "") or "")


def canonical_node_id(value: str) -> str:
    """The one canonical form of a logical node id: lowercase."""
    return value.lower()


def pod_node_id(pod: Any) -> str:
    """The logical node a session pod claims, from its label only."""
    return canonical_node_id(str(_labels(pod).get(NODE_ID_LABEL) or ""))


def _pod_terminating(pod: Any) -> bool:
    return bool(getattr(_metadata(pod), "deletion_timestamp", None))


def _pod_workloads_running(pod: Any) -> bool:
    """Every authored regular container is actually running.

    Pod phase Running only means at least one container is alive; a
    multi-container workload counts only when each declared regular container
    has state.running. Readiness probes are deliberately not consulted.
    """
    status = getattr(pod, "status", None)
    spec = getattr(pod, "spec", None)
    if not status or status.phase != "Running":
        return False
    if not spec or not spec.containers:
        return False
    statuses = status.container_statuses or []
    if len(statuses) != len(spec.containers):
        return False
    return all(entry.state and entry.state.running for entry in statuses)


@dataclass(frozen=True, slots=True)
class OwnerIdentity:
    """The ConstellationSpec a session pod must name in its ownerReferences."""

    name: str
    uid: str

    @classmethod
    def from_owner_ref(cls, owner_ref: Mapping[str, Any]) -> OwnerIdentity:
        name = str(owner_ref.get("name") or "")
        uid = str(owner_ref.get("uid") or "")
        if not name or not uid:
            raise SessionPodStateError("a session owner requires a ConstellationSpec name and uid")
        return cls(name=name, uid=uid)

    def owns(self, obj: Any) -> bool:
        """True when ``obj`` names this ConstellationSpec's name and uid as an owner."""
        return any(
            _ref_field(ref, "uid") == self.uid and _ref_field(ref, "name") == self.name
            for ref in getattr(_metadata(obj), "owner_references", None) or []
        )

    def describe(self) -> str:
        return f"{self.name}/{self.uid}"


def _describe_owners(obj: Any) -> str:
    refs = getattr(_metadata(obj), "owner_references", None) or []
    owners = [f"{_ref_field(ref, 'name')}/{_ref_field(ref, 'uid')}" for ref in refs]
    return ", ".join(owners) if owners else "no owner"


@dataclass(frozen=True, slots=True)
class SessionPodIdentity:
    """Everything that makes a session pod current for the desired session."""

    owner: OwnerIdentity
    run_label: str
    selection_identity: str
    expected_node_ids: frozenset[str]

    @classmethod
    def for_session(
        cls,
        *,
        owner_ref: Mapping[str, Any],
        session_run_id: str,
        selection_identity: str,
        node_ids: Iterable[str],
    ) -> SessionPodIdentity:
        """Build the desired identity; refuse what cannot identify pods uniquely."""
        if not session_run_id:
            raise SessionPodStateError("a session run id is required to identify session pods")
        if not selection_identity:
            raise SessionPodStateError(
                "a prepared workload selection identity is required to identify session pods"
            )
        resolved = tuple(node_ids)
        if not resolved:
            raise SessionPodStateError(
                "Session expands to 0 nodes; check constellation and ground station configs"
            )
        expected = frozenset(canonical_node_id(node_id) for node_id in resolved)
        if len(expected) != len(resolved):
            raise SessionPodStateError(
                "Expected node identity set does not match the resolved node count "
                f"({len(expected)} canonical IDs for {len(resolved)} nodes)"
            )
        return cls(
            owner=OwnerIdentity.from_owner_ref(owner_ref),
            run_label=sanitize_session_id(session_run_id),
            selection_identity=selection_identity,
            expected_node_ids=expected,
        )

    @property
    def expected_count(self) -> int:
        return len(self.expected_node_ids)

    def classify(self, pod: Any) -> PodClass:
        """The one currency rule for Ready claims and deletion decisions alike."""
        if not self.owner.owns(pod):
            return PodClass.FOREIGN
        if _pod_terminating(pod):
            return PodClass.TERMINATING
        if pod_node_id(pod) not in self.expected_node_ids:
            return PodClass.SURPLUS
        labels = _labels(pod)
        annotations = dict(getattr(_metadata(pod), "annotations", None) or {})
        if (
            labels.get(POD_SESSION_RUN_LABEL) != self.run_label
            or labels.get(POD_OWNER_UID_LABEL) != self.owner.uid
            or annotations.get(WORKLOAD_SELECTION_ANNOTATION) != self.selection_identity
        ):
            return PodClass.REPLACEABLE
        return PodClass.CURRENT


@dataclass(frozen=True, slots=True)
class ObservedPod:
    """One session pod as one observation saw it."""

    name: str
    uid: str
    node_id: str
    pod_class: PodClass
    k8s_node: str
    pod_ip: str
    workloads_running: bool
    owners: str

    @property
    def provisioned(self) -> bool:
        """Scheduled with a pod IP: its sandbox network namespace exists."""
        return bool(self.k8s_node and self.pod_ip)


def _observed(pod: Any, pod_class: PodClass) -> ObservedPod:
    metadata = _metadata(pod)
    spec = getattr(pod, "spec", None)
    status = getattr(pod, "status", None)
    return ObservedPod(
        name=str(getattr(metadata, "name", "") or ""),
        uid=str(getattr(metadata, "uid", "") or ""),
        node_id=pod_node_id(pod),
        pod_class=pod_class,
        k8s_node=str(getattr(spec, "node_name", "") or ""),
        pod_ip=str(getattr(status, "pod_ip", "") or ""),
        workloads_running=_pod_workloads_running(pod),
        owners=_describe_owners(pod),
    )


def _list_session_pods(v1: kubernetes.client.CoreV1Api, namespace: str) -> list[Any]:
    return list(v1.list_namespaced_pod(namespace, label_selector=NODE_ID_LABEL).items)


@dataclass(frozen=True, slots=True)
class SessionPodView:
    """Membership, placement and addresses derived from one observation."""

    identity: SessionPodIdentity
    pods: tuple[ObservedPod, ...]

    def __post_init__(self) -> None:
        seen: dict[str, str] = {}
        duplicates: list[str] = []
        for pod in self.of_class(PodClass.CURRENT):
            if pod.node_id in seen:
                duplicates.append(f"{pod.node_id} ({seen[pod.node_id]}, {pod.name})")
            seen[pod.node_id] = pod.name
        if duplicates:
            raise SessionPodStateError(
                "more than one current session pod for node: " + "; ".join(sorted(duplicates))
            )

    def of_class(self, *classes: PodClass) -> tuple[ObservedPod, ...]:
        return tuple(pod for pod in self.pods if pod.pod_class in classes)

    @property
    def current(self) -> tuple[ObservedPod, ...]:
        return self.of_class(PodClass.CURRENT)

    @property
    def foreign(self) -> tuple[ObservedPod, ...]:
        return self.of_class(PodClass.FOREIGN)

    @property
    def terminating(self) -> tuple[ObservedPod, ...]:
        return self.of_class(PodClass.TERMINATING)

    @property
    def deletable(self) -> tuple[ObservedPod, ...]:
        return self.of_class(*DELETABLE_CLASSES)

    @property
    def current_node_ids(self) -> frozenset[str]:
        return frozenset(pod.node_id for pod in self.current)

    @property
    def missing_node_ids(self) -> frozenset[str]:
        return self.identity.expected_node_ids - self.current_node_ids

    @property
    def provisioned_count(self) -> int:
        return sum(1 for pod in self.current if pod.provisioned)

    @property
    def running_count(self) -> int:
        return sum(1 for pod in self.current if pod.workloads_running)

    @property
    def complete(self) -> bool:
        """Exactly the expected pods are present, all current, none foreign."""
        return not self.missing_node_ids and len(self.pods) == len(self.current)

    def placement(self) -> dict[str, str]:
        """Logical node -> Kubernetes node, from current scheduled pods; refuse a gap."""
        placement = {pod.node_id: pod.k8s_node for pod in self.current if pod.k8s_node}
        missing = sorted(self.identity.expected_node_ids - set(placement))
        if missing:
            raise SessionPodStateError(
                "missing session pod placement for manifest nodes: " + ", ".join(missing[:20])
            )
        return placement

    def pod_ips(self) -> dict[str, str]:
        """Logical node -> pod IP, from current pods holding one."""
        return {pod.node_id: pod.pod_ip for pod in self.current if pod.pod_ip}

    def describe_foreign(self, limit: int = 10) -> str:
        shown = ", ".join(f"{pod.name} (owner: {pod.owners})" for pod in self.foreign[:limit])
        more = len(self.foreign) - limit
        return shown + (f", and {more} more" if more > 0 else "")


def observe_session_pods(
    v1: kubernetes.client.CoreV1Api,
    namespace: str,
    identity: SessionPodIdentity,
) -> SessionPodView:
    """One LIST of session pods, each classified exactly once."""
    return SessionPodView(
        identity=identity,
        pods=tuple(
            _observed(pod, identity.classify(pod)) for pod in _list_session_pods(v1, namespace)
        ),
    )


@dataclass(frozen=True, slots=True)
class PodDeletion:
    """One DELETE request against one observed pod UID, and what it established.

    ``reason`` is the HTTP reason phrase, the only part reported in CR status.
    ``detail`` is the API server's own explanation from the response body, kept
    for server-side diagnosis.
    """

    pod_name: str
    pod_uid: str
    outcome: DeletionOutcome
    reason: str = ""
    detail: str = ""


def _api_failure_detail(error: kubernetes.client.rest.ApiException) -> str:
    """The API server's explanation: the Status body's message, else the raw body."""
    body = error.body
    if isinstance(body, bytes):
        body = body.decode("utf-8", errors="replace")
    if not body:
        return ""
    try:
        document = json.loads(body)
    except ValueError:
        return str(body)
    if isinstance(document, Mapping) and isinstance(document.get("message"), str):
        return document["message"]
    return str(body)


def _delete_observed(
    v1: kubernetes.client.CoreV1Api,
    namespace: str,
    pod_name: str,
    pod_uid: str,
) -> PodDeletion:
    """Delete exactly the observed pod UID; report 404 and 409 as what they are."""
    if not pod_name or not pod_uid:
        raise SessionPodStateError("cannot delete a session pod without its name and observed uid")
    try:
        v1.delete_namespaced_pod(
            pod_name,
            namespace,
            body=kubernetes.client.V1DeleteOptions(
                preconditions=kubernetes.client.V1Preconditions(uid=pod_uid)
            ),
        )
    except kubernetes.client.rest.ApiException as error:
        if error.status == 404:
            log.info("Session pod %s/%s uid=%s is already absent", namespace, pod_name, pod_uid)
            return PodDeletion(pod_name, pod_uid, DeletionOutcome.ABSENT)
        if error.status == 409:
            reason = str(error.reason or "")
            detail = _api_failure_detail(error)
            log.warning(
                "Delete of session pod %s/%s uid=%s conflicted (HTTP 409 %s: %s); "
                "reobserving before another decision",
                namespace,
                pod_name,
                pod_uid,
                reason,
                detail,
            )
            return PodDeletion(pod_name, pod_uid, DeletionOutcome.CONFLICT, reason, detail)
        error.add_note(f"delete session pod {namespace}/{pod_name} uid={pod_uid}")
        raise
    except Exception as error:
        error.add_note(f"delete session pod {namespace}/{pod_name} uid={pod_uid}")
        raise
    log.info("Deletion of session pod %s/%s uid=%s accepted", namespace, pod_name, pod_uid)
    return PodDeletion(pod_name, pod_uid, DeletionOutcome.ACCEPTED)


def delete_ineligible_pods(
    v1: kubernetes.client.CoreV1Api,
    namespace: str,
    view: SessionPodView,
) -> tuple[PodDeletion, ...]:
    """Request deletion of the view's replaceable and surplus pods, fenced by UID.

    Foreign, terminating and current pods are never touched. The first 409
    ends the pass: the next decision needs a new observation.
    """
    deletions: list[PodDeletion] = []
    for pod in view.deletable:
        if pod.pod_class not in DELETABLE_CLASSES:
            raise SessionPodStateError(f"session pod {pod.name!r} is not eligible for deletion")
        deletion = _delete_observed(v1, namespace, pod.name, pod.uid)
        deletions.append(deletion)
        if deletion.outcome is DeletionOutcome.CONFLICT:
            break
    return tuple(deletions)


def delete_conflicting_pod(
    v1: kubernetes.client.CoreV1Api,
    namespace: str,
    pod: Any,
    identity: SessionPodIdentity,
) -> PodDeletion:
    """Delete one read-back pod that blocks a create, if the shared rule allows it.

    A foreign or terminating pod is refused; a current pod needs no deletion.
    """
    pod_class = identity.classify(pod)
    observed = _observed(pod, pod_class)
    if pod_class is PodClass.FOREIGN:
        raise SessionPodStateError(
            f"Pod {observed.name} already exists but is not owned by the current "
            f"ConstellationSpec (owner: {observed.owners})"
        )
    if pod_class is PodClass.TERMINATING:
        raise SessionPodStateError(f"Pod {observed.name} already exists and is deleting")
    if pod_class not in DELETABLE_CLASSES:
        raise SessionPodStateError(f"session pod {observed.name!r} is current; nothing to delete")
    return _delete_observed(v1, namespace, observed.name, observed.uid)


@dataclass(frozen=True, slots=True)
class OwnedSessionPods:
    """One observation scoped only by ownership, for paths without a prepared selection."""

    owner: OwnerIdentity
    owned: tuple[ObservedPod, ...]
    foreign: tuple[ObservedPod, ...]


def observe_owned_session_pods(
    v1: kubernetes.client.CoreV1Api,
    namespace: str,
    owner: OwnerIdentity,
) -> OwnedSessionPods:
    """One LIST, split into pods this ConstellationSpec owns and pods it does not."""
    owned: list[ObservedPod] = []
    foreign: list[ObservedPod] = []
    for pod in _list_session_pods(v1, namespace):
        if owner.owns(pod):
            pod_class = PodClass.TERMINATING if _pod_terminating(pod) else PodClass.SURPLUS
            owned.append(_observed(pod, pod_class))
        else:
            foreign.append(_observed(pod, PodClass.FOREIGN))
    return OwnedSessionPods(owner=owner, owned=tuple(owned), foreign=tuple(foreign))


def delete_all_owned_pods(
    v1: kubernetes.client.CoreV1Api,
    namespace: str,
    owner: OwnerIdentity,
) -> tuple[int, tuple[PodDeletion, ...]]:
    """Drive this ConstellationSpec's session pods toward zero; report what remains.

    ``remaining`` counts every owned pod observed, terminating ones included,
    so a caller publishes a terminal phase only once zero is observed. Pods
    of other owners are untouched. The first 409 ends the pass.
    """
    observation = observe_owned_session_pods(v1, namespace, owner)
    deletions: list[PodDeletion] = []
    for pod in observation.owned:
        if pod.pod_class is PodClass.TERMINATING:
            continue
        deletion = _delete_observed(v1, namespace, pod.name, pod.uid)
        deletions.append(deletion)
        if deletion.outcome is DeletionOutcome.CONFLICT:
            break
    return len(observation.owned), tuple(deletions)


_OWNED_RUN_ID_CONFIGMAPS: tuple[tuple[str, str], ...] = (
    ("nodalarc-session", SESSION_RUN_ID_FILENAME),
    ("nodalarc-topology-wiring", "session_id"),
)


def owned_session_run_ids(
    v1: kubernetes.client.CoreV1Api,
    namespace: str,
    owner: OwnerIdentity,
) -> tuple[str, ...]:
    """The distinct run ids of the session resources one ConstellationSpec owns.

    The identity is read from the records that carry it, never from the CR's
    status or desired generation: every session pod, terminating pods included
    (after a generation change the superseded run id can survive only on one),
    and the two session ConfigMaps that name a run id, each accepted only when
    its owner reference names the CR. A session object with another owner, or
    an owned record without its run id, is a refusal: deletion must not proceed
    on an identity it cannot prove. An empty result means nothing is deployed
    under this CR. Every value passes through ``sanitize_session_id`` because
    the pod label and the wiring key are sanitized at write and the session key
    is raw.
    """
    run_ids: set[str] = set()
    for pod in _list_session_pods(v1, namespace):
        pod_name = str(getattr(_metadata(pod), "name", "") or "")
        if not owner.owns(pod):
            raise ValueError(
                f"session pod {pod_name!r} is not owned by ConstellationSpec {owner.uid!r}"
            )
        run_id = str(_labels(pod).get(POD_SESSION_RUN_LABEL) or "").strip()
        if not run_id:
            raise ValueError(
                f"owned session pod {pod_name!r} carries no {POD_SESSION_RUN_LABEL} label"
            )
        run_ids.add(sanitize_session_id(run_id))
    for cm_name, key in _OWNED_RUN_ID_CONFIGMAPS:
        try:
            cm = v1.read_namespaced_config_map(cm_name, namespace)
        except kubernetes.client.rest.ApiException as exc:
            if exc.status == 404:
                continue
            raise
        if not owner.owns(cm):
            raise ValueError(
                f"ConfigMap {cm_name!r} is not owned by ConstellationSpec {owner.uid!r}"
            )
        value = str((cm.data or {}).get(key) or "").strip()
        if not value:
            raise ValueError(f"owned ConfigMap {cm_name!r} carries no {key!r}")
        run_ids.add(sanitize_session_id(value))
    return tuple(sorted(run_ids))
