# Copyright 2024-2026 .chance (dotchance)
# SPDX-License-Identifier: AGPL-3.0-only
# Additional terms under AGPL-3.0 Section 7 are in the NOTICE file.
"""The chart's own lifecycle checks, run from the Operator image by Helm.

``verify`` (helm test): every platform Deployment is Available, the Node Agent
DaemonSet wants at least one pod and has all of them ready, and every container
whose image is pinned by digest runs exactly that digest.

``uninstall`` (the pre-delete hook): while the Operator and the Node Agents
still run, delete every ConstellationSpec so the Operator unwinds the session,
wait until no session pod remains, then run the Node Agent's cleaner on every
server, the same cleaner ``make teardown`` runs, and require each server to
report itself clean. Helm removes the platform only after this succeeds, so an
uninstall never leaves NodalArc's kernel state on a server unreported.

Each command prints what it found and exits 0 only when everything holds.
"""

from __future__ import annotations

import sys
import time

import kubernetes.client
import kubernetes.config
from kubernetes.stream import stream

from nodalarc.cr_runtime_config import CR_GROUP, CR_PLURAL, CR_VERSION

NODE_AGENT_DAEMONSET = "nodalarc-node-agent"
NODE_AGENT_POD_LABEL = "app=nodalarc-node-agent"
NODE_AGENT_CONTAINER = "node-agent"
SESSION_POD_LABEL = "nodalarc.io/node-id"
# The helm test pod (templates/platform-lifecycle.yaml). Helm never deletes a
# hook pod on uninstall, so the uninstall hook does.
TEST_POD = "nodalarc-verify"
CLEANER = ["python", "-m", "node_agent.reconcile", "--clean"]
SESSION_UNWIND_TIMEOUT_S = 600.0
POLL_S = 2.0


def _namespace() -> str:
    with open("/var/run/secrets/kubernetes.io/serviceaccount/namespace") as handle:
        return handle.read().strip()


def _digest(image: str) -> str | None:
    return image.rsplit("@", 1)[1] if "@sha256:" in image else None


def verify(namespace: str) -> list[str]:
    """Every problem found; empty when the platform runs as the chart says."""
    apps = kubernetes.client.AppsV1Api()
    core = kubernetes.client.CoreV1Api()
    problems: list[str] = []

    deployments = apps.list_namespaced_deployment(namespace).items
    if not deployments:
        problems.append("no Deployment exists in the namespace")
    for deployment in deployments:
        wanted = deployment.spec.replicas or 0
        available = deployment.status.available_replicas or 0
        print(f"deployment {deployment.metadata.name}: {available}/{wanted} available")
        if available < wanted:
            problems.append(
                f"deployment {deployment.metadata.name}: {available}/{wanted} available"
            )

    daemonset = apps.read_namespaced_daemon_set(NODE_AGENT_DAEMONSET, namespace)
    desired = daemonset.status.desired_number_scheduled or 0
    ready = daemonset.status.number_ready or 0
    print(f"daemonset {NODE_AGENT_DAEMONSET}: {ready}/{desired} ready")
    if desired == 0:
        problems.append(
            f"daemonset {NODE_AGENT_DAEMONSET} wants 0 pods: no server matches the placement values"
        )
    elif ready < desired:
        problems.append(f"daemonset {NODE_AGENT_DAEMONSET}: {ready}/{desired} ready")

    for pod in core.list_namespaced_pod(namespace).items:
        if pod.metadata.labels and SESSION_POD_LABEL in pod.metadata.labels:
            continue  # session pods belong to the session, not the platform
        statuses = {status.name: status for status in pod.status.container_statuses or []}
        for container in pod.spec.containers:
            pinned = _digest(container.image)
            if pinned is None:
                continue
            status = statuses.get(container.name)
            running = _digest(status.image_id) if status and status.image_id else None
            if running != pinned:
                problems.append(
                    f"pod {pod.metadata.name} container {container.name} runs {running}, "
                    f"its image names {pinned}"
                )
            else:
                print(f"pod {pod.metadata.name} container {container.name}: runs {pinned}")
    return problems


def _wait(description: str, done) -> None:
    deadline = time.monotonic() + SESSION_UNWIND_TIMEOUT_S
    while not done():
        if time.monotonic() > deadline:
            raise TimeoutError(
                f"{description} did not happen within {SESSION_UNWIND_TIMEOUT_S:.0f} s"
            )
        time.sleep(POLL_S)
    print(description)


def uninstall(namespace: str) -> list[str]:
    """Unwind the session and prove every server clean; every problem found."""
    custom = kubernetes.client.CustomObjectsApi()
    core = kubernetes.client.CoreV1Api()

    def specs() -> list[str]:
        listed = custom.list_namespaced_custom_object(CR_GROUP, CR_VERSION, namespace, CR_PLURAL)
        return [item["metadata"]["name"] for item in listed["items"]]

    for name in specs():
        print(f"deleting ConstellationSpec {name}; the Operator unwinds its session")
        custom.delete_namespaced_custom_object(CR_GROUP, CR_VERSION, namespace, CR_PLURAL, name)
    try:
        _wait("every ConstellationSpec is gone", lambda: not specs())
        _wait(
            "no session pod remains",
            lambda: not core.list_namespaced_pod(namespace, label_selector=SESSION_POD_LABEL).items,
        )
    except TimeoutError as error:
        return [str(error)]

    problems: list[str] = []
    agents = core.list_namespaced_pod(namespace, label_selector=NODE_AGENT_POD_LABEL).items
    if not agents:
        problems.append("no Node Agent pod exists: no server's kernel state can be verified")
    for pod in agents:
        server = pod.spec.node_name
        client = stream(
            core.connect_get_namespaced_pod_exec,
            pod.metadata.name,
            namespace,
            container=NODE_AGENT_CONTAINER,
            command=CLEANER,
            stderr=True,
            stdin=False,
            stdout=True,
            tty=False,
            _preload_content=False,
        )
        client.run_forever(timeout=120)
        report = client.read_stdout().strip()
        errors = client.read_stderr().strip()
        code = client.returncode
        client.close()
        print(f"server {server}: {report}")
        if code != 0:
            problems.append(
                f"server {server} is not clean (cleaner exit {code}): {report} {errors}"
            )

    try:
        core.delete_namespaced_pod(TEST_POD, namespace)
        print(f"deleted the helm test pod {TEST_POD}")
    except kubernetes.client.rest.ApiException as error:
        if error.status != 404:
            problems.append(f"could not delete the helm test pod {TEST_POD}: {error.reason}")
    return problems


def main(argv: list[str]) -> int:
    commands = {"verify": verify, "uninstall": uninstall}
    if len(argv) != 1 or argv[0] not in commands:
        print("usage: python -m nodalarc.platform_lifecycle verify|uninstall", file=sys.stderr)
        return 2
    kubernetes.config.load_incluster_config()
    problems = commands[argv[0]](_namespace())
    for problem in problems:
        print(f"FAILED: {problem}", file=sys.stderr)
    print("OK" if not problems else f"{len(problems)} problem(s)")
    return 0 if not problems else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
