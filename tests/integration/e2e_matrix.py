"""E2E validation matrix for every shipped catalog session.

Runs each session: byte-verify shipped YAML → guarded catalog switch →
wait for the exact transition and Ready state → verify pods + FRR configs +
routing convergence + WebSocket snapshots → write evidence files.

Usage: make test-runtime-matrix
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import requests
from nodalarc.catalog_closure import FilesystemCatalogReadView
from nodalarc.configuration_yaml import load_configuration_yaml
from nodalarc.runtime_naming import gs_bridge_port_name
from nodalarc.workload_target import (
    NODE_ID_LABEL,
    WorkloadTarget,
    WorkloadTargetError,
    select_live_pod,
    workload_target_from_pod,
)

# Default assumes a local port-forward. Set VS_API_HOST to any reachable LAN
# address or service DNS name when testing a distributed deployment.
VS_API_HOST = os.environ.get("VS_API_HOST", "127.0.0.1:8080")
BASE_URL = f"http://{VS_API_HOST}"
KUBECTL = "sudo KUBECONFIG=/etc/rancher/k3s/k3s.yaml kubectl"


# The MBB acceptance lanes run this shipped session unchanged: deployed through
# the catalog contract like every shipped permutation, with the transition's
# document digest compared against the checkout's file. No fixture, no rewrite,
# no overlap substitution: the experiment is the one users get.
MBB_ACCEPTANCE_SESSION_ID = "earth-leo-walker"
MBB_BAD_OPS_CODES = {
    "KERNEL_DIRTY",
    "ACTUATION_BLOCKED",
    "ACTUATION_HALTED",
    "AUTHORITY_SUBSET_VIOLATION",
    "OPERATOR_REPAIR_REQUESTED",
    "OPERATOR_REPAIR_SUCCEEDED",
    "OPERATOR_REPAIR_FAILED",
}

INTERMITTENT_CONNECTIVITY_WINDOWS = {
    "earth-leo-polar": {
        "disconnected_offset_seconds": 120,
        "settle_seconds": 30,
    }
}


def _run_provenance_from_environment() -> dict[str, str]:
    fields = {
        "source_git_sha": os.environ.get("NODALARC_EVIDENCE_SOURCE_GIT_SHA", ""),
        "source_tree_tag": os.environ.get("NODALARC_EVIDENCE_SOURCE_TAG", ""),
        "namespace": os.environ.get("NAMESPACE", ""),
        "expected_runtime_release": os.environ.get("NODALARC_EXPECTED_RUNTIME_RELEASE", ""),
        "expected_runtime_build": os.environ.get("NODALARC_EXPECTED_RUNTIME_BUILD", ""),
    }
    missing = [name for name, value in fields.items() if not value]
    if missing:
        raise RuntimeError(
            "Runtime evidence provenance is incomplete; use make test-runtime-matrix "
            f"(missing {', '.join(missing)})"
        )
    return fields


def _runtime_identity_error(provenance: dict[str, str], facts: dict) -> str | None:
    observed_release = facts.get("release")
    observed_build = facts.get("build")
    if (
        observed_release == provenance["expected_runtime_release"]
        and observed_build == provenance["expected_runtime_build"]
    ):
        return None
    return (
        "Transition runtime identity differs from the checkout under test: "
        f"expected {provenance['expected_runtime_release']} / "
        f"{provenance['expected_runtime_build']}, observed "
        f"{observed_release} / {observed_build}"
    )


def _classify_matrix_result(evidence: dict, perm: dict) -> str:
    """Apply xfail/xpass accounting to one matrix result in place."""
    if evidence.get("result") == "PASS" and perm.get("xfail"):
        evidence["result"] = "XPASS"
        evidence["xfail_reason"] = perm["xfail"]
        return "xpass"
    if evidence.get("result") == "PASS":
        return "pass"
    if perm.get("xfail"):
        evidence["result"] = "XFAIL"
        evidence["xfail_reason"] = perm["xfail"]
        return "xfail"
    return "fail"


def _perm_declares_ground(perm: dict) -> bool:
    """Return whether the scenario declaration includes ground endpoints."""
    ground = perm.get("gs", perm.get("ground_stations"))
    if ground is None:
        return False
    if isinstance(ground, str):
        return bool(ground.strip())
    if isinstance(ground, (list, tuple, set)):
        return len(ground) > 0
    return bool(ground)


def get_token(retries: int = 12, delay: float = 5.0) -> str:
    for _attempt in range(retries):
        try:
            resp = requests.get(f"{BASE_URL}/api/v1/auth/token", timeout=5)
            return resp.json()["token"]
        except Exception:
            time.sleep(delay)
    raise RuntimeError(f"VS-API not reachable after {retries * delay}s")


def headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


def acceptance_progress(message: str) -> None:
    print(f"[acceptance] {message}", flush=True)


def request_json(method: str, path: str, *, token: str | None = None, retries: int = 12, **kwargs):
    """Request a VS-API JSON endpoint, tolerating session-switch restarts."""

    url = f"{BASE_URL}{path}"
    request_headers = kwargs.pop("headers", {})
    if token is not None:
        request_headers = {**headers(token), **request_headers}
    last_error = ""
    for attempt in range(retries):
        try:
            resp = requests.request(method, url, headers=request_headers, timeout=10, **kwargs)
            if resp.status_code >= 500:
                last_error = f"{resp.status_code} {resp.text[:300]}"
            else:
                return resp.json()
        except Exception as exc:
            last_error = str(exc)
        if attempt + 1 < retries:
            time.sleep(2)
    raise RuntimeError(
        f"{method} {path} did not return JSON after {retries} attempts: {last_error}"
    )


def _node_type(node: dict) -> str | None:
    value = node.get("node_type") or node.get("kind")
    return str(value) if value is not None else None


def _is_satellite_node(node: dict) -> bool:
    return _node_type(node) == "satellite"


def _is_ground_node(node: dict) -> bool:
    return _node_type(node) == "ground_station"


def _satellite_nodes(nodes: list[dict]) -> list[dict]:
    return [node for node in nodes if _is_satellite_node(node)]


def _ground_nodes(nodes: list[dict]) -> list[dict]:
    return [node for node in nodes if _is_ground_node(node)]


def _nodes_by_id(nodes: list[dict]) -> dict[str, dict]:
    return {str(node["node_id"]): node for node in nodes if node.get("node_id") is not None}


def _node_id_has_type(node_id: str, nodes_by_id: dict[str, dict], node_type: str) -> bool:
    node = nodes_by_id.get(node_id)
    return node is not None and _node_type(node) == node_type


def _router_loopback_from_node(node: dict | None) -> str | None:
    if not node:
        return None
    for address in node.get("addresses") or []:
        if not isinstance(address, dict):
            continue
        if address.get("purpose") == "router_loopback" and address.get("family") == "ipv4":
            raw = str(address.get("address") or "").strip()
            if raw:
                return raw.split("/", 1)[0]
    return None


def _published_loopback_ip(node_id: str, nodes_by_id: dict[str, dict]) -> str | None:
    """The node's IPv4 router loopback as the resolver published it on the
    VS-API state. There is no other source: a node without a published
    loopback has no probe identity, and no command is run to guess one."""
    return _router_loopback_from_node(nodes_by_id.get(node_id))


def _workload_target(node_id: str) -> tuple[WorkloadTarget | None, str | None]:
    """The node's live pod and primary workload container, as the Operator
    published them. The pod is selected by the node-id label, never by a
    name derived from the id; exactly one live pod must exist; the
    annotation must name a declared container; and the pod's label must be
    the requested node. Any other outcome is a typed reason, not a guess."""
    import subprocess

    listing = subprocess.run(
        f"{KUBECTL} get pods -n nodalarc -l {NODE_ID_LABEL}={node_id} -o json",
        capture_output=True,
        text=True,
        timeout=20,
        shell=True,
    )
    if listing.returncode != 0:
        return None, f"pod listing for {node_id} failed: {listing.stderr.strip()[-200:]}"
    try:
        items = json.loads(listing.stdout).get("items") or []
    except ValueError:
        return None, f"pod listing for {node_id} was not JSON"
    try:
        target = workload_target_from_pod(select_live_pod(items, node_id))
    except WorkloadTargetError as exc:
        return None, str(exc)
    if target.node_id != node_id:
        return (
            None,
            f"pod {target.pod_name!r} is labelled for node {target.node_id!r}, not {node_id!r}",
        )
    return target, None


def _workload_exec(node_id: str, command: str, *, timeout: int = 20) -> dict:
    """Run one command in the node's published primary workload container.

    The target is resolved on every call, so a replaced pod is never reached
    through a stale name. The exit status classifies nothing: each probe
    step decides what it observed by parsing the tool's own output. A
    target that cannot be resolved is reported in ``resolution_error`` and
    nothing is executed.
    """
    import subprocess

    target, error = _workload_target(node_id)
    if target is None:
        return {
            "rc": None,
            "stdout": "",
            "stderr": "",
            "target": None,
            "resolution_error": error,
        }
    result = subprocess.run(
        f"{KUBECTL} exec -n {target.namespace} {target.pod_name} "
        f"-c {target.container} -- {command}",
        capture_output=True,
        text=True,
        timeout=timeout,
        shell=True,
    )
    return {
        "rc": result.returncode,
        "stdout": result.stdout[-4000:],
        "stderr": result.stderr[-1000:],
        "target": {"pod": target.pod_name, "container": target.container},
        "resolution_error": None,
    }


# Kernel answers that are evidence of *no route*: ENETUNREACH and EHOSTUNREACH
# in the wording of glibc ("Network is unreachable", "No route to host") and
# of musl ("Network unreachable", "Host is unreachable"), which the Alpine
# router image uses. Every other RTNETLINK answer (Operation not permitted,
# Invalid argument, ...) is a failed probe.
ROUTING_UNREACHABLE_ANSWERS = (
    "Network is unreachable",
    "Network unreachable",
    "No route to host",
    "Host is unreachable",
)


def _route_observation(result: dict, dst_ip: str) -> dict:
    """What `ip route get DST` observed: a route (positive), a recognized
    no-route answer (negative), or nothing usable (unobserved)."""
    if result.get("resolution_error"):
        return {"observed": False, "positive": False, "reason": result["resolution_error"]}
    stdout = result.get("stdout") or ""
    stderr = result.get("stderr") or ""
    egress = _route_egress_dev(stdout)
    if dst_ip in stdout and egress:
        return {"observed": True, "positive": True, "egress_dev": egress, "reason": "route present"}
    for answer in ROUTING_UNREACHABLE_ANSWERS:
        if f"RTNETLINK answers: {answer}" in stderr or f"RTNETLINK answers: {answer}" in stdout:
            return {
                "observed": True,
                "positive": False,
                "egress_dev": None,
                "reason": f"kernel answered {answer}",
            }
    return {
        "observed": False,
        "positive": False,
        "egress_dev": None,
        "reason": (
            f"route query for {dst_ip} produced no observation "
            f"(rc={result.get('rc')}): {(stderr or stdout).strip()[-160:]}"
        ),
    }


_DAEMON_REFUSAL_MARKERS = ("failed to connect to any daemons", "Exiting:", "% ")
_NEIGHBOR_TABLE_MARKERS = {
    "isis": ("System Id", "Area "),
    "ospf": ("Neighbor ID",),
}


def _adjacency_observation(result: dict, protocol: str) -> dict:
    """What the routing daemon's neighbor query observed: a table with an
    adjacency in the up state (positive), a table without one (negative),
    or no table at all (unobserved: vtysh absent, daemon refused, unusable)."""
    if result.get("resolution_error"):
        return {"observed": False, "positive": False, "reason": result["resolution_error"]}
    stdout = result.get("stdout") or ""
    stderr = result.get("stderr") or ""
    combined = stdout + "\n" + stderr
    if any(marker in combined for marker in _DAEMON_REFUSAL_MARKERS):
        return {
            "observed": False,
            "positive": False,
            "reason": f"routing daemon query refused: {combined.strip()[-160:]}",
        }
    markers = _NEIGHBOR_TABLE_MARKERS["ospf" if protocol == "ospf" else "isis"]
    if not any(marker in stdout for marker in markers):
        return {
            "observed": False,
            "positive": False,
            "reason": (
                f"neighbor query produced no table (rc={result.get('rc')}): "
                f"{combined.strip()[-160:]}"
            ),
        }
    up = _routing_neighbor_up(stdout, protocol)
    return {
        "observed": True,
        "positive": up,
        "reason": "adjacency up" if up else "neighbor table has no adjacency in the up state",
    }


_PING_STATISTICS = re.compile(
    r"(\d+) packets transmitted, (\d+) (?:packets )?received,.*?(\d+(?:\.\d+)?)% packet loss"
)


def _parse_ping_statistics(stdout: str) -> dict | None:
    """The numbers of ping's statistics line, or None when ping printed none.

    The line is an observation only when its numbers agree: at least one
    packet transmitted, no more received than transmitted, and the printed
    loss percentage matching the counts (ping rounds to a whole percent).
    A line that fails those checks is returned with ``consistent`` False and
    the ``problem`` named, and no caller treats it as an observation.
    """
    for line in stdout.splitlines():
        match = _PING_STATISTICS.search(line)
        if not match:
            continue
        transmitted, received = int(match.group(1)), int(match.group(2))
        loss_pct = float(match.group(3))
        problem = None
        if transmitted <= 0:
            problem = "no packet was transmitted"
        elif received > transmitted:
            problem = f"{received} received exceeds {transmitted} transmitted"
        else:
            expected_loss = (transmitted - received) * 100.0 / transmitted
            if abs(loss_pct - expected_loss) > 1.0:
                problem = f"{loss_pct:g}% loss contradicts {received} of {transmitted} received"
        return {
            "transmitted": transmitted,
            "received": received,
            "loss_pct": loss_pct,
            "loss_class": _packet_loss_class(transmitted, received) if problem is None else None,
            "consistent": problem is None,
            "problem": problem,
            "stats": line.strip(),
        }
    return None


def _packet_loss_class(transmitted: int, received: int) -> str:
    if transmitted > 0 and received == transmitted:
        return "zero_loss"
    if received == 0:
        return "total_loss"
    return "partial_loss"


def _ping_unreachable_answer(stdout: str, stderr: str) -> str | None:
    """A recognized no-route answer printed by ping itself (`ping: ...`), never
    one that appears inside an exec or runtime diagnostic."""
    for line in (stdout + "\n" + stderr).splitlines():
        text = line.strip()
        if not text.startswith("ping:"):
            continue
        for answer in ROUTING_UNREACHABLE_ANSWERS:
            if answer in text:
                return answer
    return None


def _packet_observation(result: dict) -> dict:
    """What a synchronous ping observed. Consistent completed statistics, or a
    no-route answer printed by ping, are observations; anything else is
    unobserved. The sequence-only reading belongs to the interrupted handover
    window and is never a completed observation here."""
    if result.get("resolution_error"):
        return {"observed": False, "positive": False, "reason": result["resolution_error"]}
    stdout = result.get("stdout") or ""
    stderr = result.get("stderr") or ""
    stats = _parse_ping_statistics(stdout)
    if stats is not None and not stats["consistent"]:
        return {
            "observed": False,
            "positive": False,
            "replies": False,
            "loss_class": None,
            "stats": stats["stats"],
            "reason": f"ping statistics are not a valid observation: {stats['problem']}",
        }
    if stats is not None:
        return {
            "observed": True,
            "positive": stats["loss_class"] == "zero_loss",
            "replies": stats["received"] > 0,
            "loss_class": stats["loss_class"],
            "stats": stats["stats"],
            "reason": f"ping {stats['loss_class']}: {stats['stats']}",
        }
    answer = _ping_unreachable_answer(stdout, stderr)
    if answer is not None:
        return {
            "observed": True,
            "positive": False,
            "replies": False,
            "loss_class": "total_loss",
            "stats": "network unreachable",
            "reason": f"ping answered {answer}",
        }
    return {
        "observed": False,
        "positive": False,
        "replies": False,
        "loss_class": None,
        "stats": "",
        "reason": (
            f"ping produced no statistics (rc={result.get('rc')}): "
            f"{(stderr or stdout).strip()[-160:]}"
        ),
    }


def _sweep_verdict(attempts: list[dict], no_probe_reason: str) -> dict:
    """The FAIL of a probe sweep that found no proof. Disconnection is a
    conclusion only when every executed probe was fully observed and
    negative; one failed probe, or none executed, is a failed probe. Every
    attempt and every distinct failure reason is retained in the evidence."""
    failures = [a for a in attempts if a.get("kind") == "probe_failure"]
    negatives = [a for a in attempts if a.get("kind") == "observed_negative"]
    counts = {
        "attempt_count": len(attempts),
        "probe_failure_count": len(failures),
        "observed_negative_count": len(negatives),
    }
    if failures:
        reasons: list[str] = []
        for attempt in failures:
            if attempt["reason"] not in reasons:
                reasons.append(attempt["reason"])
        summary = "; ".join(reasons[:3])
        if len(reasons) > 3:
            summary += f"; and {len(reasons) - 3} more distinct reasons"
        return {
            "result": "FAIL",
            "failure_kind": "probe",
            "reason": f"probe failed ({len(failures)} of {len(attempts)} attempts): {summary}",
            "probe_failure_reasons": reasons,
            "attempts": attempts,
            **counts,
        }
    if negatives:
        return {
            "result": "FAIL",
            "failure_kind": "connectivity",
            "reason": f"{negatives[-1]['reason']} ({len(negatives)} attempts, all observed negative)",
            "attempts": attempts,
            **counts,
        }
    return {
        "result": "FAIL",
        "failure_kind": "probe",
        "reason": f"no probe executed: {no_probe_reason}",
        "attempts": attempts,
        **counts,
    }


def _link_as_ground_sat(
    link: dict,
    nodes_by_id: dict[str, dict],
) -> tuple[str, str] | None:
    a = str(link.get("node_a", ""))
    b = str(link.get("node_b", ""))
    if _node_id_has_type(a, nodes_by_id, "ground_station") and _node_id_has_type(
        b, nodes_by_id, "satellite"
    ):
        return a, b
    if _node_id_has_type(b, nodes_by_id, "ground_station") and _node_id_has_type(
        a, nodes_by_id, "satellite"
    ):
        return b, a
    return None


def _link_as_sat_sat(
    link: dict,
    nodes_by_id: dict[str, dict],
) -> tuple[str, str] | None:
    a = str(link.get("node_a", ""))
    b = str(link.get("node_b", ""))
    if _node_id_has_type(a, nodes_by_id, "satellite") and _node_id_has_type(
        b, nodes_by_id, "satellite"
    ):
        return a, b
    return None


def deploy_catalog_session(token: str, perm: dict) -> dict:
    """Deploy the exact shipped catalog revision represented by one permutation."""
    session_ref = f"nodalarc:sessions/{perm['id']}.yaml"
    summaries = request_json("GET", "/api/v1/sessions", token=token)
    if not isinstance(summaries, list):
        raise RuntimeError("VS-API session listing was not an array")
    summary = next(
        (
            candidate
            for candidate in summaries
            if (candidate.get("source_id") or {}).get("session_ref") == session_ref
        ),
        None,
    )
    if summary is None:
        raise RuntimeError(f"VS-API did not list shipped session {session_ref}")
    if not summary.get("deploy_allowed"):
        raise RuntimeError(
            f"VS-API refused shipped session {session_ref}: {summary.get('blockers')}"
        )

    response = requests.get(
        f"{BASE_URL}/api/v1/sessions/yaml",
        params={"session_ref": session_ref},
        headers=headers(token),
        timeout=10,
    )
    response.raise_for_status()
    if response.text != perm["session_yaml"]:
        raise RuntimeError(f"VS-API shipped YAML differs from checkout for {session_ref}")

    required = ("source_revision", "document_digest", "dependency_digest")
    missing = [field for field in required if not summary.get(field)]
    if missing:
        raise RuntimeError(f"VS-API session listing omitted {', '.join(missing)} for {session_ref}")
    return request_json(
        "POST",
        "/api/v1/sessions/switch",
        token=token,
        json={
            "source": {"kind": "catalog", "session_ref": session_ref},
            "expected_source_revision": summary["source_revision"],
            "expected_document_digest": summary["document_digest"],
            "expected_dependency_digest": summary["dependency_digest"],
        },
        retries=3,
    )


def deploy_shipped_and_wait(token: str, perm: dict, *, timeout: int = 600) -> dict:
    """Deploy one shipped permutation through the catalog contract and wait for
    its admitted transition: the guarded switch answers with an operation id,
    the transition's terminal state decides, the transition's runtime facts must
    name the checkout under test, and the deployed document digest must be the
    checkout's file. Returns PASS with the responses and the observed runtime,
    or FAIL with a reason. Every acceptance lane and every matrix permutation
    deploys through this one path."""
    deploy_response = deploy_catalog_session(token, perm)
    operation_id = deploy_response.get("operation_id")
    if deploy_response.get("status") != "accepted" or not operation_id:
        return {
            "result": "FAIL",
            "reason": f"Deploy refused: {deploy_response}",
            "deploy_response": deploy_response,
        }
    transition = wait_for_transition(token, str(operation_id), timeout=timeout)
    if transition.get("state") != "succeeded":
        return {
            "result": "FAIL",
            "reason": f"Transition failed: {transition}",
            "deploy_response": deploy_response,
            "transition": transition,
        }
    facts = transition.get("facts") or {}
    observed_runtime = {
        "release": facts.get("release"),
        "build": facts.get("build"),
        "document_digest": facts.get("document_digest"),
        "closure_digest": facts.get("closure_digest"),
        "resolved_semantic_digest": facts.get("resolved_semantic_digest"),
    }
    outcome = {
        "deploy_response": deploy_response,
        "transition": transition,
        "observed_runtime": observed_runtime,
    }
    identity_error = _runtime_identity_error(perm["run_provenance"], facts)
    if identity_error is not None:
        return {"result": "FAIL", "reason": identity_error, **outcome}
    expected_digest = f"sha256:{perm['document_sha256']}"
    if observed_runtime["document_digest"] != expected_digest:
        return {
            "result": "FAIL",
            "reason": (
                f"Deployed document digest {observed_runtime['document_digest']} is not the "
                f"checkout's {perm['id']} ({expected_digest})"
            ),
            **outcome,
        }
    return {"result": "PASS", **outcome}


def wait_for_transition(token: str, operation_id: str, timeout: int = 600) -> dict:
    """Wait for the exact admitted session transition to reach a terminal state."""
    deadline = time.monotonic() + timeout
    last: dict = {}
    while time.monotonic() < deadline:
        last = request_json(
            "GET",
            f"/api/v1/session-transitions/{operation_id}",
            token=token,
            retries=3,
        )
        state = last.get("state")
        if state == "succeeded":
            return last
        if state in {"failed", "cancelled"}:
            return last
        time.sleep(2)
    return {
        "state": "timeout",
        "failure": {"message": f"transition {operation_id} did not finish within {timeout}s"},
        "last": last,
    }


def wait_for_ready(token: str, timeout: int = 600) -> dict:
    """Wait for CR Ready AND VS-API session_status to settle."""
    import subprocess

    deadline = time.monotonic() + timeout

    # Wait for CR to reach Ready or Error.
    cr_ready = False
    while time.monotonic() < deadline:
        try:
            result = subprocess.run(
                f"{KUBECTL} get constellationspec current-session -n nodalarc "
                "-o jsonpath={.status.phase}",
                capture_output=True,
                text=True,
                timeout=10,
                shell=True,
            )
            phase = result.stdout.strip()
            if phase == "Ready":
                cr_ready = True
                break
            if phase == "Error":
                result2 = subprocess.run(
                    f"{KUBECTL} get constellationspec current-session -n nodalarc "
                    "-o jsonpath={.status.message}",
                    capture_output=True,
                    text=True,
                    timeout=10,
                    shell=True,
                )
                return {"phase": "Error", "detail": result2.stdout.strip()}
        except Exception:
            pass
        time.sleep(5)

    if not cr_ready:
        return {"phase": "Timeout"}

    # Wait for VS-API to expose a live, non-empty state snapshot.
    # The _run_switch background task may still be running its poll loop.
    for _ in range(120):  # up to 120s
        try:
            t = get_token()
            state = request_json("GET", "/api/v1/state", token=t, retries=2)
            status = state.get("session_status", "")
            nodes = state.get("nodes", [])
            if status != "switching" and nodes:
                return {"phase": "Ready", "nodes": len(nodes)}
        except Exception:
            pass
        time.sleep(1)

    return {"phase": "Timeout", "detail": "VS-API did not expose a live state snapshot"}


def check_pods(perm: dict) -> dict:
    """Check pod count and status via kubectl."""
    import subprocess

    result = subprocess.run(
        f"{KUBECTL} get pods -n nodalarc -l nodalarc.io/node-id --no-headers",
        capture_output=True,
        text=True,
        shell=True,
    )
    lines = [l for l in result.stdout.strip().splitlines() if l.strip()]
    total = len(lines)
    running = sum(1 for l in lines if "Running" in l)
    return {"total": total, "running": running}


def check_routing(token: str, perm: dict) -> dict:
    """Check routing convergence via introspect."""
    protocol = perm["protocol"]
    if protocol == "nodalpath":
        return {
            "protocol": "nodalpath",
            "check": "deferred_to_ping",
            "reason": "MPLS table checked in ping step",
        }

    nodes = request_json("GET", "/api/v1/state", token=token).get("nodes", [])
    sat = next(iter(_satellite_nodes(nodes)), None)
    if not sat:
        return {"error": "no satellites found"}

    if protocol == "isis":
        cmd = "show isis neighbor"
    else:
        cmd = "show ip ospf neighbor"

    introspect = request_json(
        "POST",
        "/api/v1/introspect",
        token=token,
        json={"node_id": sat["node_id"], "command": cmd},
    )
    output = introspect.get("output", "")
    neighbor_count = len([l for l in output.splitlines() if "Up" in l or "Full" in l])
    return {
        "protocol": protocol,
        "node": sat["node_id"],
        "command": cmd,
        "neighbor_count": neighbor_count,
        "output_lines": len(output.splitlines()),
    }


def _routing_neighbor_command(protocol: str) -> str:
    return "show ip ospf neighbor" if protocol == "ospf" else "show isis neighbor"


def _routing_neighbor_up(output: str, protocol: str) -> bool:
    return "Full" in output if protocol == "ospf" else "Up" in output


def check_websocket(token: str, step_seconds: int = 1) -> dict:
    """Check the feed delivers advancing sim_time.

    The sample gap derives from the session's own tick — a GEO session
    deliberately ticks every 10 seconds, and two samples inside one
    tick legitimately read identical sim_time (found on the first
    catalog run: both GEO sessions failed this check while their
    adjacencies and pings passed)."""
    state1 = request_json("GET", "/api/v1/state", token=token)
    t1 = state1.get("sim_time", "")
    time.sleep(max(3, int(step_seconds) + 2))
    state2 = request_json("GET", "/api/v1/state", token=token)
    t2 = state2.get("sim_time", "")
    nodes = state2.get("nodes", [])
    sats = _satellite_nodes(nodes)

    def _sats_ready(sats_now: list[dict]) -> bool:
        # plane/slot are OPTIONAL grid coordinates — individually placed
        # satellites (GEO longitude slots) legitimately carry none. What
        # every satellite must have is a real position.
        return all(
            isinstance(s.get("lat_deg"), (int, float))
            and (s.get("plane") is None or isinstance(s.get("plane"), int))
            for s in sats_now
        )

    plane_ok = _sats_ready(sats)

    # Retry — PositionEvents may not have reached all nodes yet
    retries = 0
    while not plane_ok and retries < 3:
        time.sleep(10)
        nodes = request_json("GET", "/api/v1/state", token=token).get("nodes", [])
        sats = _satellite_nodes(nodes)
        plane_ok = _sats_ready(sats)
        retries += 1

    return {
        "sim_time_1": t1[:19],
        "sim_time_2": t2[:19],
        "advancing": t1 != t2,
        "node_count": len(nodes),
        "plane_slot_ok": plane_ok,
        "plane_slot_retries": retries,
    }


def check_ping(token: str, perm: dict, *, ground_wait_s: int | None = None) -> dict:
    """Prove routed connectivity for the declared topology.

    Ground sessions must prove a ground-originated routed ping and routing adjacency.
    Satellite-only sessions fall back to an ISL loopback ping. SKIP is valid only when
    the session declares no ground endpoint and no connected satellite pair exists.
    """
    protocol = perm["protocol"]
    if protocol == "nodalpath":
        return check_nodalpath_mpls(token, perm)

    state = request_json("GET", "/api/v1/state", token=token)
    nodes = state.get("nodes", [])
    links = state.get("links", [])
    if isinstance(links, dict):
        links = list(links.values())

    nodes_by_id = _nodes_by_id(nodes)
    gs_nodes = _ground_nodes(nodes)
    sat_nodes = _satellite_nodes(nodes)
    declares_ground = _perm_declares_ground(perm)

    if declares_ground or gs_nodes:
        if declares_ground and not gs_nodes:
            return {
                "result": "FAIL",
                "failure_kind": "probe",
                "mode": "ground_to_ground",
                "reason": "declared ground topology materialized no ground nodes",
                "ground_declared": True,
                "ground_node_count": 0,
                "active_link_count": len([l for l in links if l.get("state") == "active"]),
            }
        ground_topology = perm.get("ground_topology")
        # Cross-body sessions converge over multi-second light-time RTTs;
        # size the readiness deadline to the declared shape.
        bodies = {info["body"] for info in (ground_topology or {}).values()}
        ground_probe = _find_routed_ground_probe(
            token,
            protocol=protocol,
            wait_s=(
                ground_wait_s if ground_wait_s is not None else 240 if len(bodies) > 1 else 120
            ),
            ground_topology=ground_topology,
        )
        if ground_probe and ground_probe.get("result") == "PASS":
            return {
                **ground_probe,
                "ground_declared": declares_ground,
                "ground_node_count": len(gs_nodes),
            }
        if not (ground_probe and ground_probe.get("result") == "SINGLE_SITE"):
            return {
                "result": "FAIL",
                "failure_kind": (ground_probe or {}).get("failure_kind", "probe"),
                "mode": "ground_to_ground",
                "reason": (ground_probe or {}).get("reason", "ground connectivity was not proven"),
                "ground_declared": declares_ground,
                "ground_node_count": len(gs_nodes),
                "active_link_count": len([l for l in links if l.get("state") == "active"]),
                "last_probe": ground_probe,
            }
        # Single ground site: inter-site transit is unprovable by shape.
        # Fall through to the satellite strategies below, which prove the
        # space link directly (a satellite has no LAN-only path to ground).

    # Find active links to identify connected pairs in satellite-only sessions.
    active_links = [l for l in links if l.get("state") == "active"]

    # Strategy 1: Find two satellites connected by an ISL
    src = None
    dst = None
    for link in active_links:
        sat_pair = _link_as_sat_sat(link, nodes_by_id)
        if sat_pair is not None:
            src, dst = sat_pair
            break

    # Strategy 2: If no ISL link, find a satellite connected to a GS
    if not src:
        for link in active_links:
            ground_sat_pair = _link_as_ground_sat(link, nodes_by_id)
            if ground_sat_pair is not None:
                gs_id, sat_id = ground_sat_pair
                src, dst = sat_id, gs_id
                break

    if not src or not dst:
        return {
            "result": "SKIP",
            "reason": f"No connected node pairs found ({len(active_links)} active links, "
            f"{len(sat_nodes)} sats, {len(gs_nodes)} gs)",
            "active_link_count": len(active_links),
        }

    dst_ip = _published_loopback_ip(dst, nodes_by_id)
    if not dst_ip:
        return {
            "result": "FAIL",
            "failure_kind": "probe",
            "reason": f"no published router loopback for {dst}",
            "src": src,
            "dst": dst,
        }

    # Ping with retries; a reply proves the path, statistics are the observation.
    attempts: list[dict] = []
    deadline = time.monotonic() + 120  # 2 minutes
    while time.monotonic() < deadline:
        ping = _workload_exec(src, f"ping -c 3 -W 5 {dst_ip}", timeout=30)
        observation = _packet_observation(ping)
        attempt = {
            "candidate": f"{src}->{dst}",
            "elapsed_s": round(120 - (deadline - time.monotonic()), 1),
            "rc": ping["rc"],
            "stdout": ping["stdout"][-300:],
            "reason": observation["reason"],
            "kind": (
                "probe_failure"
                if not observation["observed"]
                else "observed_positive"
                if observation.get("replies")
                else "observed_negative"
            ),
        }
        attempts.append(attempt)
        if attempt["kind"] == "observed_positive":
            return {
                "result": "PASS",
                "src": src,
                "dst": dst,
                "dst_ip": dst_ip,
                "stats": observation["stats"],
                "loss_class": observation["loss_class"],
                "attempts": len(attempts),
            }
        time.sleep(10)

    return {
        **_sweep_verdict(attempts, "satellite ping never ran"),
        "src": src,
        "dst": dst,
        "dst_ip": dst_ip,
        "attempt_count": len(attempts),
        "last_stdout": attempts[-1]["stdout"] if attempts else "",
    }


def check_nodalpath_mpls(token: str, perm: dict) -> dict:
    """Check MPLS route entries for NodalPath sessions.

    NodalPath installs MPLS routes in the kernel via pyroute2 (not through FRR),
    so we check 'ip -f mpls route show' via kubectl exec (not vtysh introspect).
    """
    deadline = time.monotonic() + 120
    attempts: list[dict] = []
    output = ""
    nodes = request_json("GET", "/api/v1/state", token=token).get("nodes", [])
    sat = next(iter(_satellite_nodes(nodes)), None)
    if sat is None:
        return {
            "result": "FAIL",
            "failure_kind": "probe",
            "protocol": "nodalpath",
            "reason": "no satellites found",
        }
    node_id = sat["node_id"]
    while time.monotonic() < deadline:
        result = _workload_exec(node_id, "ip -f mpls route show", timeout=10)
        output = result["stdout"]
        if result["resolution_error"] or result["rc"] != 0:
            attempts.append(
                {
                    "candidate": node_id,
                    "kind": "probe_failure",
                    "reason": result["resolution_error"]
                    or f"mpls route query failed (rc={result['rc']}): {result['stderr'][-160:]}",
                }
            )
            time.sleep(15)
            continue
        mpls_lines = len([l for l in output.splitlines() if l.strip()])
        if mpls_lines > 0:
            return {
                "result": "PASS",
                "protocol": "nodalpath",
                "mpls_entries": mpls_lines,
                "attempts": len(attempts) + 1,
            }
        attempts.append(
            {"candidate": node_id, "kind": "observed_negative", "reason": "mpls table is empty"}
        )
        time.sleep(15)

    return {
        **_sweep_verdict(attempts, "mpls route query never ran"),
        "protocol": "nodalpath",
        "mpls_entries": 0,
        "attempt_count": len(attempts),
        "last_output": output[:500],
    }


def _active_ground_pair(token: str) -> tuple[str, str, str] | None:
    state = request_json("GET", "/api/v1/state", token=token)
    nodes = state.get("nodes", [])
    nodes_by_id = _nodes_by_id(nodes)
    links = state.get("links", [])
    if isinstance(links, dict):
        links = list(links.values())
    for link in links:
        if link.get("state") != "active":
            continue
        pair = _link_as_ground_sat(link, nodes_by_id)
        if pair is None:
            continue
        gs_id, sat_id = pair
        dst_ip = _published_loopback_ip(sat_id, nodes_by_id)
        if dst_ip:
            return gs_id, sat_id, dst_ip
    return None


def _kubectl_exec(node_id: str, command: str, *, timeout: int = 20) -> dict:
    """Run a command in the node's published primary workload container."""
    return _workload_exec(node_id, command, timeout=timeout)


def _run_shell(command: str, *, timeout: int = 20) -> dict:
    import subprocess

    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
        timeout=timeout,
        shell=True,
    )
    return {
        "rc": result.returncode,
        "stdout": result.stdout.strip()[-1000:],
        "stderr": result.stderr.strip()[-1000:],
    }


def _host_ground_ifname(gs_id: str, gs_ifname: str) -> str:
    if not gs_ifname.startswith("term") or not gs_ifname[4:].isdigit():
        raise ValueError(f"Unsupported ground terminal interface name: {gs_ifname}")
    return gs_bridge_port_name(gs_id, int(gs_ifname[4:]))


def _force_ground_host_interface_down(gs_id: str, gs_ifname: str, *, timeout: int = 20) -> dict:
    host_ifname = _host_ground_ifname(gs_id, gs_ifname)
    target, error = _workload_target(gs_id)
    if target is None:
        return {
            "rc": 1,
            "stdout": "",
            "stderr": f"workload target for {gs_id}: {error}",
            "host_ifname": host_ifname,
            "node_name": "",
        }
    node_result = _run_shell(
        f"{KUBECTL} get pod -n {target.namespace} {target.pod_name} -o jsonpath={{.spec.nodeName}}",
        timeout=timeout,
    )
    node_name = node_result["stdout"]
    if node_result["rc"] != 0 or not node_name:
        return {
            "rc": node_result["rc"] or 1,
            "stdout": node_result["stdout"],
            "stderr": node_result["stderr"],
            "host_ifname": host_ifname,
            "node_name": node_name,
            "node_agent_pod": None,
        }
    agent_result = _run_shell(
        f"{KUBECTL} get pods -n nodalarc -l app=nodalarc-node-agent \
        --field-selector spec.nodeName={node_name} -o jsonpath={{.items[0].metadata.name}}",
        timeout=timeout,
    )
    node_agent_pod = agent_result["stdout"]
    if agent_result["rc"] != 0 or not node_agent_pod:
        return {
            "rc": agent_result["rc"] or 1,
            "stdout": agent_result["stdout"],
            "stderr": agent_result["stderr"],
            "host_ifname": host_ifname,
            "node_name": node_name,
            "node_agent_pod": node_agent_pod or None,
        }
    break_result = _run_shell(
        f"{KUBECTL} exec -n nodalarc {node_agent_pod} -c node-agent -- ip link set dev {host_ifname} down",
        timeout=timeout,
    )
    return {
        **break_result,
        "host_ifname": host_ifname,
        "node_name": node_name,
        "node_agent_pod": node_agent_pod,
    }


MBB_LIFECYCLE_CODE = "MBB_TEARDOWN_TERMINAL"
_OCCURRENCE_FIELDS = (
    "session_id",
    "epoch_id",
    "allocator_step",
    "snapshot_seq",
    "teardown_id",
)


def _lifecycle_occurrences(events: list[dict]) -> list[dict]:
    """The OME's MBB lifecycle records grouped into distinct occurrences.

    ``teardown_id`` alone names only the old and successor pairs and can recur,
    so an occurrence is scoped by run (session id), epoch, allocator step and
    snapshot as well. Every raw record is kept under its occurrence; a group
    with more than one record is a duplicate publication; one whose records
    disagree in their details, the terminal outcome included, is flagged as
    conflicting and counts as nothing. Nothing here changes what the runtime
    publishes."""
    groups: dict[tuple, dict] = {}
    for event in events:
        if event.get("source") != "ome" or event.get("code") != MBB_LIFECYCLE_CODE:
            continue
        details = event.get("details") or {}
        key = tuple(details.get(field) for field in _OCCURRENCE_FIELDS)
        group = groups.setdefault(
            key,
            {
                "occurrence": dict(zip(_OCCURRENCE_FIELDS, key, strict=True)),
                "gs_id": details.get("gs_id"),
                "outcomes": [],
                "records": [],
                "record_count": 0,
                "duplicate_records": False,
                "conflicting_outcomes": False,
                "conflicting_records": False,
            },
        )
        group["records"].append(event)
        group["record_count"] += 1
        group["duplicate_records"] = group["record_count"] > 1
        outcome = details.get("terminal_outcome")
        if outcome not in group["outcomes"]:
            group["outcomes"].append(outcome)
        group["conflicting_outcomes"] = len(group["outcomes"]) > 1
        first = group["records"][0].get("details") or {}
        if details != first:
            group["conflicting_records"] = True
    return list(groups.values())


def _completed_occurrences(occurrences: list[dict]) -> list[dict]:
    """Occurrences that report one outcome, teardown_completed, without conflict."""
    return [
        group
        for group in occurrences
        if group["outcomes"] == ["teardown_completed"] and not group["conflicting_records"]
    ]


def check_mbb_lifecycle_and_ops(token: str, *, wait_s: int = 180) -> dict:
    deadline = time.monotonic() + wait_s
    last_events: list[dict] = []
    while time.monotonic() < deadline:
        events = request_json("GET", "/api/v1/ops/events?limit=500", token=token)
        last_events = events
        occurrences = _lifecycle_occurrences(events)
        completed = _completed_occurrences(occurrences)
        conflicting = [group for group in occurrences if group["conflicting_records"]]
        bad = [event for event in events if event.get("code") in MBB_BAD_OPS_CODES]
        if completed or bad or conflicting:
            return {
                "result": "PASS" if completed and not bad and not conflicting else "FAIL",
                "lifecycle_record_count": sum(group["record_count"] for group in occurrences),
                "lifecycle_occurrence_count": len(occurrences),
                "completed_count": len(completed),
                "duplicate_record_groups": [
                    group for group in occurrences if group["duplicate_records"]
                ],
                "conflicting_record_groups": [
                    group for group in occurrences if group["conflicting_records"]
                ],
                "bad_ops_codes": [event.get("code") for event in bad],
                "occurrences": occurrences,
            }
        time.sleep(5)
    return {
        "result": "FAIL",
        "reason": f"No completed MBB lifecycle occurrence within {wait_s}s",
        "event_count": len(last_events),
    }


def _parse_event_time(event: dict) -> datetime | None:
    raw = event.get("timestamp")
    if not raw:
        return None
    try:
        return datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return None


def _event_at_or_after(event: dict, started_at: datetime) -> bool:
    event_time = _parse_event_time(event)
    return event_time is not None and event_time >= started_at


def _ground_links_by_gs(state: dict) -> dict[str, list[dict]]:
    links = state.get("links", [])
    if isinstance(links, dict):
        links = list(links.values())
    nodes_by_id = _nodes_by_id(state.get("nodes", []))
    by_gs: dict[str, list[dict]] = {}
    for link in links:
        if link.get("state") != "active":
            continue
        pair = _link_as_ground_sat(link, nodes_by_id)
        if pair is None:
            continue
        gs_id, _sat_id = pair
        by_gs.setdefault(gs_id, []).append(link)
    return by_gs


def _ground_node_ids(state: dict) -> list[str]:
    return sorted(n.get("node_id", "") for n in state.get("nodes", []) if _is_ground_node(n))


def _pair_separation(a: dict, b: dict) -> float:
    """Ranking key for site separation. Cross-body pairs sort above any
    same-body pair; same-body pairs rank by great-circle central angle in
    degrees (radius-independent, so it orders correctly on any body)."""
    if a["body"] != b["body"]:
        return float("inf")
    import math

    phi1 = math.radians(a["lat_deg"])
    phi2 = math.radians(b["lat_deg"])
    half_dphi = (phi2 - phi1) / 2
    half_dlam = math.radians(b["lon_deg"] - a["lon_deg"]) / 2
    h = math.sin(half_dphi) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(half_dlam) ** 2
    return math.degrees(2 * math.asin(min(1.0, math.sqrt(h))))


def _transit_pairs(
    ground_topology: dict[str, dict], srcs: list[str], ground_ids: list[str]
) -> list[tuple[str, str]]:
    """Candidate (src, dst) probe pairs that can prove inter-site transit,
    most distant first.

    Pairs sharing a site are excluded: the site LAN satisfies every
    readiness check (FIB, adjacency, ping) without a packet ever leaving
    the site, so such a pair proves nothing about the space segment. When
    the resolved topology spans bodies, only cross-body pairs qualify —
    proving an Earth-Earth path for a session that declares Earth-Luna
    reachability would silently downgrade the claim."""
    pairs: list[tuple[float, str, str]] = []
    for src in srcs:
        src_info = ground_topology.get(src)
        if src_info is None:
            continue
        for dst in ground_ids:
            dst_info = ground_topology.get(dst)
            if dst_info is None or dst == src or dst_info["site"] == src_info["site"]:
                continue
            pairs.append((_pair_separation(src_info, dst_info), src, dst))
    if len({info["body"] for info in ground_topology.values()}) > 1:
        pairs = [p for p in pairs if p[0] == float("inf")]
    pairs.sort(key=lambda t: (-t[0], t[1], t[2]))
    return [(src, dst) for _, src, dst in pairs]


def _route_egress_dev(route_stdout: str) -> str | None:
    """The kernel's chosen egress interface from `ip route get` output."""
    tokens = route_stdout.split()
    for index, token in enumerate(tokens):
        if token == "dev" and index + 1 < len(tokens):
            return tokens[index + 1]
    return None


# Sweeps stay bounded because every candidate requires a kernel query. A
# linked source and destination site are only eligible, not necessarily in
# the same active routing component, so each successive sweep starts after
# the prior window instead of permanently retrying the first distance-ranked
# pairs. This matters on the sparse polar session, where the only routable
# pair can be the closest one.
_TRANSIT_PAIRS_PER_SWEEP = 16


def _find_routed_ground_probe(
    token: str,
    *,
    protocol: str = "isis",
    wait_s: int = 180,
    ground_topology: dict[str, dict] | None = None,
    sources: list[str] | None = None,
) -> dict | None:
    """Find one ground-originated routed proof.

    With ``ground_topology`` (resolver-derived site/body/position/WAN
    facts), a PASS proves inter-site space transit: the pair spans
    different sites — cross-body when the session declares more than one
    body — and the source's kernel-chosen egress interface is one of its
    manifest-allocated space-link terminals, never the site LAN. Without
    it (legacy generated sessions), the result is explicitly marked
    ``transit_proven: False``: the LAN alone can satisfy every check.
    """
    deadline = time.monotonic() + wait_s
    no_probe_reason = "no routed ground probe candidate"
    candidate_cursor = 0
    attempts: list[dict] = []
    single_site = ground_topology is not None and (
        len({info["site"] for info in ground_topology.values()}) < 2
    )
    if single_site:
        return {
            "result": "SINGLE_SITE",
            "reason": "topology has one ground site; no inter-site transit pair exists",
        }
    while time.monotonic() < deadline:
        state = request_json("GET", "/api/v1/state", token=token)
        nodes_by_id = _nodes_by_id(state.get("nodes", []))
        ground_ids = _ground_node_ids(state)
        by_gs = _ground_links_by_gs(state)
        # ``sources`` restricts the probe's origin to the named ground nodes.
        srcs = sorted(gw for gw in by_gs if sources is None or gw in sources)
        if ground_topology is not None:
            linked_sites = {ground_topology[gw]["site"] for gw in by_gs if gw in ground_topology}
            candidates = [
                (src, dst)
                for src, dst in _transit_pairs(ground_topology, srcs, ground_ids)
                if ground_topology.get(dst, {}).get("site") in linked_sites
            ]
            if not candidates:
                no_probe_reason = (
                    "no transit-capable probe pair: no ground node with an active "
                    "space link pairs with a linked ground site elsewhere"
                )
                time.sleep(3)
                continue
            candidate_count = len(candidates)
            sweep_size = min(_TRANSIT_PAIRS_PER_SWEEP, candidate_count)
            sweep_start = candidate_cursor % candidate_count
            candidates = (candidates + candidates)[sweep_start : sweep_start + sweep_size]
            candidate_cursor = (sweep_start + sweep_size) % candidate_count
        else:
            candidates = [(src, dst) for src in srcs for dst in ground_ids if dst != src]
        for src, dst_gs in candidates:
            key = f"{src}->{dst_gs}"
            dst_ip = _published_loopback_ip(dst_gs, nodes_by_id)
            if not dst_ip:
                attempts.append(
                    {
                        "candidate": key,
                        "kind": "probe_failure",
                        "reason": f"no published router loopback for {dst_gs}",
                    }
                )
                continue
            route = _kubectl_exec(src, f"ip route get {dst_ip}", timeout=10)
            route_obs = _route_observation(route, dst_ip)
            if not route_obs["observed"]:
                attempts.append(
                    {"candidate": key, "kind": "probe_failure", "reason": route_obs["reason"]}
                )
                continue
            fib_ready = route_obs["positive"]
            egress_dev = route_obs.get("egress_dev")
            if ground_topology is not None:
                src_info = ground_topology[src]
                dst_info = ground_topology[dst_gs]
                space_egress = egress_dev in src_info["wan_ifnames"]
                separation = _pair_separation(src_info, dst_info)
                transit_fields = {
                    "transit_proven": True,
                    "src_site": src_info["site"],
                    "dst_site": dst_info["site"],
                    "src_body": src_info["body"],
                    "dst_body": dst_info["body"],
                    "separation": (
                        "cross-body" if separation == float("inf") else f"{separation:.1f}deg"
                    ),
                    "egress_dev": egress_dev,
                }
            else:
                space_egress = True
                transit_fields = {
                    "transit_proven": False,
                    "egress_dev": egress_dev,
                    "transit_note": "no resolver topology: pair may share a site LAN",
                }
            if not fib_ready or not space_egress:
                attempts.append(
                    {
                        "candidate": key,
                        "kind": "observed_negative",
                        "reason": (
                            f"{key}: no route ({route_obs['reason']})"
                            if not fib_ready
                            else f"{key}: route via {egress_dev} is not a space-link terminal"
                        ),
                        "observation": {"route": route_obs["reason"], "egress_dev": egress_dev},
                        "route_stdout": route["stdout"][-300:],
                        "route_stderr": route["stderr"][-300:],
                    }
                )
                continue
            neigh = _kubectl_exec(
                src, f"vtysh -c '{_routing_neighbor_command(protocol)}'", timeout=10
            )
            ping = _kubectl_exec(src, f"ping -c 1 -W 5 {dst_ip}", timeout=10)
            neigh_obs = _adjacency_observation(neigh, protocol)
            ping_obs = _packet_observation(ping)
            if not neigh_obs["observed"] or not ping_obs["observed"]:
                attempts.append(
                    {
                        "candidate": key,
                        "kind": "probe_failure",
                        "reason": "; ".join(
                            obs["reason"] for obs in (neigh_obs, ping_obs) if not obs["observed"]
                        ),
                    }
                )
                continue
            neighbor_up = neigh_obs["positive"]
            packet_ready = ping_obs["positive"]
            if fib_ready and neighbor_up and packet_ready and space_egress:
                return {
                    "result": "PASS",
                    "mode": "ground_to_ground",
                    "protocol": protocol,
                    "key": key,
                    "src": src,
                    "dst_gs": dst_gs,
                    "dst": dst_gs,
                    "dst_ip": dst_ip,
                    "active_ground_links": by_gs[src],
                    "fib_ready": fib_ready,
                    "neighbor_up": neighbor_up,
                    "packet_ready": packet_ready,
                    "packet_stats": ping_obs["stats"],
                    **transit_fields,
                    "route_stdout": route["stdout"],
                    "isis_stdout": neigh["stdout"],
                    "ping_stdout": ping["stdout"],
                }
            attempts.append(
                {
                    "candidate": key,
                    "kind": "observed_negative",
                    "reason": (
                        f"{key}: route via {egress_dev}, "
                        f"adjacency {'up' if neighbor_up else 'not up'}, {ping_obs['reason']}"
                    ),
                    "observation": {
                        "route": route_obs["reason"],
                        "egress_dev": egress_dev,
                        "adjacency": neigh_obs["reason"],
                        "packets": ping_obs["reason"],
                    },
                }
            )
        time.sleep(3)
    return _sweep_verdict(attempts, no_probe_reason)


def _mbb_probe_sources(perm: dict) -> list[str]:
    """Stations whose resolved MBB steady limit is one: every handover there
    is an MBB overlap, so the handover the window observes is the one under
    test. Read from the resolver's per-station facts, never from names."""
    return sorted(
        node_id
        for node_id, station in (perm.get("mbb_stations") or {}).items()
        if station.get("handover_mode") == "mbb" and station.get("steady_limit") == 1
    )


def _find_all_routed_ground_probes(token: str, perm: dict, *, protocol: str = "isis") -> list[dict]:
    """One monitored flow per limit-one MBB station: from that station to the
    most distant other-site gateway whose route leaves by a space-link terminal
    and is adjacent and answering right now. Same-site pairs never qualify."""
    state = request_json("GET", "/api/v1/state", token=token)
    nodes_by_id = _nodes_by_id(state.get("nodes", []))
    ground_ids = _ground_node_ids(state)
    by_gs = _ground_links_by_gs(state)
    topology = perm["ground_topology"]
    probes: list[dict] = []
    for src in _mbb_probe_sources(perm):
        if src not in by_gs or src not in topology:
            continue
        for _src, dst_gs in _transit_pairs(topology, [src], ground_ids):
            dst_ip = _published_loopback_ip(dst_gs, nodes_by_id)
            if not dst_ip:
                continue
            route = _kubectl_exec(src, f"ip route get {dst_ip}", timeout=10)
            route_obs = _route_observation(route, dst_ip)
            egress_dev = route_obs.get("egress_dev")
            if not (route_obs["observed"] and route_obs["positive"]):
                continue
            if egress_dev not in topology[src]["wan_ifnames"]:
                continue
            neigh = _kubectl_exec(
                src, f"vtysh -c '{_routing_neighbor_command(protocol)}'", timeout=10
            )
            ping = _kubectl_exec(src, f"ping -c 1 -W 5 {dst_ip}", timeout=10)
            neigh_obs = _adjacency_observation(neigh, protocol)
            ping_obs = _packet_observation(ping)
            if not (neigh_obs["observed"] and neigh_obs["positive"]):
                continue
            if not (ping_obs["observed"] and ping_obs["positive"]):
                continue
            probes.append(
                {
                    "mode": "ground_to_ground",
                    "protocol": protocol,
                    "key": f"{src}->{dst_gs}",
                    "src": src,
                    "dst_gs": dst_gs,
                    "dst_ip": dst_ip,
                    "src_site": topology[src]["site"],
                    "dst_site": topology[dst_gs]["site"],
                    "egress_dev": egress_dev,
                    "transit_proven": True,
                    "steady_limit": perm["mbb_stations"][src]["steady_limit"],
                    "mbb_overlap_ticks": perm["mbb_stations"][src]["mbb_overlap_ticks"],
                    "active_ground_links": by_gs[src],
                    "route_stdout": route["stdout"],
                    "isis_stdout": neigh["stdout"],
                    "ping_stdout": ping["stdout"],
                }
            )
            break
    return probes


def check_mbb_convergence_preconditions(token: str, perm: dict) -> dict:
    """The monitored flow itself is the precondition: a limit-one MBB station
    with an installed route to another site's gateway that leaves by a
    space-link terminal, adjacent and answering."""
    sources = _mbb_probe_sources(perm)
    rule = {
        "sources": sources,
        "requires": "different site, space-link egress, adjacency up, one reply",
    }
    if not sources:
        return {
            "result": "FAIL",
            "reason": "the resolved session has no MBB station with a steady limit of one",
            "probe_rule": rule,
        }
    probe = _find_routed_ground_probe(
        token, wait_s=180, ground_topology=perm["ground_topology"], sources=sources
    )
    if not probe or probe.get("result") != "PASS":
        return {
            **(probe or {"result": "FAIL", "reason": "No routed ground probe found"}),
            "result": "FAIL",
            "probe_rule": rule,
        }
    return {"result": "PASS", "probe_rule": rule, **probe}


def _sequence_ranges(seqs: list[int]) -> list[list[int]]:
    if not seqs:
        return []
    ranges: list[list[int]] = []
    start = prev = seqs[0]
    for seq in seqs[1:]:
        if seq == prev + 1:
            prev = seq
            continue
        ranges.append([start, prev])
        start = prev = seq
    ranges.append([start, prev])
    return ranges


def _mbb_packet_window_passed(output: dict, overlap: dict | None, bad_events: list[dict]) -> bool:
    """Hard-gate emulator-side MBB proof; packet loss is recorded, not hidden."""
    return (
        bool(output.get("protocol_observed"))
        and bool(overlap and overlap.get("successor_fib_ready"))
        and not bad_events
    )


def _routing_layer_outcome(overlap: dict | None) -> str:
    if overlap is None:
        return "overlap_not_sampled"
    binding = overlap.get("binding")
    if binding is not None and not binding.get("bound"):
        return binding["reason"]
    if overlap.get("successor_fib_ready"):
        return "successor_fib_ready"
    if not overlap.get("neighbor_up"):
        return "successor_adjacency_not_up"
    route_dev = overlap.get("route_dev")
    successor_if = overlap.get("successor_interface")
    if route_dev is None:
        return "no_kernel_route"
    if successor_if and route_dev != successor_if:
        return "fib_still_points_to_other_interface"
    return "successor_fib_not_ready"


def _select_terminal_probe(
    probes: list[dict],
    overlap_by_key: dict[str, dict],
) -> tuple[str, dict | None] | tuple[None, None]:
    if not probes:
        return None, None
    with_ready_fib = [
        probe
        for probe in probes
        if (overlap_by_key.get(probe["key"]) or {}).get("successor_fib_ready")
    ]
    with_overlap = [probe for probe in probes if overlap_by_key.get(probe["key"])]
    selected = (with_ready_fib or with_overlap or probes)[0]
    return selected["key"], overlap_by_key.get(selected["key"])


def _successor_interface(active_links: list[dict]) -> str | None:
    gained = [link for link in active_links if link.get("link_reason") == "vis_gained"]
    if len(gained) == 1:
        return gained[0].get("interface_a")
    return None


def _isis_neighbor_rows(stdout: str) -> list[dict]:
    """Every adjacency row of `show isis neighbor`, one per neighbor and
    interface, so the successor's and the incumbent's adjacencies are read
    individually instead of as "some adjacency is Up"."""
    rows: list[dict] = []
    for line in stdout.splitlines():
        parts = line.split()
        if len(parts) < 4 or parts[0] in ("System", "Area") or not parts[2].isdigit():
            continue
        rows.append(
            {"system_id": parts[0], "interface": parts[1], "level": parts[2], "state": parts[3]}
        )
    return rows


def _adjacency_on(rows: list[dict], interface: str | None) -> dict | None:
    if interface is None:
        return None
    return next((row for row in rows if row["interface"] == interface), None)


class _PingObserver:
    """One monitored flow's packets: BusyBox ping in the published container,
    every output line stamped with the harness's receipt time as it arrives.
    BusyBox ping exits on the first unroutable answer, so whenever an instance
    exits while the window is still open, early or at its count, the observer
    starts the next instance, up to a bound, and the exit and the gap between
    instances are stamped records inside a continuing observation. Every
    instance's lines, exit status and identity are kept; sequences restart
    with every instance. Receipt stamps are not send stamps."""

    def __init__(
        self,
        key: str,
        target: WorkloadTarget,
        dst_ip: str,
        *,
        count: int,
        interval_s: float,
        max_restarts: int = 20,
        restart_delay_s: float = 0.5,
    ) -> None:
        import threading

        self.key = key
        self.target = target
        self.dst_ip = dst_ip
        self.count = count
        self.interval_s = interval_s
        self.max_restarts = max_restarts
        self.restart_delay_s = restart_delay_s
        self.instances: list[dict] = []
        self.restart_limit_reached = False
        self._stop = threading.Event()
        self._proc = None
        self._thread = threading.Thread(target=self._run, name=f"ping-{key}", daemon=True)

    @property
    def command(self) -> str:
        return (
            f"{KUBECTL} exec -n {self.target.namespace} {self.target.pod_name} "
            f"-c {self.target.container} -- "
            f"ping -c {self.count} -i {self.interval_s} -W 1 {self.dst_ip}"
        )

    def start(self) -> None:
        self._thread.start()

    def finished(self) -> bool:
        return not self._thread.is_alive()

    def _run(self) -> None:
        instance = 0
        while not self._stop.is_set():
            started = self._run_instance(instance)
            if not started or self._stop.is_set():
                break
            if instance >= self.max_restarts:
                self.restart_limit_reached = True
                break
            instance += 1
            self._stop.wait(self.restart_delay_s)

    def _run_instance(self, instance: int) -> bool:
        """Run one instance to its end; False when it could not be started,
        which is recorded on the instance as an observer failure."""
        import subprocess
        import threading

        record = {
            "instance": instance,
            "command": self.command,
            "started_wall": datetime.now(UTC).isoformat(),
            "ended_wall": None,
            "returncode": None,
            "stopped_by_harness": False,
            "startup_error": None,
            "lines": [],
        }
        self.instances.append(record)
        try:
            proc = subprocess.Popen(
                self.command,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                shell=True,
                start_new_session=True,
            )
        except Exception as exc:
            record["startup_error"] = f"{type(exc).__name__}: {exc}"
            record["ended_wall"] = datetime.now(UTC).isoformat()
            return False
        self._proc = proc
        readers = [
            threading.Thread(target=self._pump, args=(proc.stdout, "stdout", record), daemon=True),
            threading.Thread(target=self._pump, args=(proc.stderr, "stderr", record), daemon=True),
        ]
        for reader in readers:
            reader.start()
        proc.wait()
        for reader in readers:
            reader.join(timeout=5)
        record["returncode"] = proc.returncode
        record["ended_wall"] = datetime.now(UTC).isoformat()
        record["stopped_by_harness"] = self._stop.is_set()
        self._proc = None
        return True

    @staticmethod
    def _pump(stream, name: str, record: dict) -> None:
        if stream is None:
            return
        for line in iter(stream.readline, ""):
            record["lines"].append(
                {
                    "receipt_wall": datetime.now(UTC).isoformat(),
                    "stream": name,
                    "text": line.rstrip("\n"),
                }
            )
        stream.close()

    def stop(self, *, grace_s: float = 15.0) -> None:
        import signal

        self._stop.set()
        proc = self._proc
        if proc is not None and proc.poll() is None:
            try:
                os.killpg(proc.pid, signal.SIGINT)
            except ProcessLookupError, PermissionError:
                pass
        self._thread.join(timeout=grace_s)
        proc = self._proc
        if proc is not None and proc.poll() is None:
            proc.kill()
            self._thread.join(timeout=5)


RECEIPT_STAMP_NOTE = (
    "receipt stamps are the harness's arrival times, not send times; contiguity of "
    "retained replies is not evidence of continuity"
)


def _instance_observation(record: dict) -> dict:
    """What one ping instance showed, in distinct classes: replies (every one,
    stamped, with the sequences missing between the retained ones), explicit
    no-route answers printed by ping, observer failures (an instance that
    could not start, any other ping error, anything else on stderr such as a
    kubectl or exec diagnostic), and nothing at all. The original stamped
    lines travel with the interpretation."""
    replies: list[dict] = []
    unreachable: list[dict] = []
    observer_failures: list[dict] = []
    stdout_text: list[str] = []
    if record.get("startup_error"):
        observer_failures.append(
            {
                "receipt_wall": record["started_wall"],
                "stream": "observer",
                "text": f"instance could not start: {record['startup_error']}",
            }
        )
    for line in record["lines"]:
        text = line["text"]
        if line["stream"] == "stdout":
            stdout_text.append(text)
        stripped = text.strip()
        if stripped.startswith("ping:"):
            answer = next((a for a in ROUTING_UNREACHABLE_ANSWERS if a in stripped), None)
            if answer is not None:
                unreachable.append({**line, "answer": answer})
            else:
                observer_failures.append({**line, "kind": "unrecognized ping error"})
            continue
        if line["stream"] == "stdout" and "bytes from" in text and "seq=" in text:
            try:
                seq = int(text.split("seq=", 1)[1].split(None, 1)[0])
            except ValueError:
                continue
            replies.append({"seq": seq, "receipt_wall": line["receipt_wall"], "text": text})
        elif line["stream"] == "stderr" and stripped:
            observer_failures.append({**line, "kind": "observer diagnostic"})
    seqs = sorted(reply["seq"] for reply in replies)
    missing_within = sorted(set(range(seqs[0], seqs[-1] + 1)) - set(seqs)) if seqs else []
    statistics = _parse_ping_statistics("\n".join(stdout_text))
    counted_loss = False
    if statistics is not None:
        if not statistics["consistent"]:
            observer_failures.append(
                {
                    "receipt_wall": record["ended_wall"] or record["started_wall"],
                    "stream": "stdout",
                    "text": statistics["stats"],
                    "kind": f"inconsistent ping statistics: {statistics['problem']}",
                }
            )
        else:
            counted_loss = statistics["received"] < statistics["transmitted"]
    return {
        "instance": record["instance"],
        "started_wall": record["started_wall"],
        "ended_wall": record["ended_wall"],
        "returncode": record["returncode"],
        "stopped_by_harness": record["stopped_by_harness"],
        "startup_error": record.get("startup_error"),
        "reply_count": len(replies),
        "replies": replies,
        "reply_seq_ranges": _sequence_ranges(seqs),
        "missing_seq_ranges_within_retained_replies": _sequence_ranges(missing_within),
        "measured_loss": bool(missing_within) or counted_loss,
        "unreachable_answers": unreachable,
        "observer_failures": observer_failures,
        "statistics": statistics,
        "protocol_observed": bool(replies or unreachable) and not observer_failures,
        "raw_lines": record["lines"],
    }


def _probe_packet_observation(instances: list[dict]) -> dict:
    """One flow's packet evidence across every ping instance of a window, in
    four classes kept apart: measured loss (sequences missing between retained
    replies), explicit no-route answers, observer failures, and unobserved
    intervals between instances. An observer failure makes the measurement
    invalid: it never qualifies the gate, whatever else was seen."""
    observations = [_instance_observation(record) for record in instances]
    restart_gaps = []
    for earlier, later in zip(observations, observations[1:], strict=False):
        gap_s = None
        if earlier["ended_wall"] and later["started_wall"]:
            gap_s = (
                datetime.fromisoformat(later["started_wall"])
                - datetime.fromisoformat(earlier["ended_wall"])
            ).total_seconds()
        restart_gaps.append(
            {
                "after_instance": earlier["instance"],
                "ended_wall": earlier["ended_wall"],
                "next_started_wall": later["started_wall"],
                "unobserved_gap_s": gap_s,
            }
        )
    reply_count = sum(item["reply_count"] for item in observations)
    unreachable = [answer for item in observations for answer in item["unreachable_answers"]]
    failures = [failure for item in observations for failure in item["observer_failures"]]
    measured_loss = any(item["measured_loss"] for item in observations)
    if failures:
        outcome = "observer_error"
    elif unreachable:
        outcome = "routing_unreachable"
    elif measured_loss:
        outcome = "measured_loss"
    elif reply_count == 0:
        outcome = "no_replies"
    else:
        outcome = "no_loss_measured"
    return {
        "instances": observations,
        "instance_count": len(observations),
        "restart_gaps": restart_gaps,
        "unobserved_gap_count": len(restart_gaps),
        "reply_count": reply_count,
        "measured_loss": measured_loss,
        "unreachable_answers": unreachable,
        "observer_failures": failures,
        "measurement_valid": not failures,
        "packet_outcome": outcome,
        "protocol_observed": bool(reply_count or unreachable) and not failures,
        "stats": (
            f"{reply_count} replies in {len(observations)} instance(s); "
            f"measured loss {'yes' if measured_loss else 'no'}; "
            f"{len(unreachable)} unreachable answer(s); {len(failures)} observer failure(s); "
            f"{len(restart_gaps)} unobserved interval(s) between instances"
        ),
        "note": RECEIPT_STAMP_NOTE,
    }


def _bracket(token: str, src: str) -> dict:
    """One VS-API reading of the station's links and the decision epoch, with
    the read's own stamps: the OME's snapshot sequence, sim time and epoch as
    the VS-API held them at that moment."""
    started = datetime.now(UTC)
    state = request_json("GET", "/api/v1/state", token=token)
    decisions = request_json("GET", "/api/v1/ground-link-decisions", token=token)
    return {
        "read_started_wall": started.isoformat(),
        "read_finished_wall": datetime.now(UTC).isoformat(),
        "session_id": state.get("session_id"),
        "sim_time": state.get("sim_time"),
        "decision_snapshot_seq": decisions.get("snapshot_seq"),
        "decision_sim_time": decisions.get("sim_time"),
        "epoch_id": decisions.get("epoch_id"),
        "allocation_events": decisions.get("allocation_events", []),
        "active_ground_links": _ground_links_by_gs(state).get(src, []),
    }


_KERNEL_READ_MARK = "===nodalarc-read==="


def _gs_interface(link: dict, gs_id: str) -> str | None:
    return link.get("interface_a") if link.get("node_a") == gs_id else link.get("interface_b")


def _overlap_interfaces(links: list[dict], gs_id: str) -> dict | None:
    """The incumbent and successor of the overlap the station's links show,
    with the satellite behind the incumbent; None when no overlap is shown."""
    gained = [link for link in links if link.get("link_reason") == "vis_gained"]
    if len(links) < 2 or len(gained) != 1:
        return None
    successor = gained[0]
    incumbent = next(
        (
            link
            for link in links
            if link is not successor
            and (
                link.get("scheduling_state") == "teardown"
                or link.get("teardown_remaining_ticks") is not None
                or link.get("successor_pair")
            )
        ),
        None,
    )
    if incumbent is None:
        return None
    incumbent_sat = (
        incumbent.get("node_b") if incumbent.get("node_a") == gs_id else incumbent.get("node_a")
    )
    return {
        "incumbent_interface": _gs_interface(incumbent, gs_id),
        "successor_interface": _gs_interface(successor, gs_id),
        "incumbent_sat": incumbent_sat,
    }


def _parse_link_line(text: str) -> dict | None:
    """Flags and operational state from one `ip -o link show` line."""
    line = text.strip()
    if "<" not in line or ">" not in line:
        return None
    flags = line.split("<", 1)[1].split(">", 1)[0].split(",")
    tokens = line.split()
    state = tokens[tokens.index("state") + 1] if "state" in tokens else None
    return {"flags": flags, "state": state, "lower_up": "LOWER_UP" in flags, "raw": line}


def _kernel_read_command(dst_ip: str, incumbent_if: str, successor_if: str, protocol: str) -> str:
    """One command in the pod: the flow's route first, then the incumbent's
    and the successor's link state, then the neighbor table. What follows the
    route in the same command was read after the route."""
    neighbor = _routing_neighbor_command(protocol)
    return (
        f'sh -c "ip route get {dst_ip}; echo {_KERNEL_READ_MARK}; '
        f"ip -o link show dev {incumbent_if}; echo {_KERNEL_READ_MARK}; "
        f"ip -o link show dev {successor_if}; echo {_KERNEL_READ_MARK}; "
        f"vtysh -c '{neighbor}'\""
    )


def _split_kernel_read(stdout: str) -> list[str] | None:
    parts = [part.strip("\n") for part in stdout.split(_KERNEL_READ_MARK)]
    return parts if len(parts) == 4 else None


def _sample_station(token: str, src: str, probes: list[dict], *, protocol: str = "isis") -> dict:
    """One receipt-stamped reading of a station. The kernel reads are bracketed
    by two VS-API readings, ``pre`` and ``post``, which bound the decision
    timeline the VS-API had received; they cannot order the kernel reads
    against the teardown, because the VS-API delivers snapshots with a lag.
    When ``pre`` shows an overlap, each flow's route is read in one command
    together with the incumbent's link state and the neighbor table, read
    after the route: those are the kernel's own word on whether the incumbent
    was still up when the route was read."""
    started = datetime.now(UTC)
    try:
        pre = _bracket(token, src)
        overlap = _overlap_interfaces(pre["active_ground_links"], src)
        kernel_started = datetime.now(UTC)
        routes: dict[str, dict] = {}
        kernel_after_route: dict[str, dict] = {}
        neighbor_stdout: str | None = None
        for probe in probes:
            if overlap is None:
                route = _kubectl_exec(src, f"ip route get {probe['dst_ip']}", timeout=10)
                observation = _route_observation(route, probe["dst_ip"])
                routes[probe["key"]] = {
                    **observation,
                    "stdout": route["stdout"],
                    "stderr": route["stderr"],
                }
                continue
            command = _kernel_read_command(
                probe["dst_ip"],
                overlap["incumbent_interface"],
                overlap["successor_interface"],
                protocol,
            )
            result = _kubectl_exec(src, command, timeout=15)
            parts = _split_kernel_read(result["stdout"])
            if parts is None:
                routes[probe["key"]] = {
                    **_route_observation(result, probe["dst_ip"]),
                    "stdout": result["stdout"],
                    "stderr": result["stderr"],
                }
                kernel_after_route[probe["key"]] = {
                    **overlap,
                    "error": "combined kernel read did not return its four parts",
                    "raw": result["stdout"],
                    "stderr": result["stderr"],
                }
                continue
            route_text, incumbent_text, successor_text, neighbor_text = parts
            routes[probe["key"]] = {
                **_route_observation(
                    {**result, "stdout": route_text, "stderr": ""}, probe["dst_ip"]
                ),
                "stdout": route_text,
                "stderr": result["stderr"],
            }
            rows_after = _isis_neighbor_rows(neighbor_text)
            kernel_after_route[probe["key"]] = {
                **overlap,
                "incumbent_link": _parse_link_line(incumbent_text),
                "successor_link": _parse_link_line(successor_text),
                "neighbors_after_route": rows_after,
                "incumbent_adjacency_after_route": _adjacency_on(
                    rows_after, overlap["incumbent_interface"]
                ),
                "raw": result["stdout"],
            }
            neighbor_stdout = neighbor_text
        if neighbor_stdout is None:
            neigh = _kubectl_exec(
                src, f"vtysh -c '{_routing_neighbor_command(protocol)}'", timeout=10
            )
            neighbor_stdout = neigh["stdout"]
            neighbor_observation = _adjacency_observation(neigh, protocol)
        else:
            neighbor_observation = _adjacency_observation(
                {"rc": 0, "stdout": neighbor_stdout, "stderr": ""}, protocol
            )
        kernel_finished = datetime.now(UTC)
        post = _bracket(token, src)
    except Exception as exc:  # a read that failed is a stamped sample error, never an escape
        return {
            "read_started_wall": started.isoformat(),
            "read_finished_wall": datetime.now(UTC).isoformat(),
            "src": src,
            "sample_error": f"{type(exc).__name__}: {exc}",
        }
    finished = datetime.now(UTC)
    links = pre["active_ground_links"]
    return {
        "read_started_wall": started.isoformat(),
        "read_finished_wall": finished.isoformat(),
        "src": src,
        "session_id": pre["session_id"],
        "sim_time": pre["sim_time"],
        "decision_snapshot_seq": pre["decision_snapshot_seq"],
        "decision_sim_time": pre["decision_sim_time"],
        "epoch_id": pre["epoch_id"],
        "allocation_events": pre["allocation_events"],
        "active_ground_links": links,
        "successor_interface": _successor_interface(links),
        "kernel_read_started_wall": kernel_started.isoformat(),
        "kernel_read_finished_wall": kernel_finished.isoformat(),
        "neighbors": _isis_neighbor_rows(neighbor_stdout),
        "neighbor_observation": neighbor_observation,
        "isis_stdout": neighbor_stdout,
        "routes": routes,
        "kernel_after_route": kernel_after_route,
        "pre": pre,
        "post": post,
    }


def _sorted_pair(link: dict) -> list[str]:
    return sorted([str(link.get("node_a", "")), str(link.get("node_b", ""))])


def _handover_id(teardown_pair: list[str] | None, successor_pair: list[str] | None) -> str:
    return f"{teardown_pair}->{successor_pair}"


def _incumbent_link_down(link_events: list[dict], teardown_pair: list[str] | None) -> dict | None:
    """The Scheduler's LinkDown record for the incumbent pair, if retained."""
    if teardown_pair is None:
        return None
    for event in link_events:
        if not isinstance(event, dict) or event.get("event_type") != "LinkDown":
            continue
        if sorted([str(event.get("node_a", "")), str(event.get("node_b", ""))]) == teardown_pair:
            return event
    return None


def _bind_overlap_to_terminal(overlap: dict, event: dict, link_events: list[dict]) -> dict:
    """Whether an overlap sample proves the overlap of the teardown that was
    graded. The sample must show that handover's own pairs and belong to the
    same run and epoch as the terminal event on both of its VS-API readings.
    Its route must have been read before the teardown was enacted on the
    station's kernel: the VS-API readings cannot establish that, since they
    deliver the OME's snapshots with a lag, so the proof is the kernel's own,
    read in the same command right after the route: the incumbent's link still
    carrying LOWER_UP in state UP and the incumbent satellite's adjacency still
    Up on that interface. A teardown is enacted by dropping the incumbent's
    carrier, and any route change it causes follows that drop, so a route read
    while the carrier was still up preceded the enactment. Anything less, a
    dropped or unreadable incumbent, another satellite on its interface, a
    missing identity, a run or epoch mismatch, or a pre reading that already
    carried the teardown, is reported as uncertain with the evidence kept."""
    details = event.get("details") or {}
    old_pair = sorted(str(node) for node in (details.get("old_pair") or []))
    successor_pair = sorted(str(node) for node in (details.get("successor_pair") or []))
    terminal_handover = _handover_id(old_pair, successor_pair)
    teardown_pair = overlap.get("teardown_pair")
    if teardown_pair is None:
        return {
            "bound": False,
            "reason": "overlap_handover_unidentified",
            "sample_handover": overlap.get("handover_id"),
            "terminal_handover": terminal_handover,
        }
    if teardown_pair != old_pair or overlap.get("successor_pair") != successor_pair:
        return {
            "bound": False,
            "reason": "overlap_of_another_handover",
            "sample_handover": overlap.get("handover_id"),
            "terminal_handover": terminal_handover,
        }
    pre, post = overlap.get("pre") or {}, overlap.get("post") or {}
    identity = {
        "pre_session_id": pre.get("session_id"),
        "post_session_id": post.get("session_id"),
        "pre_epoch_id": pre.get("epoch_id"),
        "post_epoch_id": post.get("epoch_id"),
        "terminal_session_id": details.get("session_id"),
        "terminal_epoch_id": details.get("epoch_id"),
    }
    if any(value is None for value in identity.values()):
        return {"bound": False, "reason": "overlap_identity_missing", **identity}
    if not (
        identity["pre_session_id"] == identity["post_session_id"] == identity["terminal_session_id"]
        and identity["pre_epoch_id"] == identity["post_epoch_id"] == identity["terminal_epoch_id"]
    ):
        return {"bound": False, "reason": "overlap_identity_mismatch", **identity}
    terminal_seq = details.get("snapshot_seq")
    terminal_sim = details.get("master_sim_time")
    link_down = _incumbent_link_down(link_events, teardown_pair)
    kernel = overlap.get("kernel_after_route") or {}
    incumbent_link = kernel.get("incumbent_link") or {}
    incumbent_adjacency = kernel.get("incumbent_adjacency_after_route") or {}
    evidence = {
        **identity,
        "pre_decision_snapshot_seq": pre.get("decision_snapshot_seq"),
        "post_decision_snapshot_seq": post.get("decision_snapshot_seq"),
        "pre_sim_time": pre.get("sim_time"),
        "post_sim_time": post.get("sim_time"),
        "terminal_snapshot_seq": terminal_seq,
        "terminal_master_sim_time": terminal_sim,
        "incumbent_link_down_sim_time": link_down.get("sim_time") if link_down else None,
        "kernel_read_started_wall": overlap.get("kernel_read_started_wall"),
        "kernel_read_finished_wall": overlap.get("kernel_read_finished_wall"),
        "incumbent_interface": kernel.get("incumbent_interface"),
        "incumbent_sat": kernel.get("incumbent_sat"),
        "incumbent_link_after_route": incumbent_link or None,
        "incumbent_adjacency_after_route": incumbent_adjacency or None,
        "kernel_read_error": kernel.get("error"),
    }
    try:
        pre_before = (
            pre.get("decision_snapshot_seq") is not None
            and terminal_seq is not None
            and int(pre["decision_snapshot_seq"]) < int(terminal_seq)
            and pre.get("sim_time") is not None
            and terminal_sim is not None
            and _parse_api_datetime(str(pre["sim_time"])) < _parse_api_datetime(str(terminal_sim))
        )
    except TypeError, ValueError:
        pre_before = False
    if not pre_before:
        return {"bound": False, "reason": "overlap_ordering_uncertain", **evidence}
    incumbent_up = bool(incumbent_link.get("lower_up")) and incumbent_link.get("state") == "UP"
    adjacency_held = incumbent_adjacency.get("state") == "Up" and incumbent_adjacency.get(
        "system_id"
    ) == kernel.get("incumbent_sat")
    if not (incumbent_up and adjacency_held):
        return {"bound": False, "reason": "overlap_ordering_uncertain", **evidence}
    return {
        "bound": True,
        "reason": "the incumbent was still up on the kernel when the route was read",
        **evidence,
    }


def _overlap_gate_fields(sample: dict, probe: dict) -> dict | None:
    """The gate's overlap input, read from one sample in which the station
    holds two active ground links with one successor: the kernel route's
    egress for the monitored flow and the successor's own adjacency. None when
    the sample shows no overlap."""
    links = sample.get("active_ground_links") or []
    if len(links) < 2:
        return None
    successor_if = _successor_interface(links)
    if successor_if is None:
        return None
    route = (sample.get("routes") or {}).get(probe["key"]) or {}
    route_dev = route.get("egress_dev")
    rows = sample.get("neighbors") or []
    successor_row = _adjacency_on(rows, successor_if)
    incumbent_rows = [row for row in rows if row["interface"] != successor_if]
    successor_link = next(link for link in links if link.get("link_reason") == "vis_gained")
    teardown_link = next(
        (
            link
            for link in links
            if link is not successor_link
            and (
                link.get("scheduling_state") == "teardown"
                or link.get("teardown_remaining_ticks") is not None
                or link.get("successor_pair")
            )
        ),
        None,
    )
    teardown_pair = _sorted_pair(teardown_link) if teardown_link else None
    successor_pair = _sorted_pair(successor_link)
    return {
        "handover_id": _handover_id(teardown_pair, successor_pair),
        "teardown_pair": teardown_pair,
        "successor_pair": successor_pair,
        "session_id": sample.get("session_id"),
        "epoch_id": sample.get("epoch_id"),
        "sim_time": sample.get("sim_time"),
        "read_started_wall": sample.get("read_started_wall"),
        "read_finished_wall": sample.get("read_finished_wall"),
        "kernel_read_started_wall": sample.get("kernel_read_started_wall"),
        "kernel_read_finished_wall": sample.get("kernel_read_finished_wall"),
        "decision_snapshot_seq": sample.get("decision_snapshot_seq"),
        "pre": sample.get("pre"),
        "post": sample.get("post"),
        "kernel_after_route": (sample.get("kernel_after_route") or {}).get(probe["key"]),
        "active_ground_links": links,
        "successor_interface": successor_if,
        "route_dev": route_dev,
        "successor_fib_ready": bool(
            route.get("observed") and route.get("positive") and route_dev == successor_if
        ),
        "successor_adjacency": successor_row,
        "neighbor_up": bool(successor_row and successor_row["state"] == "Up"),
        "incumbent_adjacencies": incumbent_rows,
        "isis_stdout": sample.get("isis_stdout"),
        "fib_stdout": route.get("stdout"),
    }


def _event_identity(event: dict) -> str:
    seq = event.get("seq")
    if seq is not None:
        return f"seq:{seq}"
    return json.dumps(event, sort_keys=True, default=str)


def _link_events_for(token: str, nodes: set[str], *, start_sim: str | None) -> list[dict]:
    """The Scheduler's LinkUp and LinkDown records touching the monitored
    stations since the window began, with their original sim and wall times:
    the successor's LinkUp and the incumbent's LinkDown, as published after
    proof, not as inferred."""
    query = f"/api/v1/links?start={start_sim}" if start_sim else "/api/v1/links"
    try:
        events = request_json("GET", query, token=token)
    except Exception as exc:
        return [{"link_events_error": str(exc)}]
    return [
        event
        for event in events
        if isinstance(event, dict)
        and (event.get("node_a") in nodes or event.get("node_b") in nodes)
    ]


def _run_mbb_packet_window(
    token: str,
    perm: dict,
    *,
    count: int = 1200,
    interval_s: float = 0.2,
    cadence_s: float = 1.0,
    post_terminal_s: float = 10.0,
) -> dict:
    """One packet window over every monitored flow, recorded as a timeline.

    Per second, and at every event, the window samples each monitored station
    (links, adjacencies, routes) and reads the ops events; the ping observers
    stream their lines with receipt stamps and restart after early exits.
    Allocator decisions, Scheduler link events, routing observations and
    packets stay distinct records. The gate's overlap input is the first
    sample that shows the overlap, taken before the teardown event arrived; a
    later sample never satisfies it retroactively. The window keeps sampling
    for ``post_terminal_s`` after the teardown event so the route and the
    packets after the teardown are on the same timeline, as information."""
    probes = _find_all_routed_ground_probes(token, perm)
    if not probes:
        return {
            "result": "FAIL",
            "reason": "No routed ground probe from a limit-one MBB station",
            "probe_rule": {"sources": _mbb_probe_sources(perm)},
        }

    started_at = datetime.now(UTC)
    window_s = min(max(count * interval_s + 30, 120), 300)
    deadline = time.monotonic() + window_s
    probe_by_key = {probe["key"]: probe for probe in probes}
    probes_by_src: dict[str, list[dict]] = {}
    for probe in probes:
        probes_by_src.setdefault(probe["src"], []).append(probe)

    targets: dict[str, WorkloadTarget] = {}
    for probe in probes:
        target, error = _workload_target(probe["src"])
        if target is None:
            return {
                "result": "FAIL",
                "failure_kind": "probe",
                "reason": f"workload target for {probe['src']}: {error}",
                "probes": probes,
            }
        targets[probe["key"]] = target

    observers = {
        probe["key"]: _PingObserver(
            probe["key"], targets[probe["key"]], probe["dst_ip"], count=count, interval_s=interval_s
        )
        for probe in probes
    }
    for observer in observers.values():
        observer.start()

    samples: list[dict] = []
    ops_events: dict[str, dict] = {}
    bad_events: list[dict] = []
    # The first sighting of every overlap, per flow and per handover identity,
    # taken before any teardown event for that station arrived.
    overlap_by_key: dict[str, dict[str, dict]] = {}
    terminal_by_src: dict[str, dict] = {}
    terminal_receipt_mono: float | None = None
    missed_intervals = 0
    collection_error: str | None = None
    next_due = time.monotonic()
    try:
        while time.monotonic() < deadline:
            for src, probes_for_src in probes_by_src.items():
                sample = _sample_station(token, src, probes_for_src)
                sample["index"] = len(samples)
                samples.append(sample)
                if sample.get("sample_error") or src in terminal_by_src:
                    continue
                for probe in probes_for_src:
                    gate = _overlap_gate_fields(sample, probe)
                    if gate is None:
                        continue
                    sightings = overlap_by_key.setdefault(probe["key"], {})
                    sightings.setdefault(
                        gate["handover_id"], {**gate, "sample_index": sample["index"]}
                    )
            try:
                events = request_json("GET", "/api/v1/ops/events?limit=500", token=token)
            except Exception as exc:
                samples.append(
                    {
                        "index": len(samples),
                        "read_started_wall": datetime.now(UTC).isoformat(),
                        "sample_error": f"ops events read failed: {exc}",
                    }
                )
                events = []
            receipt = datetime.now(UTC).isoformat()
            for event in events:
                if not _event_at_or_after(event, started_at):
                    continue
                identity = _event_identity(event)
                if identity in ops_events:
                    continue
                ops_events[identity] = {**event, "receipt_wall": receipt}
                if event.get("code") in MBB_BAD_OPS_CODES:
                    bad_events.append(event)
                details = event.get("details") or {}
                src = details.get("gs_id")
                if (
                    event.get("source") == "ome"
                    and event.get("code") == MBB_LIFECYCLE_CODE
                    and src in probes_by_src
                    and details.get("terminal_outcome") == "teardown_completed"
                    and src not in terminal_by_src
                ):
                    terminal_by_src[src] = {
                        "event": event,
                        "receipt_wall": receipt,
                        "sample_index_at_receipt": len(samples),
                    }
                    if terminal_receipt_mono is None:
                        terminal_receipt_mono = time.monotonic()
            if terminal_receipt_mono is not None:
                if time.monotonic() - terminal_receipt_mono >= post_terminal_s:
                    break
            if all(observer.finished() for observer in observers.values()):
                break
            next_due += cadence_s
            now = time.monotonic()
            if now > next_due:
                skipped = int((now - next_due) // cadence_s) + 1
                missed_intervals += skipped
                next_due += skipped * cadence_s
            else:
                time.sleep(next_due - now)
    except Exception as exc:  # the collector failed; what it collected is kept
        collection_error = f"{type(exc).__name__}: {exc}"
    finally:
        for observer in observers.values():
            observer.stop()
    ended_at = datetime.now(UTC)

    first_sim = next((sample.get("sim_time") for sample in samples if sample.get("sim_time")), None)
    link_events = _link_events_for(token, set(probes_by_src), start_sim=first_sim)
    lifecycle = _lifecycle_occurrences(list(ops_events.values()))

    # Each flow's overlap sample is the first sighting of the graded teardown's
    # own handover, bound to that teardown by pairs and ordering; anything
    # else is recorded but qualifies nothing.
    graded_overlap: dict[str, dict | None] = {}
    outputs: dict[str, dict] = {}
    for key, observer in observers.items():
        probe = probe_by_key[key]
        terminal = terminal_by_src.get(probe["src"])
        overlap = None
        if terminal is not None:
            details = terminal["event"].get("details") or {}
            wanted = _handover_id(
                sorted(str(n) for n in (details.get("old_pair") or [])),
                sorted(str(n) for n in (details.get("successor_pair") or [])),
            )
            sightings = overlap_by_key.get(key, {})
            candidate = sightings.get(wanted) or next(iter(sightings.values()), None)
            if candidate is not None:
                binding = _bind_overlap_to_terminal(candidate, terminal["event"], link_events)
                overlap = {
                    **candidate,
                    "binding": binding,
                    "successor_fib_ready": bool(
                        binding["bound"] and candidate["successor_fib_ready"]
                    ),
                }
        graded_overlap[key] = overlap
        post_teardown_route = None
        if terminal is not None and overlap is not None and overlap["binding"]["bound"]:
            for sample in samples[terminal["sample_index_at_receipt"] :]:
                route = (sample.get("routes") or {}).get(key) or {}
                if route.get("egress_dev") == overlap["successor_interface"]:
                    post_teardown_route = {
                        "sample_index": sample["index"],
                        "sim_time": sample.get("sim_time"),
                        "read_finished_wall": sample.get("read_finished_wall"),
                        "egress_dev": route.get("egress_dev"),
                        "note": "information only; a post-teardown sample never satisfies the overlap requirement",
                    }
                    break
        outputs[key] = {
            **_probe_packet_observation(observer.instances),
            "command": observer.command,
            "restart_limit_reached": observer.restart_limit_reached,
            "overlap_observation": overlap,
            "terminal_observation": (
                None
                if terminal is None
                else {
                    "event_timestamp": terminal["event"].get("timestamp"),
                    "receipt_wall": terminal["receipt_wall"],
                    "sample_index_at_receipt": terminal["sample_index_at_receipt"],
                    "routing_layer_outcome": _routing_layer_outcome(overlap),
                }
            ),
            "post_teardown_route_observation": post_teardown_route,
        }

    collector = {
        "cadence_s": cadence_s,
        "sample_count": len(samples),
        "missed_intervals": missed_intervals,
        "collection_error": collection_error,
        "window_s": window_s,
        "started_wall": started_at.isoformat(),
        "ended_wall": ended_at.isoformat(),
        "post_terminal_s": post_terminal_s,
        "note": RECEIPT_STAMP_NOTE,
    }
    retained = {
        "probes": probes,
        "probe_outputs": outputs,
        "overlap_sightings_by_key": overlap_by_key,
        "timeline": samples,
        "ops_events": list(ops_events.values()),
        "link_events": link_events,
        "lifecycle_occurrences": lifecycle,
        "bad_ops_codes": [event.get("code") for event in bad_events],
        "collector": collector,
    }
    if not terminal_by_src:
        return {
            "result": "FAIL",
            "reason": (
                f"collection failed: {collection_error}"
                if collection_error
                else "No probed station completed an MBB teardown during the packet window"
            ),
            "terminal_gs_ids": [],
            **retained,
        }

    terminal_src = min(terminal_by_src, key=lambda src: terminal_by_src[src]["receipt_wall"])
    selected_key, overlap = _select_terminal_probe(probes_by_src[terminal_src], graded_overlap)
    if overlap is None:
        overlap = {
            "routing_layer_outcome": "overlap_not_sampled",
            "successor_fib_ready": False,
        }
    output = outputs[selected_key]
    passed = _mbb_packet_window_passed(output, overlap, bad_events) and collection_error is None
    probe = probe_by_key[selected_key]
    return {
        "result": "PASS" if passed else "FAIL",
        **({"reason": f"collection failed: {collection_error}"} if collection_error else {}),
        "src": probe["src"],
        "dst_gs": probe["dst_gs"],
        "dst_ip": probe["dst_ip"],
        "count": count,
        "interval_s": interval_s,
        "stats": output["stats"],
        "packet_outcome": output["packet_outcome"],
        "protocol_observed": output["protocol_observed"],
        "reply_count": output["reply_count"],
        "overlap_required": True,
        "overlap_ready": bool(overlap.get("successor_fib_ready")),
        "packet_loss_policy": "recorded_not_gated",
        "overlap_proof": overlap,
        "routing_layer_outcome": _routing_layer_outcome(overlap),
        "terminal_event": terminal_by_src[terminal_src]["event"],
        "terminal_observation": output["terminal_observation"],
        "post_teardown_route_observation": output["post_teardown_route_observation"],
        "terminal_gs_ids": sorted(terminal_by_src),
        **retained,
    }


def check_mbb_packet_behavior(
    token: str,
    perm: dict,
    *,
    count: int = 1200,
    interval_s: float = 0.2,
    max_wait_s: int = 900,
) -> dict:
    """Repeated packet windows until one is graded at a completed teardown or
    the time runs out. Every attempt is retained in full, the failed ones
    included, whatever a later attempt shows."""
    deadline = time.monotonic() + max_wait_s
    attempts: list[dict] = []
    while time.monotonic() < deadline:
        remaining_s = deadline - time.monotonic()
        if remaining_s < 60:
            break
        window_count = min(count, max(300, int(min(remaining_s, 300) / interval_s)))
        evidence = _run_mbb_packet_window(token, perm, count=window_count, interval_s=interval_s)
        attempts.append(evidence)
        if evidence.get("result") == "PASS" or evidence.get("terminal_event") is not None:
            return {**evidence, "attempts": attempts}
    return {
        "result": "FAIL",
        "reason": "No qualifying MBB handover packet observation before timeout",
        "max_wait_s": max_wait_s,
        "attempts": attempts,
    }


def _active_ground_link_with_interfaces(token: str, *, wait_s: int = 180) -> dict | None:
    deadline = time.monotonic() + wait_s
    while time.monotonic() < deadline:
        state = request_json("GET", "/api/v1/state", token=token)
        nodes_by_id = _nodes_by_id(state.get("nodes", []))
        decision_snapshot = request_json("GET", "/api/v1/ground-link-decisions", token=token)
        decision_by_pair = {
            tuple(decision.get("pair", [])): decision
            for decision in decision_snapshot.get("decisions", [])
            if len(decision.get("pair", [])) == 2
        }
        links = state.get("links", [])
        if isinstance(links, dict):
            links = list(links.values())
        candidates: list[dict] = []
        for link in links:
            if link.get("state") != "active":
                continue
            a = link.get("node_a", "")
            b = link.get("node_b", "")
            ia = link.get("interface_a") or ""
            ib = link.get("interface_b") or ""
            if not ia or not ib:
                continue
            if _node_id_has_type(a, nodes_by_id, "ground_station") and _node_id_has_type(
                b, nodes_by_id, "satellite"
            ):
                row = {"gs_id": a, "sat_id": b, "gs_ifname": ia, "sat_ifname": ib}
            elif _node_id_has_type(a, nodes_by_id, "satellite") and _node_id_has_type(
                b, nodes_by_id, "ground_station"
            ):
                row = {"gs_id": b, "sat_id": a, "gs_ifname": ib, "sat_ifname": ia}
            else:
                continue
            pair = tuple(sorted((row["gs_id"], row["sat_id"])))
            decision = decision_by_pair.get(pair) or {}
            if decision.get("reject_reason") not in (None, "ok"):
                continue
            elevation = decision.get("elevation_deg")
            if elevation is None or float(elevation) < 45.0:
                continue
            candidates.append(
                {
                    **row,
                    "state_sim_time": state.get("sim_time"),
                    "decision_snapshot_seq": decision_snapshot.get("snapshot_seq"),
                    "decision_elevation_deg": elevation,
                    "decision_range_km": decision.get("range_km"),
                    "link": link,
                }
            )
        if candidates:
            return max(candidates, key=lambda item: float(item["decision_elevation_deg"]))
        time.sleep(2)
    return None


def _actuation_entry(token: str, gs_id: str) -> dict:
    state = request_json("GET", "/api/v1/state", token=token)
    notices = [n for n in state.get("actuation_notices", []) if n.get("gs_id") == gs_id]
    health = request_json("GET", "/api/v1/ops/health", token=token)
    entries = []
    for inst in health.get("scheduler_instances", []):
        for entry in inst.get("ground_stations", []):
            if entry.get("gs_id") == gs_id:
                entries.append(
                    {**entry, "scheduler_instance_id": inst.get("scheduler_instance_id")}
                )
    return {
        "notices": notices,
        "health_entries": entries,
        "state_session_status": state.get("session_status"),
        "state_sim_time": state.get("sim_time"),
    }


def _wait_for_actuation_state(
    token: str,
    gs_id: str,
    target_state: str,
    *,
    wait_s: int = 180,
) -> dict:
    deadline = time.monotonic() + wait_s
    last: dict = {}
    while time.monotonic() < deadline:
        last = _actuation_entry(token, gs_id)
        if target_state == "clean":
            if any(e.get("actuation_state") == "clean" for e in last.get("health_entries", [])):
                if not last.get("notices"):
                    return {"result": "PASS", **last}
        elif any(e.get("actuation_state") == target_state for e in last.get("health_entries", [])):
            return {"result": "PASS", **last}
        elif any(n.get("actuation_state") == target_state for n in last.get("notices", [])):
            return {"result": "PASS", **last}
        time.sleep(2)
    return {
        "result": "FAIL",
        "reason": f"{gs_id} did not reach actuation_state={target_state} within {wait_s}s",
        **last,
    }


def _wait_for_scheduler_actuation_roster(token: str, *, wait_s: int = 180) -> dict:
    """Wait until the scheduler has published the startup clean roster for all GSes."""

    deadline = time.monotonic() + wait_s
    last: dict = {}
    while time.monotonic() < deadline:
        state = request_json("GET", "/api/v1/state", token=token)
        ground_ids = {
            node.get("node_id") for node in state.get("nodes", []) if _is_ground_node(node)
        }
        health = request_json("GET", "/api/v1/ops/health", token=token)
        instances = health.get("scheduler_instances", [])
        rosters = []
        for inst in instances:
            entries = inst.get("ground_stations", [])
            clean_ids = {
                entry.get("gs_id") for entry in entries if entry.get("actuation_state") == "clean"
            }
            rosters.append(
                {
                    "scheduler_instance_id": inst.get("scheduler_instance_id"),
                    "clean_count": len(clean_ids),
                    "entry_count": len(entries),
                    "missing_ground_ids": sorted(ground_ids - clean_ids),
                }
            )
            if ground_ids and ground_ids <= clean_ids:
                return {
                    "result": "PASS",
                    "ground_count": len(ground_ids),
                    "scheduler_instance_id": inst.get("scheduler_instance_id"),
                    "session_status": state.get("session_status"),
                    "sim_time": state.get("sim_time"),
                }
        last = {
            "ground_count": len(ground_ids),
            "session_status": state.get("session_status"),
            "sim_time": state.get("sim_time"),
            "rosters": rosters,
        }
        time.sleep(2)
    return {
        "result": "FAIL",
        "reason": f"Scheduler startup actuation roster did not complete within {wait_s}s",
        **last,
    }


def _events_since(
    token: str,
    started_at: datetime,
    *,
    limit: int = 500,
    source: str | None = None,
) -> list[dict]:
    query = f"limit={limit}"
    if source:
        query += f"&source={source}"
    events = request_json("GET", f"/api/v1/ops/events?{query}", token=token)
    return [event for event in events if _event_at_or_after(event, started_at)]


def run_dirty_repair_acceptance(provenance: dict[str, str] | None = None) -> dict:
    perm = acceptance_permutation(provenance or _run_provenance_from_environment())
    evidence: dict = {
        "id": "P6-REPAIR",
        "label": "forced-kernel-dirty-operator-repair",
        "session_ref": perm["session_ref"],
        "document_sha256": perm["document_sha256"],
        "started_at": datetime.now(UTC).isoformat(),
    }
    try:
        acceptance_progress("dirty-repair: acquiring token")
        token = get_token()
        acceptance_progress("dirty-repair: deploying the shipped session")
        deployed = deploy_shipped_and_wait(token, perm)
        evidence["deploy_response"] = deployed.get("deploy_response")
        evidence["transition"] = deployed.get("transition")
        evidence["observed_runtime"] = deployed.get("observed_runtime")
        if deployed["result"] != "PASS":
            evidence["result"] = "FAIL"
            evidence["error"] = deployed["reason"]
            return evidence
        acceptance_progress("dirty-repair: waiting for session readiness")
        ready_result = wait_for_ready(token, timeout=600)
        evidence["ready_result"] = ready_result
        acceptance_progress(f"dirty-repair: readiness result {ready_result}")
        if ready_result.get("phase") != "Ready":
            evidence["result"] = "FAIL"
            evidence["error"] = f"Did not reach Ready: {ready_result}"
            return evidence

        acceptance_progress("dirty-repair: waiting for scheduler actuation roster")
        roster = _wait_for_scheduler_actuation_roster(token, wait_s=180)
        evidence["scheduler_actuation_roster"] = roster
        acceptance_progress(f"dirty-repair: roster result {roster.get('result')}")
        if roster.get("result") != "PASS":
            evidence["result"] = "FAIL"
            evidence["error"] = "Scheduler actuation startup roster did not complete"
            return evidence

        acceptance_progress("dirty-repair: waiting for active ground link candidate")
        time.sleep(20)
        token = get_token()
        pair = _active_ground_link_with_interfaces(token, wait_s=240)
        acceptance_progress(f"dirty-repair: selected pair {pair}")
        evidence["selected_pair"] = pair
        if not pair:
            evidence["result"] = "FAIL"
            evidence["error"] = "No active ground link with interfaces found"
            return evidence

        gs_id = pair["gs_id"]
        sat_id = pair["sat_id"]
        gs_ifname = pair["gs_ifname"]
        break_started = datetime.now(UTC)
        acceptance_progress(f"dirty-repair: forcing host peer for {gs_id} {gs_ifname} down")
        break_cmd = _force_ground_host_interface_down(gs_id, gs_ifname, timeout=10)
        evidence["forced_mutation"] = {
            "operation": "ground host veth admin-down",
            "gs_id": gs_id,
            "gs_ifname": gs_ifname,
            "sat_id": sat_id,
            "host_ifname": break_cmd.get("host_ifname"),
            "node_name": break_cmd.get("node_name"),
            "node_agent_pod": break_cmd.get("node_agent_pod"),
            "result": break_cmd,
        }
        if break_cmd["rc"] != 0:
            evidence["result"] = "FAIL"
            evidence["error"] = "Failed to induce dirty kernel state"
            return evidence

        acceptance_progress(f"dirty-repair: waiting for {gs_id} kernel_dirty")
        dirty = _wait_for_actuation_state(token, gs_id, "kernel_dirty", wait_s=240)
        evidence["dirty_observation"] = dirty
        acceptance_progress(f"dirty-repair: dirty observation {dirty.get('result')}")
        evidence["events_after_forced_mutation"] = _events_since(token, break_started)
        if dirty.get("result") != "PASS":
            evidence["result"] = "FAIL"
            evidence["error"] = "Forced kernel mutation did not produce kernel_dirty state"
            return evidence

        intervention_id = f"dirty-repair-{int(time.time())}"
        acceptance_progress(f"dirty-repair: requesting repair {intervention_id}")
        repair_response = request_json(
            "POST",
            "/api/v1/ops/repair",
            token=token,
            json={
                "gs_id": gs_id,
                "reason": "Acceptance test: repair a deliberately induced kernel mismatch",
                "intervention_id": intervention_id,
            },
            retries=3,
        )
        evidence["repair_response"] = repair_response
        if repair_response.get("status") != "accepted":
            evidence["result"] = "FAIL"
            evidence["error"] = "Operator repair was not accepted"
            return evidence

        acceptance_progress(f"dirty-repair: waiting for {gs_id} clean after repair")
        clean = _wait_for_actuation_state(token, gs_id, "clean", wait_s=180)
        acceptance_progress(f"dirty-repair: clean observation {clean.get('result')}")
        repair_events = []
        succeeded = False
        failed = []
        event_deadline = time.monotonic() + 30
        while time.monotonic() < event_deadline:
            scheduler_events = request_json(
                "GET", "/api/v1/ops/events?limit=500&source=scheduler", token=token
            )
            repair_events = [
                event
                for event in scheduler_events
                if (event.get("details") or {}).get("intervention_id") == intervention_id
            ]
            succeeded = any(
                event.get("code") == "OPERATOR_REPAIR_SUCCEEDED" for event in repair_events
            )
            failed = [
                event for event in repair_events if event.get("code") == "OPERATOR_REPAIR_FAILED"
            ]
            if succeeded or failed:
                break
            time.sleep(1)
        evidence["clean_observation"] = clean
        evidence["events_after_repair"] = repair_events
        evidence["operator_repair_succeeded_event"] = succeeded
        evidence["operator_repair_failed_events"] = failed
        evidence["result"] = (
            "PASS" if clean.get("result") == "PASS" and succeeded and not failed else "FAIL"
        )
        if evidence["result"] != "PASS":
            evidence["error"] = "Operator repair did not return the GS to clean proven state"
    except Exception as exc:
        evidence["result"] = "ERROR"
        evidence["error"] = str(exc)
    evidence["finished_at"] = datetime.now(UTC).isoformat()
    return evidence


def _wait_for_mbb_overlap(token: str, *, wait_s: int = 600) -> dict:
    deadline = time.monotonic() + wait_s
    next_progress = time.monotonic() + 30
    last_summary: dict = {}

    def pair_for_gs(link: dict, gs_id: str, nodes_by_id: dict[str, dict]) -> list[str] | None:
        a = link.get("node_a", "")
        b = link.get("node_b", "")
        if a == gs_id and _node_id_has_type(b, nodes_by_id, "satellite"):
            return [gs_id, b]
        if b == gs_id and _node_id_has_type(a, nodes_by_id, "satellite"):
            return [gs_id, a]
        return None

    while time.monotonic() < deadline:
        decision_snapshot = request_json("GET", "/api/v1/ground-link-decisions", token=token)
        allocation_events = decision_snapshot.get("allocation_events", [])
        overlap_events = [
            event
            for event in allocation_events
            if event.get("category") == "mbb_overlap_started"
            and len(event.get("pair") or []) == 2
            and len(event.get("successor_pair") or []) == 2
        ]
        state = request_json("GET", "/api/v1/state", token=token)
        nodes_by_id = _nodes_by_id(state.get("nodes", []))
        by_gs = _ground_links_by_gs(state)
        teardown_candidates = []
        multi_link_candidates = []
        for gs_id, links in sorted(by_gs.items()):
            links_by_pair = {
                tuple(pair): link
                for link in links
                if (pair := pair_for_gs(link, gs_id, nodes_by_id)) is not None
            }
            for link in links:
                is_teardown = (
                    link.get("scheduling_state") == "teardown"
                    or link.get("teardown_remaining_ticks") is not None
                    or bool(link.get("successor_pair"))
                )
                if not is_teardown:
                    continue
                old_pair = pair_for_gs(link, gs_id, nodes_by_id)
                successor_pair = link.get("successor_pair") or []
                successor_link = (
                    links_by_pair.get(tuple(successor_pair)) if len(successor_pair) == 2 else None
                )
                candidate = {
                    "gs_id": gs_id,
                    "old_pair": old_pair,
                    "successor_pair": successor_pair,
                    "sim_time": state.get("sim_time"),
                    "decision_snapshot_seq": decision_snapshot.get("snapshot_seq"),
                    "decision_sim_time": decision_snapshot.get("sim_time"),
                    "teardown_link": link,
                    "successor_link": successor_link,
                    "active_ground_links": links,
                    "trigger_source": "link_state_snapshot",
                }
                teardown_candidates.append(candidate)
                if old_pair and successor_link:
                    return {"result": "PASS", **candidate}
            if len(links) >= 2:
                multi_link_candidates.append({"gs_id": gs_id, "links": links})

        if overlap_events:
            event = overlap_events[0]
            old_pair = list(event["pair"])
            successor_pair = list(event["successor_pair"])
            if _node_id_has_type(old_pair[0], nodes_by_id, "ground_station"):
                gs_id = old_pair[0]
            elif _node_id_has_type(old_pair[1], nodes_by_id, "ground_station"):
                gs_id = old_pair[1]
            else:
                continue
            return {
                "result": "PASS",
                "gs_id": gs_id,
                "old_pair": old_pair,
                "successor_pair": successor_pair,
                "sim_time": decision_snapshot.get("sim_time"),
                "decision_snapshot_seq": decision_snapshot.get("snapshot_seq"),
                "decision_sim_time": decision_snapshot.get("sim_time"),
                "overlap_event": event,
                "active_ground_links": by_gs.get(gs_id, []),
                "trigger_source": "ground_allocation_event",
            }

        last_summary = {
            "sim_time": state.get("sim_time"),
            "decision_snapshot_seq": decision_snapshot.get("snapshot_seq"),
            "teardown_candidates": teardown_candidates[:5],
            "multi_link_candidates_without_teardown": multi_link_candidates[:5],
            "allocation_events": allocation_events[:5],
            "active_ground_gs_count": len(by_gs),
        }
        if time.monotonic() >= next_progress:
            acceptance_progress(
                "seek-mbb: waiting for OME overlap start; "
                f"active_ground_gs={len(by_gs)} multi_link={len(multi_link_candidates)} "
                f"allocation_events={len(allocation_events)}"
            )
            next_progress = time.monotonic() + 30
        time.sleep(0.2)
    return {
        "result": "FAIL",
        "reason": f"No OME MBB overlap start observed within {wait_s}s",
        **last_summary,
    }


def _parse_api_datetime(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _wait_for_playback_not_seeking(token: str, epoch_id: int, *, wait_s: int = 120) -> dict:
    deadline = time.monotonic() + wait_s
    last: dict = {}
    while time.monotonic() < deadline:
        last = request_json("POST", "/api/v1/playback", token=token, json={"action": "get_status"})
        if last.get("epoch_id", -1) >= epoch_id and last.get("state") != "seeking":
            return {"result": "PASS", "status": last}
        time.sleep(1)
    return {
        "result": "FAIL",
        "reason": f"Playback did not resume from seek epoch {epoch_id} within {wait_s}s",
        "status": last,
    }


def _connectivity_expectation(session_id: str) -> dict:
    window = INTERMITTENT_CONNECTIVITY_WINDOWS.get(session_id)
    if window is None:
        return {"mode": "continuous"}
    return {"mode": "intermittent", **window}


def _seek_playback_and_pause(token: str, target_sim_time: str) -> dict:
    seek = request_json(
        "POST",
        "/api/v1/playback",
        token=token,
        json={"action": "seek", "target_sim_time": target_sim_time},
        retries=3,
    )
    if seek.get("state") != "seeking" or "epoch_id" not in seek:
        return {
            "result": "FAIL",
            "reason": "seek was not accepted into seeking state",
            "seek": seek,
        }
    resumed = _wait_for_playback_not_seeking(token, int(seek["epoch_id"]), wait_s=120)
    if resumed.get("result") != "PASS":
        return {"result": "FAIL", "reason": resumed.get("reason"), "seek": seek, "resume": resumed}
    paused = request_json(
        "POST",
        "/api/v1/playback",
        token=token,
        json={"action": "pause"},
        retries=3,
    )
    if paused.get("state") != "paused" or not paused.get("paused"):
        return {
            "result": "FAIL",
            "reason": "playback did not enter paused state after seek",
            "seek": seek,
            "resume": resumed,
            "pause": paused,
        }
    return {"result": "PASS", "seek": seek, "resume": resumed, "pause": paused}


def check_intermittent_connectivity(token: str, perm: dict) -> dict:
    expectation = perm.get("connectivity_expectation") or {}
    start_time = perm.get("session_start_time")
    evidence: dict = {"result": "FAIL", "mode": "intermittent_ground_to_ground"}
    if not start_time:
        return {**evidence, "reason": "intermittent connectivity check requires session start time"}

    start = _parse_api_datetime(str(start_time))
    disconnected_target = start + timedelta(seconds=int(expectation["disconnected_offset_seconds"]))
    settle_seconds = int(expectation.get("settle_seconds", 30))

    try:
        disconnected_control = _seek_playback_and_pause(token, disconnected_target.isoformat())
        evidence["disconnected_control"] = disconnected_control
        if disconnected_control.get("result") != "PASS":
            evidence["reason"] = "could not establish deterministic disconnected window"
            return evidence
        time.sleep(settle_seconds)
        disconnected_probe = check_ping(token, perm, ground_wait_s=15)
        evidence["disconnected_probe"] = disconnected_probe
        if disconnected_probe.get("result") != "FAIL":
            evidence["reason"] = "intermittent observation window produced a routed path"
            return evidence
        if disconnected_probe.get("failure_kind") != "connectivity":
            evidence["reason"] = (
                "disconnected window probe did not complete an observation: "
                f"{disconnected_probe.get('reason')}"
            )
            return evidence
        if disconnected_probe.get("active_link_count", 0) <= 0:
            evidence["reason"] = "disconnected window had no active physical links to observe"
            return evidence
        evidence["observed_outcome"] = "unreachable"
        evidence["result"] = "PASS"
        return evidence
    finally:
        evidence["resume_response"] = request_json(
            "POST",
            "/api/v1/playback",
            token=token,
            json={"action": "resume"},
            retries=3,
        )


def check_declared_connectivity(token: str, perm: dict) -> dict:
    expectation = perm.get("connectivity_expectation") or {"mode": "continuous"}
    if expectation.get("mode") == "intermittent":
        return check_intermittent_connectivity(token, perm)
    return check_ping(token, perm)


SEEK_INTO_OVERLAP_OFFSET_S = 10


def _seek_target_for_overlap(
    sample: dict,
    *,
    overlap_ticks: int,
    step_seconds: int,
    offset_s: int = SEEK_INTO_OVERLAP_OFFSET_S,
) -> dict:
    """Where the seek lane aims inside an observed MBB overlap, from one fresh
    sample of the station's links. The overlap started ``overlap_ticks`` minus
    the teardown link's remaining ticks before the sample's sim time; the target
    is that start plus ``offset_s`` sim-seconds. The opportunity is pending only
    while the sample still carries the teardown link with remaining ticks; a
    sample without it is an expired opportunity, never a test."""
    link = sample.get("teardown_link") or {}
    remaining = link.get("teardown_remaining_ticks")
    if not link or remaining is None or int(remaining) <= 0:
        return {
            "pending": False,
            "reason": "the sample carries no pending teardown for the observed overlap",
            "sample_sim_time": sample.get("sim_time"),
        }
    sim = _parse_api_datetime(sample["sim_time"])
    elapsed_ticks = max(0, overlap_ticks - int(remaining))
    start = sim - timedelta(seconds=elapsed_ticks * step_seconds)
    target = start + timedelta(seconds=offset_s)
    return {
        "pending": True,
        "sample_sim_time": sample["sim_time"],
        "remaining_ticks": int(remaining),
        "overlap_ticks": overlap_ticks,
        "overlap_start_sim_time": start.isoformat(),
        "target_sim_time": target.isoformat(),
        "direction": "backward" if target < sim else "forward",
    }


def _pre_seek_sample(token: str, gs_id: str, old_pair: list[str]) -> dict:
    """One fresh, receipt-stamped read of the station's links and the decision
    epoch, taken immediately before the seek request."""
    started = datetime.now(UTC)
    state = request_json("GET", "/api/v1/state", token=token)
    decisions = request_json("GET", "/api/v1/ground-link-decisions", token=token)
    finished = datetime.now(UTC)
    links = _ground_links_by_gs(state).get(gs_id, [])
    teardown_link = next(
        (
            link
            for link in links
            if {link.get("node_a"), link.get("node_b")} == set(old_pair)
            and (
                link.get("scheduling_state") == "teardown"
                or link.get("teardown_remaining_ticks") is not None
            )
        ),
        None,
    )
    return {
        "sim_time": state.get("sim_time"),
        "epoch_id": decisions.get("epoch_id"),
        "decision_snapshot_seq": decisions.get("snapshot_seq"),
        "read_started_wall": started.isoformat(),
        "read_finished_wall": finished.isoformat(),
        "teardown_link": teardown_link,
        "active_ground_links": links,
    }


def _invalidation_matches(
    event: dict, *, old_pair: list, successor_pair: list, epoch_id, target: str
) -> bool:
    details = event.get("details") or {}
    if details.get("terminal_outcome") != "teardown_invalidated_by_epoch":
        return False
    if details.get("old_pair") != old_pair or details.get("successor_pair") != successor_pair:
        return False
    if epoch_id is not None and details.get("epoch_id") != epoch_id:
        return False
    recorded = details.get("seek_target_sim_time")
    if recorded is None:
        return False
    return _parse_api_datetime(str(recorded)) == _parse_api_datetime(target)


def run_seek_during_mbb_acceptance(provenance: dict[str, str] | None = None) -> dict:
    perm = acceptance_permutation(provenance or _run_provenance_from_environment())
    evidence: dict = {
        "id": "P6-SEEK-MBB",
        "label": "seek-during-mbb-overlap",
        "session_ref": perm["session_ref"],
        "document_sha256": perm["document_sha256"],
        "started_at": datetime.now(UTC).isoformat(),
    }
    try:
        acceptance_progress("seek-mbb: acquiring token")
        token = get_token()
        acceptance_progress("seek-mbb: deploying the shipped session")
        deployed = deploy_shipped_and_wait(token, perm)
        evidence["deploy_response"] = deployed.get("deploy_response")
        evidence["transition"] = deployed.get("transition")
        evidence["observed_runtime"] = deployed.get("observed_runtime")
        if deployed["result"] != "PASS":
            evidence["result"] = "FAIL"
            evidence["error"] = deployed["reason"]
            return evidence
        ready_result = wait_for_ready(token, timeout=600)
        evidence["ready_result"] = ready_result
        if ready_result.get("phase") != "Ready":
            evidence["result"] = "FAIL"
            evidence["error"] = f"Did not reach Ready: {ready_result}"
            return evidence

        time.sleep(20)
        token = get_token()
        acceptance_progress("seek-mbb: waiting for OME teardown-state overlap")
        overlap = _wait_for_mbb_overlap(token, wait_s=900)
        evidence["overlap_observation"] = overlap
        acceptance_progress(f"seek-mbb: overlap observation {overlap.get('result')}")
        if overlap.get("result") != "PASS":
            evidence["result"] = "FAIL"
            evidence["error"] = "No MBB overlap available for seek test"
            return evidence

        # The seek aims inside the observed overlap, at its start plus ten
        # sim-seconds, from a fresh sample taken right before the request; an
        # overlap no longer pending at that sample is an expired opportunity.
        station = perm["mbb_stations"].get(overlap["gs_id"]) or {}
        sample = _pre_seek_sample(token, overlap["gs_id"], overlap["old_pair"])
        target = _seek_target_for_overlap(
            sample,
            overlap_ticks=int(station.get("mbb_overlap_ticks") or 0),
            step_seconds=int(perm["step_seconds"]),
        )
        evidence["pre_seek_sample"] = sample
        evidence["seek_target"] = target
        if not target["pending"]:
            evidence["result"] = "FAIL"
            evidence["opportunity"] = "expired"
            evidence["error"] = (
                "MBB overlap no longer pending when the seek was to be requested; "
                "no seek-during-overlap test was performed"
            )
            return evidence
        seek_target = target["target_sim_time"]
        acceptance_progress(f"seek-mbb: requesting seek to {seek_target}")
        requested_wall = datetime.now(UTC)
        seek_response = request_json(
            "POST",
            "/api/v1/playback",
            token=token,
            json={"action": "seek", "target_sim_time": seek_target},
            retries=3,
        )
        accepted_wall = datetime.now(UTC)
        evidence["seek_request"] = {
            "target_sim_time": seek_target,
            "response": seek_response,
            "requested_wall": requested_wall.isoformat(),
            "accepted_wall": accepted_wall.isoformat(),
            "sample_age_at_request_s": (
                requested_wall - datetime.fromisoformat(sample["read_finished_wall"])
            ).total_seconds(),
        }
        if seek_response.get("state") != "seeking" or "epoch_id" not in seek_response:
            evidence["result"] = "FAIL"
            evidence["error"] = "Seek was not accepted into seeking state"
            return evidence

        acceptance_progress("seek-mbb: waiting for playback to resume")
        resumed = _wait_for_playback_not_seeking(token, int(seek_response["epoch_id"]), wait_s=120)
        evidence["resume_observation"] = resumed
        acceptance_progress(f"seek-mbb: resume observation {resumed.get('result')}")
        events = request_json("GET", "/api/v1/ops/events?limit=500", token=token)
        lifecycle = [
            event
            for event in events
            if event.get("source") == "ome" and event.get("code") == "MBB_TEARDOWN_TERMINAL"
        ]
        # The invalidation must be of this particular old-epoch teardown: same
        # pairs, the epoch the sample saw, and the seek target that was requested.
        invalidated = [
            event
            for event in lifecycle
            if _invalidation_matches(
                event,
                old_pair=list(overlap.get("old_pair") or []),
                successor_pair=list(overlap.get("successor_pair") or []),
                epoch_id=sample.get("epoch_id"),
                target=seek_target,
            )
        ]
        bad = [event for event in events if event.get("code") in MBB_BAD_OPS_CODES]
        state_after = request_json("GET", "/api/v1/state", token=token)
        notices_after = state_after.get("actuation_notices", [])
        evidence["events_after_seek"] = events
        evidence["seek_invalidated_lifecycle_events"] = invalidated
        evidence["bad_ops_events"] = bad
        evidence["actuation_notices_after_seek"] = notices_after
        evidence["result"] = (
            "PASS"
            if resumed.get("result") == "PASS" and invalidated and not bad and not notices_after
            else "FAIL"
        )
        if evidence["result"] != "PASS":
            evidence["error"] = "Seek during MBB did not produce clean epoch invalidation evidence"
    except Exception as exc:
        evidence["result"] = "ERROR"
        evidence["error"] = str(exc)
    evidence["finished_at"] = datetime.now(UTC).isoformat()
    return evidence


def run_mbb_acceptance(provenance: dict[str, str] | None = None) -> dict:
    perm = acceptance_permutation(provenance or _run_provenance_from_environment())
    evidence: dict = {
        "id": "C-J",
        "label": "mbb-routing-packet-observation",
        "session_ref": perm["session_ref"],
        "document_sha256": perm["document_sha256"],
        "started_at": datetime.now(UTC).isoformat(),
    }
    try:
        token = get_token()
        acceptance_progress("mbb: deploying the shipped session")
        deployed = deploy_shipped_and_wait(token, perm)
        evidence["deploy_response"] = deployed.get("deploy_response")
        evidence["transition"] = deployed.get("transition")
        evidence["observed_runtime"] = deployed.get("observed_runtime")
        if deployed["result"] != "PASS":
            evidence["result"] = "FAIL"
            evidence["error"] = deployed["reason"]
            return evidence
        acceptance_progress("mbb: waiting for session readiness")
        ready_result = wait_for_ready(token, timeout=600)
        evidence["ready_result"] = ready_result
        acceptance_progress(f"mbb: readiness result {ready_result}")
        if ready_result.get("phase") != "Ready":
            evidence["result"] = "FAIL"
            evidence["error"] = f"Did not reach Ready: {ready_result}"
            return evidence

        time.sleep(30)
        token = get_token()
        evidence["convergence_preconditions"] = check_mbb_convergence_preconditions(token, perm)
        evidence["mbb_packet_behavior"] = check_mbb_packet_behavior(token, perm)
        evidence["lifecycle_and_ops"] = check_mbb_lifecycle_and_ops(token)
        passed = all(
            evidence[key].get("result") == "PASS"
            for key in ("convergence_preconditions", "mbb_packet_behavior", "lifecycle_and_ops")
        )
        evidence["result"] = "PASS" if passed else "FAIL"
    except Exception as exc:
        evidence["result"] = "ERROR"
        evidence["error"] = str(exc)
    evidence["finished_at"] = datetime.now(UTC).isoformat()
    return evidence


def run_permutation(perm: dict) -> dict:
    """Run a single E2E permutation."""
    perm_id = perm["id"]
    label = perm.get("label") or f"{perm['constellation']}-{perm['protocol']}"
    if perm.get("extensions"):
        label += "-" + "-".join(perm["extensions"])
    print(f"\n{'=' * 60}")
    print(f"Permutation {perm_id}: {label}")
    print(f"{'=' * 60}")

    evidence: dict = {
        "id": perm_id,
        "label": label,
        "spec": perm,
        "provenance": perm["run_provenance"],
        "started_at": datetime.now(UTC).isoformat(),
    }

    try:
        token = get_token()

        # Wait for any in-progress switch to finish before starting
        import subprocess

        for _wait in range(60):
            result = subprocess.run(
                f"{KUBECTL} get constellationspec current-session -n nodalarc "
                "-o 'jsonpath={{.status.phase}}'",
                capture_output=True,
                text=True,
                timeout=10,
                shell=True,
            )
            phase = result.stdout.strip()
            if phase not in ("Pending", "Rendering", "Creating", "Wiring"):
                break
            print(f"  Waiting for previous deploy to finish (phase={phase})...")
            time.sleep(5)

        print("  Loading exact shipped session YAML...")
        yaml_str = perm["session_yaml"]
        evidence["yaml_length"] = len(yaml_str)

        # Deploy
        print("  Deploying guarded shipped catalog revision...")
        deployed = deploy_shipped_and_wait(token, perm)
        evidence["deploy_response"] = deployed.get("deploy_response")
        evidence["transition_result"] = deployed.get("transition")
        evidence["observed_runtime"] = deployed.get("observed_runtime")
        if deployed["result"] != "PASS":
            evidence["result"] = "FAIL"
            evidence["error"] = deployed["reason"]
            print(f"  FAIL: {evidence['error']}")
            return evidence

        # Wait for Ready
        print("  Waiting for Ready (up to 5 min)...")
        ready_result = wait_for_ready(token, timeout=600)
        evidence["ready_result"] = ready_result
        if ready_result.get("phase") != "Ready":
            evidence["result"] = "FAIL"
            evidence["error"] = f"Did not reach Ready: {ready_result}"
            print(f"  FAIL: {evidence['error']}")
            return evidence

        # Check pods
        # Wait for platform pods to stabilize (Operator restarts VS-API/OME)
        print("  Waiting 30s for platform stabilization...")
        time.sleep(30)
        token = get_token()  # Re-fetch (VS-API may have restarted)

        print("  Checking pods...")
        pod_result = check_pods(perm)
        evidence["pods"] = pod_result

        # Check routing
        print("  Checking routing convergence...")
        routing_result = check_routing(token, perm)
        evidence["routing"] = routing_result

        # Check WebSocket
        print("  Checking WebSocket snapshots...")
        ws_result = check_websocket(token, step_seconds=perm.get("step_seconds", 1))
        evidence["websocket"] = ws_result

        # Check declared connectivity. Ground sessions must prove a GS-originated path;
        # satellite-only sessions may fall back to an ISL loopback path.
        print("  Checking declared connectivity...")
        ping_result = check_declared_connectivity(token, perm)
        evidence["ping"] = ping_result
        transit = ""
        if "transit_proven" in ping_result:
            transit = (
                f" transit={'proven' if ping_result['transit_proven'] else 'NOT PROVEN'}"
                f" sites={ping_result.get('src_site', '?')}->{ping_result.get('dst_site', '?')}"
                f" sep={ping_result.get('separation', '?')}"
                f" egress={ping_result.get('egress_dev', '?')}"
            )
        observed_outcome = ping_result.get("observed_outcome")
        if observed_outcome:
            active_links = (ping_result.get("disconnected_probe") or {}).get(
                "active_link_count", "?"
            )
            print(
                f"  Connectivity: {ping_result.get('result', '?')} "
                f"(runtime reported {observed_outcome}; active_links={active_links})"
            )
        else:
            print(
                f"  Ping: {ping_result.get('result', '?')}"
                f" ({ping_result.get('src', '?')} -> {ping_result.get('dst', '?')}){transit}"
            )

        # Determine pass/fail
        ping_ok = ping_result.get("result") == "PASS" or (
            ping_result.get("result") == "SKIP" and ping_result.get("ground_node_count", 0) == 0
        )
        passed = (
            ready_result.get("phase") == "Ready"
            and pod_result["running"] == pod_result["total"]
            and ws_result["advancing"]
            and ws_result["plane_slot_ok"]
            and ping_ok
        )
        evidence["result"] = "PASS" if passed else "FAIL"
        print(
            f"  {evidence['result']}: {pod_result['running']} pods, "
            f"{routing_result.get('neighbor_count', '?')} neighbors, "
            f"sim_time={'advancing' if ws_result['advancing'] else 'STATIC'}, "
            f"connectivity={observed_outcome or ping_result.get('result', '?')}"
        )

    except Exception as exc:
        evidence["result"] = "ERROR"
        evidence["error"] = str(exc)
        print(f"  ERROR: {exc}")

    evidence["finished_at"] = datetime.now(UTC).isoformat()
    return evidence


def catalog_permutations(session_id: str | None = None) -> list[dict]:
    """The shipped catalog sessions, deployed verbatim — network truth
    for the worked examples before any generated permutations. Protocol
    is derived from the session's own routing domains via the production
    resolver, never hand-stated."""
    import sys as _sys

    repo = Path(__file__).resolve().parents[2]
    _sys.path.insert(0, str(repo / "lib"))
    from nodalarc.catalog_paths import CatalogRoots
    from nodalarc.models.resolved_session import SourceContext
    from nodalarc.resolve_session import resolve_session_with_assets

    roots = CatalogRoots.from_catalog_root(repo / "catalog" / "nodalarc")
    only = session_id or os.environ.get("NODALARC_E2E_ONLY", "")
    perms = []
    for path in sorted((repo / "catalog" / "nodalarc" / "sessions").glob("*.yaml")):
        if only and path.stem != only:
            continue
        text = path.read_text()
        resolved = resolve_session_with_assets(
            load_configuration_yaml(text),
            catalog=FilesystemCatalogReadView(roots),
            source_context=SourceContext(origin="e2e.catalog"),
        ).resolved
        protocols = sorted({d.protocol for d in resolved.routing_domains})
        ground_ids = sorted(
            node.node_id for node in resolved.nodes if node.kind == "ground_station"
        )
        # Site identity, body, position, and manifest-allocated WAN names
        # per ground node — the probe's inter-site transit proof derives
        # from these resolved facts, never from node-ID string shapes.
        ground_topology = {
            node.node_id: {
                "site": node.namespace,
                "body": str(node.surface_position.body),
                "lat_deg": node.surface_position.lat_deg,
                "lon_deg": node.surface_position.lon_deg,
                "wan_ifnames": [w.name for w in node.wan_interfaces],
            }
            for node in resolved.nodes
            if node.kind == "ground_station" and node.surface_position is not None
        }
        # Each station's resolved handover facts: the allocator's steady limit is
        # terminal capacity minus the MBB reserve, so a limit of one means every
        # handover at that station is an MBB overlap. Read from the resolver's
        # per-station scheduling, never from the session text.
        mbb_stations = {}
        for node in resolved.nodes:
            if node.kind != "ground_station" or node.ground_scheduling is None:
                continue
            scheduling = node.ground_scheduling
            capacity = sum(block.count for block in node.terminal_inventory)
            reserve = int(scheduling.mbb_reserve or 0)
            mbb_stations[node.node_id] = {
                "site": node.namespace,
                "handover_mode": scheduling.handover_mode,
                "terminal_capacity": capacity,
                "mbb_reserve": reserve,
                "mbb_overlap_ticks": int(scheduling.mbb_overlap_ticks or 0),
                "steady_limit": capacity - reserve
                if scheduling.handover_mode == "mbb"
                else capacity,
            }
        perms.append(
            {
                "id": path.stem,
                "label": f"catalog-{path.stem}",
                "session_ref": f"nodalarc:sessions/{path.stem}.yaml",
                "document_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
                "protocol": protocols[0],
                "protocols": protocols,
                # Derived from the resolved session, never hand-stated:
                # the ground-truth predicate for the ping/adjacency checks.
                "gs": ground_ids,
                "ground_topology": ground_topology,
                "mbb_stations": mbb_stations,
                "session_start_time": str(resolved.time.start_time),
                "connectivity_expectation": _connectivity_expectation(path.stem),
                "step_seconds": int(resolved.time.step_seconds),
                "session_yaml": text,
                "xfail": False,
            }
        )
    return perms


def acceptance_permutation(provenance: dict[str, str]) -> dict:
    """The shipped MBB acceptance session as one catalog permutation, resolved
    and digested like every other shipped session, carrying the run's
    provenance for the identity gate."""
    perms = catalog_permutations(session_id=MBB_ACCEPTANCE_SESSION_ID)
    if len(perms) != 1:
        raise RuntimeError(f"shipped session {MBB_ACCEPTANCE_SESSION_ID} not found in the catalog")
    return {**perms[0], "run_provenance": provenance}


def main():
    print(f"E2E Matrix starting at {datetime.now(UTC).isoformat()}")
    print(f"PID: {os.getpid()}")

    provenance = _run_provenance_from_environment()
    evidence_root = Path(
        os.environ.get("NODALARC_E2E_EVIDENCE_DIR", "tests/integration/e2e-evidence")
    )

    ts = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    evidence_dir = (
        evidence_root
        / "runtime-matrix"
        / f"{ts}-{provenance['source_git_sha'][:8]}-pid{os.getpid()}"
    )
    evidence_dir.mkdir(parents=True, exist_ok=False)

    # PID file: signals "running" to external pollers
    pid_file = evidence_root / ".running"
    pid_file.write_text(str(os.getpid()))

    try:
        run_start = datetime.now(UTC)
        run_token = f"{run_start.strftime('%Y%m%d-%H%M%S')}-pid{os.getpid()}"
        (evidence_dir / ".run_token").write_text(run_token)
        print(f"Run token: {run_token}")
        print(f"Evidence directory: {evidence_dir}")
        print()

        results = []
        passed = 0
        failed = 0
        xfailed = 0
        xpassed = 0

        if os.environ.get("NODALARC_ACCEPTANCE_ONLY") != "1":
            active_matrix = [
                {**permutation, "run_provenance": provenance}
                for permutation in catalog_permutations()
            ]
            print(f"Catalog sessions: {len(active_matrix)}")
            for perm in active_matrix:
                evidence = run_permutation(perm)
                bucket = _classify_matrix_result(evidence, perm)
                results.append(evidence)

                if bucket == "pass":
                    passed += 1
                elif bucket == "xpass":
                    xpassed += 1
                elif bucket == "xfail":
                    xfailed += 1
                else:
                    failed += 1

                # Write per-permutation evidence immediately, after xfail/xpass classification.
                eid = perm["id"]
                label = evidence.get("label", "unknown")
                eid_label = f"{eid:02d}" if isinstance(eid, int) else str(eid)
                evidence_file = evidence_dir / f"perm-{eid_label}-{label}.json"
                evidence_file.write_text(json.dumps(evidence, indent=2))

        # C-J, the MBB handover acceptance on the shipped walker, is a default
        # entry of the matrix: a named test-only step whose result counts like
        # every shipped session's. A matrix without it is incomplete.
        print(f"\n{'=' * 60}")
        print("Acceptance C-J: mbb-routing-packet-observation (earth-leo-walker)")
        print(f"{'=' * 60}")
        evidence = run_mbb_acceptance(provenance)
        evidence["provenance"] = provenance
        results.append(evidence)
        evidence_file = evidence_dir / "acceptance-cj-mbb-routing-packet-observation.json"
        evidence_file.write_text(json.dumps(evidence, indent=2))
        if evidence["result"] == "PASS":
            passed += 1
        else:
            failed += 1
        print(f"  C-J: {evidence['result']}")

        if os.environ.get("NODALARC_RUN_DIRTY_REPAIR") == "1":
            evidence = run_dirty_repair_acceptance(provenance)
            evidence["provenance"] = provenance
            results.append(evidence)
            evidence_file = evidence_dir / "dirty-repair.json"
            evidence_file.write_text(json.dumps(evidence, indent=2))
            if evidence["result"] == "PASS":
                passed += 1
            else:
                failed += 1

        if os.environ.get("NODALARC_RUN_SEEK_MBB") == "1":
            evidence = run_seek_during_mbb_acceptance(provenance)
            evidence["provenance"] = provenance
            results.append(evidence)
            evidence_file = evidence_dir / "seek-during-mbb.json"
            evidence_file.write_text(json.dumps(evidence, indent=2))
            if evidence["result"] == "PASS":
                passed += 1
            else:
                failed += 1

        # Write summary
        run_end = datetime.now(UTC)
        duration_s = (run_end - run_start).total_seconds()
        summary = {
            "run_token": run_token,
            "provenance": provenance,
            "start_time": run_start.isoformat(),
            "end_time": run_end.isoformat(),
            "duration_s": round(duration_s, 1),
            "total": len(results),
            "passed": passed,
            "failed": failed,
            "xfailed": xfailed,
            "xpassed": xpassed,
            "results": [
                {
                    "id": r["id"],
                    "label": r.get("label"),
                    "result": r["result"],
                    "observed_runtime": r.get("observed_runtime"),
                }
                for r in results
            ],
        }
        (evidence_dir / "matrix-summary.json").write_text(json.dumps(summary, indent=2))

        # Validate evidence is from this run
        token_file = evidence_dir / ".run_token"
        if not token_file.exists():
            print("FATAL: .run_token missing from evidence directory")
            sys.exit(2)
        stored_token = token_file.read_text().strip()
        if stored_token != run_token:
            print(f"FATAL: run_token mismatch: expected {run_token}, got {stored_token}")
            sys.exit(2)

        # Print final summary
        print()
        print("=" * 60)
        xfail_str = f", {xfailed} xfail" if xfailed else ""
        xpass_str = f", {xpassed} xpass" if xpassed else ""
        print(
            f"E2E Matrix: {passed}/{len(results)} passed, {failed}/{len(results)} failed"
            f"{xfail_str}{xpass_str}"
        )
        print(f"Duration: {duration_s:.0f}s")
        print(f"Run token: {run_token}")
        print("=" * 60)
        for r in results:
            status = r.get("result", "?")
            tag = (
                "PASS"
                if status == "PASS"
                else "XFAIL"
                if status == "XFAIL"
                else "XPASS"
                if status == "XPASS"
                else "FAIL"
                if status == "FAIL"
                else status
            )
            rid = str(r["id"]).rjust(2)
            print(f"  [{rid}] {tag:8s} {r.get('label', '')}")
        print(f"\nEvidence: {evidence_dir}/")
        print(f"Summary:  {evidence_dir}/matrix-summary.json")

    finally:
        # Always remove PID file, even on crash
        if pid_file.exists():
            pid_file.unlink()

    sys.exit(0 if failed == 0 else 1)


if __name__ == "__main__":
    main()
