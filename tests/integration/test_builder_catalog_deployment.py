"""Live qualification for Builder-authored ``user:`` catalog deployment.

This test intentionally changes the active NodalArc session.  It is excluded
from the ordinary integration suite unless ``NODALARC_RUN_BUILDER_E2E=1`` is
set, and it requires the expected release/build identities of the deployment
under test.  ``make test-builder-e2e`` supplies those identities from the
current checkout by default.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import time
import uuid
from collections.abc import Iterator, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
import requests
from nodalarc.catalog_upload import CatalogUploadSelection, verify_catalog_upload
from nodalarc.configuration_yaml import load_configuration_yaml
from nodalarc.kubernetes_runtime_config import CATALOG_UPLOAD_LABEL, read_catalog_upload

pytestmark = [
    pytest.mark.integration,
    pytest.mark.timeout(1_200),
    pytest.mark.skipif(
        os.environ.get("NODALARC_RUN_BUILDER_E2E") != "1",
        reason="destructive live Builder E2E requires NODALARC_RUN_BUILDER_E2E=1",
    ),
]

SOURCE_SESSION_REF = "nodalarc:sessions/earth-leo-simple.yaml"
SOURCE_SEGMENT_ID = "leo"
SOURCE_TERMINAL_REF = "nodalarc:terminals/rf/rf-ka-starlink-space-gateway.yaml"
TERMINAL_STATES = frozenset({"succeeded", "failed", "cancelled"})


def _kubectl(*arguments: str, timeout: int = 30) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["kubectl", *arguments],
        capture_output=True,
        check=False,
        text=True,
        timeout=timeout,
    )


def _kubectl_json(*arguments: str, timeout: int = 30) -> dict[str, Any]:
    result = _kubectl(*arguments, timeout=timeout)
    if result.returncode != 0:
        raise AssertionError(
            f"kubectl {' '.join(arguments)} failed ({result.returncode}): {result.stderr.strip()}"
        )
    value = json.loads(result.stdout)
    if not isinstance(value, dict):
        raise AssertionError("kubectl JSON response was not an object")
    return value


def _unused_loopback_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


class _VSAPIEndpoint:
    """Stable request facade over an external URL or restartable port-forward."""

    def __init__(self, namespace: str) -> None:
        self.namespace = namespace
        self._configured_url = os.environ.get("VS_API_BASE_URL", "").rstrip("/")
        self._port = _unused_loopback_port()
        self._forward: subprocess.Popen[str] | None = None

    @property
    def base_url(self) -> str:
        return self._configured_url or f"http://127.0.0.1:{self._port}"

    def close(self) -> None:
        if self._forward is None:
            return
        self._forward.terminate()
        try:
            self._forward.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self._forward.kill()
            self._forward.wait(timeout=5)
        self._forward = None

    def _start_forward(self) -> None:
        if self._configured_url:
            return
        self.close()
        self._forward = subprocess.Popen(
            [
                "kubectl",
                "port-forward",
                "-n",
                self.namespace,
                "service/nodalarc-vs-api",
                f"{self._port}:8080",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )

    def ensure_ready(self, *, timeout: float = 45) -> None:
        deadline = time.monotonic() + timeout
        last_error = "VS-API did not answer"
        while time.monotonic() < deadline:
            if not self._configured_url and (
                self._forward is None or self._forward.poll() is not None
            ):
                self._start_forward()
            try:
                response = requests.get(f"{self.base_url}/api/v1/health", timeout=2)
                if response.status_code == 200:
                    return
                last_error = f"health returned HTTP {response.status_code}"
            except requests.RequestException as error:
                last_error = str(error)
            time.sleep(1)
        output = ""
        if self._forward is not None and self._forward.poll() is not None:
            output = (
                self._forward.stdout.read() if self._forward.stdout is not None else ""
            ).strip()
        raise AssertionError(f"VS-API unavailable: {last_error}; port-forward={output}")

    def token(self) -> str:
        self.ensure_ready()
        response = requests.get(f"{self.base_url}/api/v1/auth/token", timeout=10)
        response.raise_for_status()
        return str(response.json()["token"])

    def request_json(
        self,
        method: str,
        path: str,
        *,
        expected_status: int = 200,
        payload: Mapping[str, Any] | None = None,
        token: str | None = None,
        retries: int = 1,
    ) -> dict[str, Any]:
        last_error: BaseException | None = None
        for attempt in range(retries):
            try:
                self.ensure_ready()
                selected_token = self.token() if token is None else token
                headers = {"Authorization": f"Bearer {selected_token}"} if selected_token else {}
                response = requests.request(
                    method,
                    f"{self.base_url}{path}",
                    headers=headers,
                    json=payload,
                    timeout=30,
                )
                if response.status_code != expected_status:
                    raise AssertionError(
                        f"{method} {path} returned {response.status_code}, expected "
                        f"{expected_status}: {response.text[:1_000]}"
                    )
                value = response.json()
                if not isinstance(value, dict):
                    raise AssertionError(f"{method} {path} did not return a JSON object")
                return value
            except (requests.RequestException, AssertionError) as error:
                last_error = error
                if attempt + 1 == retries:
                    raise
                time.sleep(2)
        raise AssertionError(f"request failed: {last_error}")


@pytest.fixture(scope="module")
def vs_api(k3s_available: None) -> Iterator[_VSAPIEndpoint]:
    del k3s_available
    endpoint = _VSAPIEndpoint(os.environ.get("NAMESPACE", "nodalarc"))
    endpoint.ensure_ready()
    try:
        yield endpoint
    finally:
        endpoint.close()


def _wait_for_transition(
    endpoint: _VSAPIEndpoint,
    operation_id: str,
    *,
    timeout: float = 900,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    last: dict[str, Any] | None = None
    while time.monotonic() < deadline:
        last = endpoint.request_json(
            "GET",
            f"/api/v1/session-transitions/{operation_id}",
            retries=6,
        )
        if last.get("state") in TERMINAL_STATES:
            return last
        time.sleep(3)
    raise AssertionError(f"transition {operation_id} did not finish: {last}")


class _RemoteConfigMapReader:
    def __init__(self, response: Mapping[str, Any]) -> None:
        self._response = response

    def list_namespaced_config_map(self, namespace: str, *, label_selector: str) -> Any:
        del namespace, label_selector
        return self._response


def _verify_remote_upload(
    cr: Mapping[str, Any],
    *,
    namespace: str,
) -> tuple[CatalogUploadSelection, dict[str, bytes]]:
    spec = cr.get("spec") or {}
    root_yaml = str(spec.get("sessionYaml") or "").encode()
    selection = CatalogUploadSelection.model_validate(spec.get("catalogUpload"), strict=True)
    inventory = _kubectl_json(
        "get",
        "configmaps",
        "-n",
        namespace,
        "-l",
        f"{CATALOG_UPLOAD_LABEL}={selection.upload_id}",
        "-o",
        "json",
    )
    verified = verify_catalog_upload(
        read_catalog_upload(
            _RemoteConfigMapReader(inventory),
            namespace=namespace,
            root_yaml=root_yaml,
            selection=selection,
        )
    )
    return selection, {str(entry.ref): entry.yaml_bytes for entry in verified.catalog_files}


def _runtime_service_proof(
    app: str,
    *,
    namespace: str,
    timeout: float = 120,
) -> tuple[dict[str, Any], dict[str, Any]]:
    deadline = time.monotonic() + timeout
    last_error = "runtime pod not found"
    while time.monotonic() < deadline:
        pods = _kubectl_json("get", "pods", "-n", namespace, "-l", f"app={app}", "-o", "json")
        candidates = [
            pod
            for pod in pods.get("items", [])
            if not (pod.get("metadata") or {}).get("deletionTimestamp")
            and (pod.get("status") or {}).get("phase") == "Running"
        ]
        for pod in candidates:
            metadata = pod.get("metadata") or {}
            name = str(metadata.get("name") or "")
            result = _kubectl(
                "get",
                "--raw",
                f"/api/v1/namespaces/{namespace}/pods/{name}:8081/proxy/readyz",
            )
            if result.returncode != 0:
                last_error = result.stderr.strip()
                continue
            payload = json.loads(result.stdout)
            if payload.get("status") == "ready" and isinstance(payload.get("proof"), dict):
                return pod, payload["proof"]
            last_error = result.stdout.strip()
        time.sleep(2)
    raise AssertionError(f"{app} did not publish a proof-gated ready response: {last_error}")


def _node_type(node: Mapping[str, Any]) -> str:
    return str(node.get("node_type") or node.get("kind") or "")


def _active_ground_satellite_pair(
    state: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]] | None:
    nodes = {
        str(node.get("node_id")): node
        for node in state.get("nodes", [])
        if isinstance(node, dict) and node.get("node_id")
    }
    raw_links = state.get("links", [])
    links = list(raw_links.values()) if isinstance(raw_links, dict) else raw_links
    for link in links:
        if not isinstance(link, dict) or link.get("state") != "active":
            continue
        a = nodes.get(str(link.get("node_a")))
        b = nodes.get(str(link.get("node_b")))
        if a is None or b is None:
            continue
        if _node_type(a) == "ground_station" and _node_type(b) == "satellite":
            return a, b
        if _node_type(b) == "ground_station" and _node_type(a) == "satellite":
            return b, a
    return None


def _router_loopback(node: Mapping[str, Any]) -> str | None:
    for address in node.get("addresses", []):
        if not isinstance(address, dict):
            continue
        if address.get("purpose") == "router_loopback" and address.get("family") == "ipv4":
            return str(address.get("address") or "").split("/", 1)[0] or None
    return None


def _wait_for_runtime_smoke(
    endpoint: _VSAPIEndpoint,
    *,
    namespace: str,
    timeout: float = 240,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    first_sim_time: str | None = None
    last_detail = "no state observed"
    while time.monotonic() < deadline:
        state = endpoint.request_json("GET", "/api/v1/state", retries=6)
        sim_time = str(state.get("sim_time") or "")
        first_sim_time = first_sim_time or sim_time
        pair = _active_ground_satellite_pair(state)
        if pair is None or not first_sim_time or sim_time == first_sim_time:
            last_detail = "waiting for advancing OME state and an active ground-space link"
            time.sleep(3)
            continue
        ground, satellite = pair
        satellite_loopback = _router_loopback(satellite)
        if not satellite_loopback:
            last_detail = "active satellite has no published router loopback"
            time.sleep(3)
            continue
        satellite_id = str(satellite["node_id"])
        introspection = endpoint.request_json(
            "POST",
            "/api/v1/introspect",
            payload={"node_id": satellite_id, "command": "show isis neighbor"},
            retries=3,
        )
        if "Up" not in str(introspection.get("output") or ""):
            last_detail = "IS-IS adjacency has not converged"
            time.sleep(5)
            continue
        ground_id = str(ground["node_id"])
        pods = _kubectl_json(
            "get",
            "pods",
            "-n",
            namespace,
            "-l",
            f"nodalarc.io/node-id={ground_id}",
            "-o",
            "json",
        )
        items = pods.get("items", [])
        if len(items) != 1:
            last_detail = f"expected one pod for ground node {ground_id}, found {len(items)}"
            time.sleep(3)
            continue
        pod_name = str(items[0]["metadata"]["name"])
        ping = _kubectl(
            "exec",
            "-n",
            namespace,
            pod_name,
            "-c",
            "frr",
            "--",
            "ping",
            "-c",
            "3",
            "-W",
            "5",
            satellite_loopback,
            timeout=30,
        )
        if ping.returncode == 0 and (
            "bytes from" in ping.stdout or "0% packet loss" in ping.stdout
        ):
            return {
                "first_sim_time": first_sim_time,
                "last_sim_time": sim_time,
                "ground_node": ground_id,
                "satellite_node": satellite_id,
                "satellite_loopback": satellite_loopback,
                "ping_summary": next(
                    (line for line in ping.stdout.splitlines() if "packets transmitted" in line),
                    "ping succeeded",
                ),
            }
        last_detail = f"ground-to-satellite ping not converged: {ping.stdout[-300:]}"
        time.sleep(5)
    raise AssertionError(f"runtime smoke did not converge: {last_detail}")


def _assert_proof(
    proof: Mapping[str, Any],
    *,
    origin: str,
    pod: Mapping[str, Any],
    cr: Mapping[str, Any],
    selection: CatalogUploadSelection,
    status: Mapping[str, Any],
) -> None:
    metadata = cr["metadata"]
    assert proof["schema_name"] == "nodalarc.runtime-config-proof.v3"
    assert proof["source_origin"] == origin
    assert proof["upload_id"] == selection.upload_id
    assert proof["document_digest"] == status["documentDigest"]
    assert proof["closure_digest"] == selection.closure_digest
    assert proof["resolved_semantic_digest"] == status["resolvedSemanticDigest"]
    assert proof["run_id"] == status["sessionRunId"]
    assert proof["cr_uid"] == metadata["uid"]
    assert proof["cr_generation"] == metadata["generation"]
    assert proof["pod_uid"] == pod["metadata"]["uid"]
    assert proof["release"] == status["runtimeRelease"]
    assert proof["build"] == status["runtimeBuild"]


def test_builder_user_component_closure_reaches_verified_runtime(
    vs_api: _VSAPIEndpoint,
) -> None:
    namespace = os.environ.get("NAMESPACE", "nodalarc")
    expected_release = os.environ.get("NODALARC_EXPECTED_RUNTIME_RELEASE")
    expected_build = os.environ.get("NODALARC_EXPECTED_RUNTIME_BUILD")
    if not expected_release or not expected_build:
        pytest.fail(
            "live qualification requires NODALARC_EXPECTED_RUNTIME_RELEASE and "
            "NODALARC_EXPECTED_RUNTIME_BUILD"
        )

    suffix = uuid.uuid4().hex[:12]
    session_ref = f"user:sessions/e2e-builder-{suffix}.yaml"
    terminal_ref = f"user:terminals/e2e-builder-{suffix}.yaml"
    evidence: dict[str, Any] = {
        "started_at": datetime.now(UTC).isoformat(),
        "source_session_ref": SOURCE_SESSION_REF,
        "session_ref": session_ref,
        "terminal_ref": terminal_ref,
        "expected_runtime_release": expected_release,
        "expected_runtime_build": expected_build,
    }
    error: BaseException | None = None
    try:
        bootstrap = vs_api.request_json("GET", "/api/v1/builder/bootstrap")
        assert bootstrap["capabilities"] == {
            "user_catalog_write": True,
            "deploy_yaml_closure": True,
        }

        opened = vs_api.request_json(
            "POST",
            "/api/v1/builder/draft/open",
            payload={"source_ref": SOURCE_SESSION_REF, "target_ref": session_ref},
        )
        assert opened["projection_status"] == "applied"
        assert opened["applied_revision"] == opened["draft_revision"]
        assert opened["authoring_workspace"] == opened["applied_workspace"]
        assert opened["target_ref"] == session_ref

        customized = vs_api.request_json(
            "POST",
            "/api/v1/builder/draft/customize-chain",
            payload={
                "draft": opened,
                "segment_id": SOURCE_SEGMENT_ID,
                "leaf_ref": SOURCE_TERMINAL_REF,
                "target_leaf_ref": terminal_ref,
            },
        )
        assert customized["applied"] is True, customized.get("issues")
        forked_refs = [entry["target_ref"] for entry in customized["forked_chain"]]
        assert terminal_ref in forked_refs

        initial_compiled = vs_api.request_json(
            "POST",
            "/api/v1/builder/draft/compile",
            payload={"draft": customized["draft"]},
        )
        initial_compile_result = initial_compiled["compile_result"]
        assert initial_compile_result["save_verdict"]["allowed"] is True
        assert initial_compile_result["deploy_eligibility_after_save"]["allowed"] is True
        initial_compiled_yaml = initial_compile_result["canonical_session_yaml"]
        compiled_document = load_configuration_yaml(initial_compiled_yaml)
        leo_segment = next(
            segment
            for segment in compiled_document["segments"]
            if segment["id"] == SOURCE_SEGMENT_ID
        )
        assert isinstance(leo_segment["source"], str)
        assert leo_segment["source"].startswith("user:constellations/")

        initial_saved = vs_api.request_json(
            "POST",
            "/api/v1/builder/session/save",
            payload=initial_compiled["save_request"],
        )
        initial_saved_yaml = initial_saved["session"]["canonical_yaml"]
        assert initial_saved_yaml == initial_compiled_yaml
        assert initial_saved["deploy_verdict"]["allowed"] is True
        initial_closure_refs = {
            entry["ref"] for entry in initial_saved["dependency_closure"]["entries"]
        }
        assert set(forked_refs).issubset(initial_closure_refs)

        terminal_draft = vs_api.request_json(
            "POST",
            "/api/v1/builder/catalog/draft/open",
            payload={"source_ref": terminal_ref},
        )
        original_max_range = float(terminal_draft["document"]["terminal"]["max_range_km"])
        terminal_max_range = original_max_range + 1
        terminal_notes = f"Builder live qualification component {suffix}."
        patched_terminal = vs_api.request_json(
            "POST",
            "/api/v1/builder/catalog/draft/patch",
            payload={
                "draft": terminal_draft,
                "expected_draft_revision": terminal_draft["draft_revision"],
                "commands": [
                    {
                        "operation": "replace",
                        "pointer": "/terminal/max_range_km",
                        "value": terminal_max_range,
                    },
                    {
                        "operation": "replace",
                        "pointer": "/terminal/notes",
                        "value": terminal_notes,
                    },
                ],
            },
        )
        terminal_compile = vs_api.request_json(
            "POST",
            "/api/v1/builder/catalog/draft/compile",
            payload={
                "draft": patched_terminal,
                "expected_draft_revision": patched_terminal["draft_revision"],
            },
        )
        assert terminal_compile["save_allowed"] is True, terminal_compile["issues"]
        saved_terminal = vs_api.request_json(
            "POST",
            "/api/v1/builder/catalog/draft/save",
            payload={
                "draft": patched_terminal,
                "expected_draft_revision": patched_terminal["draft_revision"],
            },
        )
        assert (
            saved_terminal["result"]["document"]["canonical_json"]["terminal"]["max_range_km"]
            == terminal_max_range
        )
        assert (
            saved_terminal["result"]["document"]["canonical_json"]["terminal"]["notes"]
            == terminal_notes
        )

        reopened = vs_api.request_json(
            "POST",
            "/api/v1/builder/draft/open",
            payload={"source_ref": session_ref},
        )
        assert reopened["projection_status"] == "applied"
        assert reopened["session_yaml"] == initial_saved_yaml
        assert reopened["expected_session_revision"] == initial_saved["session"]["revision"]

        recompiled = vs_api.request_json(
            "POST",
            "/api/v1/builder/draft/compile",
            payload={"draft": reopened},
        )
        recompile_result = recompiled["compile_result"]
        assert recompile_result["save_verdict"]["allowed"] is True
        assert recompile_result["deploy_eligibility_after_save"]["allowed"] is True
        assert recompile_result["canonical_session_yaml"] == initial_saved_yaml

        saved = vs_api.request_json(
            "POST",
            "/api/v1/builder/session/save",
            payload=recompiled["save_request"],
        )
        saved_yaml = saved["session"]["canonical_yaml"]
        assert saved_yaml == initial_saved_yaml
        assert saved["deploy_verdict"]["allowed"] is True
        assert saved["digests"]["document"] == initial_saved["digests"]["document"]
        assert saved["digests"]["dependency"] != initial_saved["digests"]["dependency"]
        closure_refs = {entry["ref"] for entry in saved["dependency_closure"]["entries"]}
        assert closure_refs == initial_closure_refs

        exported = vs_api.request_json(
            "POST",
            "/api/v1/builder/session/yaml/export",
            payload={
                "session_ref": session_ref,
                "expected_session_revision": saved["session"]["revision"],
            },
        )
        assert exported["files"][0] == {
            "logical_path": "session.yaml",
            "yaml_text": saved_yaml,
        }
        exported_entries = {
            file["logical_path"]: file["yaml_text"] for file in exported["files"][1:]
        }
        terminal_path = f"catalog/user/{terminal_ref.removeprefix('user:')}"
        assert terminal_path in exported_entries

        stored_terminal = vs_api.request_json(
            "POST",
            "/api/v1/builder/catalog/get",
            payload={"ref": terminal_ref},
        )
        assert stored_terminal["canonical_json"]["terminal"]["max_range_km"] == terminal_max_range
        assert stored_terminal["canonical_json"]["terminal"]["notes"] == terminal_notes
        assert exported_entries[terminal_path] == stored_terminal["canonical_yaml"]

        deploy_request = {
            "session_ref": session_ref,
            "expected_session_revision": saved["session"]["revision"],
            "expected_document_digest": saved["digests"]["document"],
            "expected_dependency_digest": saved["digests"]["dependency"],
        }
        accepted = vs_api.request_json(
            "POST",
            "/api/v1/builder/session/deploy",
            expected_status=202,
            payload=deploy_request,
        )
        assert accepted["source"] == deploy_request
        transition = _wait_for_transition(vs_api, accepted["operation_id"])
        assert transition["state"] == "succeeded", transition
        assert transition["source"] == {"kind": "catalog_session", "logical_id": session_ref}
        assert transition["facts"]["document_digest"] == saved["digests"]["document"]
        assert transition["facts"]["closure_digest"] == saved["digests"]["dependency"]
        assert (
            transition["facts"]["resolved_semantic_digest"] == saved["digests"]["resolved_semantic"]
        )

        cr = _kubectl_json(
            "get",
            "constellationspec",
            "current-session",
            "-n",
            namespace,
            "-o",
            "json",
        )
        metadata = cr["metadata"]
        spec = cr["spec"]
        status = cr["status"]
        assert spec["sessionYaml"] == saved_yaml
        assert metadata["annotations"]["nodalarc.io/source-id"] == session_ref
        assert (
            metadata["annotations"]["nodalarc.io/source-revision"] == saved["session"]["revision"]
        )
        assert status["phase"] == "Ready"
        assert status["observedGeneration"] == metadata["generation"]
        assert transition["runtime"]["generation"] == metadata["generation"]
        assert status["runtimeRelease"] == expected_release
        assert status["runtimeBuild"] == expected_build
        assert status["documentDigest"] == saved["digests"]["document"]
        assert status["closureDigest"] == saved["digests"]["dependency"]
        assert status["resolvedSemanticDigest"] == saved["digests"]["resolved_semantic"]

        selection, uploaded_files = _verify_remote_upload(cr, namespace=namespace)
        assert selection.closure_digest == saved["digests"]["dependency"]
        assert selection.file_count == len(uploaded_files)
        assert transition["facts"]["file_count"] == selection.file_count + 1
        assert set(spec["catalogUpload"]) == {"upload_id", "closure_digest", "file_count"}
        assert uploaded_files[terminal_ref] == stored_terminal["canonical_yaml"].encode()
        assert closure_refs == set(uploaded_files)

        ome_pod, ome_proof = _runtime_service_proof("nodalarc-ome", namespace=namespace)
        scheduler_pod, scheduler_proof = _runtime_service_proof(
            "nodalarc-scheduler",
            namespace=namespace,
        )
        _assert_proof(
            ome_proof,
            origin="ome",
            pod=ome_pod,
            cr=cr,
            selection=selection,
            status=status,
        )
        _assert_proof(
            scheduler_proof,
            origin="scheduler",
            pod=scheduler_pod,
            cr=cr,
            selection=selection,
            status=status,
        )
        smoke = _wait_for_runtime_smoke(vs_api, namespace=namespace)

        evidence.update(
            {
                "result": "PASS",
                "operation": transition,
                "session_revision": saved["session"]["revision"],
                "document_digest": status["documentDigest"],
                "closure_digest": selection.closure_digest,
                "resolved_semantic_digest": status["resolvedSemanticDigest"],
                "upload_id": selection.upload_id,
                "uploaded_refs": sorted(uploaded_files),
                "cr_uid": metadata["uid"],
                "cr_generation": metadata["generation"],
                "session_run_id": status["sessionRunId"],
                "ome_proof": ome_proof,
                "scheduler_proof": scheduler_proof,
                "runtime_smoke": smoke,
            }
        )
    except BaseException as caught:
        error = caught
        evidence.update(
            {
                "result": "FAIL",
                "error_type": type(caught).__name__,
                "error": str(caught),
            }
        )
        raise
    finally:
        evidence["finished_at"] = datetime.now(UTC).isoformat()
        evidence_root = Path(
            os.environ.get("NODALARC_E2E_EVIDENCE_DIR", "tests/integration/e2e-evidence")
        )
        evidence_root.mkdir(parents=True, exist_ok=True)
        evidence_path = evidence_root / f"builder-user-ref-{suffix}.json"
        evidence_path.write_text(json.dumps(evidence, indent=2, sort_keys=True) + "\n")
        print(f"Builder Kubernetes evidence: {evidence_path}")
        if error is not None:
            print(f"Builder Kubernetes qualification failed: {type(error).__name__}: {error}")
