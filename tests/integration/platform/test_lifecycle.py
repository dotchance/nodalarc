"""A platform operator installs NodalArc, starts a session, restarts it and tears it down.

Each step runs the make target a platform operator runs. It then checks what make reported
against the cluster and the servers themselves (evidence.py) and against the emulated network
(the operator truths). The steps run in file order; a step is skipped when the one it builds on
did not pass.

This run destroys the platform on the cluster and installs it again when it ends. It is selected
with -m platform.
"""

from __future__ import annotations

import re
import subprocess
import time

import httpx
import pytest

from ..operator import truths
from ..operator.harness.client import Operator, Refused, vs_api_base_url
from . import evidence

pytestmark = [pytest.mark.integration, pytest.mark.platform, pytest.mark.timeout(7200)]

_passed: set[str] = set()
# What each server held with no NodalArc on the cluster, read before the install.
_servers_before_install: dict[str, set[str]] = {}


def _builds_on(step: str) -> None:
    if step not in _passed:
        pytest.skip(f"the {step} step did not pass")


def _make(*targets: str, timeout: float) -> str:
    """Run make the way a platform operator does. Returns everything it printed."""
    done = subprocess.run(
        ["make", *targets],
        cwd=evidence.PROJECT_ROOT,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    printed = done.stdout + done.stderr
    assert done.returncode == 0, (
        f"make {' '.join(targets)} exited {done.returncode}:\n{printed[-4000:]}"
    )
    return printed


@pytest.fixture(scope="module", autouse=True)
def cluster_left_running():
    """A run that tore the platform down at its end installs it and starts a session again."""
    yield
    if "install" in _passed and not evidence.nodalarc_objects():
        _make("install", timeout=900)
        _make("session", timeout=900)


@pytest.fixture(autouse=True)
def vs_api_is_found_on_the_cluster(monkeypatch: pytest.MonkeyPatch) -> None:
    """An install may place VS-API on another server than the one a fixed address names."""
    monkeypatch.delenv("VS_API_HOST", raising=False)


def _operator_once_the_session_is_ready(timeout: float) -> Operator:
    """A fresh operator, once NodalArc shows the session ready and its state current."""
    deadline = time.monotonic() + timeout
    while True:
        try:
            operator = Operator(vs_api_base_url())
            state = operator.state()
            if state["session_status"] == "ready" and not state["stale"]:
                return operator
            shown = (
                f"{state['session_status']!r} stale={state['stale']} "
                f"{state['session_status_detail']!r}"
            )
        except (Refused, httpx.HTTPError, RuntimeError) as error:
            shown = repr(error)
        assert time.monotonic() < deadline, (
            f"NodalArc did not show a ready session within {timeout:.0f} s; last shown: {shown}"
        )
        time.sleep(5.0)


def _uids(pods: list[dict]) -> dict[str, str]:
    return {pod["metadata"]["uid"]: pod["metadata"]["name"] for pod in pods}


def _container_restarts(pods: list[dict]) -> dict[str, int]:
    return {
        f"{pod['metadata']['name']}/{status['name']}": status["restartCount"]
        for pod in pods
        for status in pod["status"].get("containerStatuses", [])
    }


def test_the_run_starts_from_a_cluster_without_nodalarc() -> None:
    printed = _make("teardown", timeout=900)
    assert "Teardown complete" in printed, printed[-2000:]
    left = evidence.nodalarc_objects()
    assert not left, f"teardown reported complete and the cluster still holds {left}"
    for server, address in evidence.servers().items():
        _servers_before_install[server] = evidence.server_network(address)
    _passed.add("teardown")


def test_install_runs_this_tree_on_every_server_with_no_session() -> None:
    _builds_on("teardown")
    _make("build", timeout=3600)
    _make("load", timeout=1800)
    printed = _make("install", timeout=900)
    assert "Platform ready" in printed, printed[-2000:]

    pods = evidence.platform_pods()
    assert pods, "install reported a ready platform and the namespace holds no platform pod"
    assert not evidence.not_ready(pods), evidence.not_ready(pods)
    commit = evidence.head_commit()
    other_builds = {
        container: image
        for container, image in evidence.nodalarc_images(pods).items()
        if not image.rsplit(":", 1)[-1].startswith(commit)
    }
    assert not other_builds, f"pods that do not run commit {commit}: {other_builds}"
    servers_with_an_agent = {
        pod["spec"]["nodeName"]
        for pod in pods
        if pod["metadata"]["name"].startswith("nodalarc-node-agent-")
    }
    assert servers_with_an_agent == set(evidence.servers()), servers_with_an_agent

    status = _make("status", timeout=300)
    drift_table = status[status.index("Image drift") :]
    assert "DRIFT" not in drift_table and "ABSENT" not in drift_table, drift_table

    assert not evidence.session_pods(), "a fresh install holds session pods"
    with pytest.raises(Refused) as refused:
        Operator(vs_api_base_url()).state()
    assert refused.value.status == 503 and refused.value.body["code"] == "session.inactive", (
        f"a fresh install with no session answered: {refused.value}"
    )
    _passed.add("install")


def test_a_session_started_with_make_is_the_session_that_runs() -> None:
    _builds_on("install")
    printed = _make("session", timeout=900)
    ready = re.search(r"Session ready\. (\d+)/(\d+) session pods running", printed)
    assert ready, printed[-2000:]
    pods = evidence.session_pods()
    # The later steps need a started session. Whether make's report holds is this step's verdict.
    _passed.add("session")

    assert len(pods) == int(ready.group(2)), f"make counted {ready.group(2)}; found {len(pods)}"
    assert not evidence.not_ready(pods), (
        f"make reported the session ready while these pods were not: {evidence.not_ready(pods)}"
    )
    operator = Operator(vs_api_base_url())
    state = operator.state()
    assert state["session_status"] == "ready", state["session_status_detail"]
    on_servers = {
        server: evidence.server_network(address) - _servers_before_install[server]
        for server, address in evidence.servers().items()
    }
    assert any(on_servers.values()), "a session runs and no server holds a link for it"
    truths.assert_session_is_truthful(operator)


def test_a_restart_replaces_the_platform_pods_and_the_session_stays_truthful() -> None:
    _builds_on("session")
    platform_before = _uids(evidence.platform_pods())
    session_before = evidence.session_pods()

    printed = _make("restart", timeout=900)
    assert "[restart] Done." in printed, printed[-2000:]
    restarted = re.findall(r"Restarted (?:deployment|daemonset)/(\S+)", printed)
    assert restarted, printed[-2000:]

    platform_after = evidence.platform_pods()
    assert not evidence.not_ready(platform_after), evidence.not_ready(platform_after)
    kept = [
        pod["metadata"]["name"]
        for pod in platform_after
        if pod["metadata"]["uid"] in platform_before
        and any(pod["metadata"]["name"].startswith(f"{workload}-") for workload in restarted)
    ]
    assert not kept, f"make reported these restarted and the same pods still run: {kept}"

    session_after = evidence.session_pods()
    assert _uids(session_after) == _uids(session_before), "a platform restart replaced session pods"
    assert _container_restarts(session_after) == _container_restarts(session_before), (
        "a platform restart restarted containers of the session"
    )
    truths.assert_session_is_truthful(_operator_once_the_session_is_ready(300.0))


def test_teardown_returns_the_cluster_and_every_server_to_what_they_held_before() -> None:
    _builds_on("session")
    printed = _make("teardown", timeout=900)
    assert "Teardown complete. Cluster is clean." in printed, printed[-2000:]

    differences = [f"cluster: {name}" for name in evidence.nodalarc_objects()]
    for server, address in evidence.servers().items():
        now = evidence.server_network(address)
        before = _servers_before_install[server]
        differences += [f"{server}: left behind: {entry}" for entry in sorted(now - before)]
        differences += [f"{server}: removed: {entry}" for entry in sorted(before - now)]
    assert not differences, "teardown reported a clean cluster:\n" + "\n".join(differences)
