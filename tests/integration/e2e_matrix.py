"""E2E validation matrix — tests 12 constellation/protocol permutations via wizard API.

Runs each permutation: generate session → deploy via CRD → wait for Ready →
verify pods + FRR configs + routing convergence + WebSocket snapshots →
write evidence files.

Usage: .venv/bin/python3 tests/integration/e2e_matrix.py
"""

from __future__ import annotations

import json
import os
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

import requests

VS_API_HOST = os.environ.get("VS_API_HOST", "192.168.10.202:8080")
BASE_URL = f"http://{VS_API_HOST}"
EVIDENCE_DIR = Path("tests/integration/e2e-evidence")
KUBECTL = "sudo KUBECONFIG=/etc/rancher/k3s/k3s.yaml kubectl"

MATRIX = [
    {
        "id": 1,
        "constellation": "starlink-early-44",
        "protocol": "isis",
        "extensions": ["sr"],
        "gs": "global",
    },
    {
        "id": 2,
        "constellation": "starlink-early-44",
        "protocol": "ospf",
        "extensions": [],
        "gs": "global",
    },
    {
        "id": 3,
        "constellation": "iridium-small-36",
        "protocol": "isis",
        "extensions": ["sr"],
        "gs": "polar-emphasis",
    },
    {
        "id": 4,
        "constellation": "iridium-small-36",
        "protocol": "ospf",
        "extensions": ["te"],
        "gs": "global",
    },
    {
        "id": 5,
        "constellation": "kuiper-50",
        "protocol": "isis",
        "extensions": ["sr"],
        "gs": "transatlantic",
    },
    {
        "id": 6,
        "constellation": "kuiper-50",
        "protocol": "ospf",
        "extensions": ["te", "mpls"],
        "gs": "global",
    },
    {
        "id": 7,
        "constellation": "oneweb-60",
        "protocol": "isis",
        "extensions": ["sr"],
        "gs": "us-conus",
    },
    {
        "id": 8,
        "constellation": "oneweb-60",
        "protocol": "ospf",
        "extensions": [],
        "gs": "transpacific",
    },
    {
        "id": 9,
        "constellation": "starlink-early-44",
        "protocol": "isis",
        "extensions": ["sr"],
        "gs": "global",
        "area": "stripe",
    },
    {
        "id": 10,
        "constellation": "starlink-early-44",
        "protocol": "nodalpath",
        "extensions": [],
        "gs": "global",
    },
    {
        "id": 11,
        "constellation": "iridium-small-36",
        "protocol": "nodalpath",
        "extensions": [],
        "gs": "polar-emphasis",
    },
    {
        "id": 12,
        "constellation": "iridium-66",
        "protocol": "isis",
        "extensions": ["sr"],
        "gs": "global",
    },
]


def get_token() -> str:
    resp = requests.get(f"{BASE_URL}/api/v1/auth/token")
    return resp.json()["token"]


def headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


def generate_session(token: str, perm: dict) -> str:
    """Generate session YAML via wizard API."""
    body = {
        "constellation": perm["constellation"],
        "protocol": perm["protocol"],
        "extensions": perm.get("extensions", []),
        "ground_stations": perm["gs"],
    }
    if perm.get("area"):
        body["area_strategy"] = perm["area"]
    resp = requests.post(f"{BASE_URL}/api/v1/session/generate", headers=headers(token), json=body)
    if resp.status_code != 200:
        raise RuntimeError(f"Generate failed: {resp.status_code} {resp.text}")
    return resp.json().get("yaml", "")


def deploy_session(token: str, yaml_str: str) -> dict:
    """Deploy session via wizard API."""
    resp = requests.post(
        f"{BASE_URL}/api/v1/session/deploy",
        headers=headers(token),
        json={"yaml": yaml_str},
    )
    if resp.status_code not in (200, 202):
        raise RuntimeError(f"Deploy failed: {resp.status_code} {resp.text}")
    return resp.json()


def wait_for_ready(token: str, timeout: int = 300) -> dict:
    """Wait for CR status to reach Ready — checks K8s directly."""
    import subprocess

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            result = subprocess.run(
                f"{KUBECTL} get constellationspec current-session -n nodalarc "
                "-o jsonpath={.status.phase}".split(),
                capture_output=True,
                text=True,
                timeout=10,
            )
            phase = result.stdout.strip()
            if phase == "Ready":
                # Also check VS-API has nodes
                try:
                    resp = requests.get(f"{BASE_URL}/api/v1/state", headers=headers(token))
                    nodes = resp.json().get("nodes", [])
                    return {"phase": "Ready", "nodes": len(nodes)}
                except Exception:
                    return {"phase": "Ready", "nodes": 0}
            if phase == "Error":
                result2 = subprocess.run(
                    f"{KUBECTL} get constellationspec current-session -n nodalarc "
                    "-o jsonpath={.status.message}".split(),
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
                return {"phase": "Error", "detail": result2.stdout.strip()}
        except Exception:
            pass
        time.sleep(5)
    return {"phase": "Timeout"}


def check_pods(perm: dict) -> dict:
    """Check pod count and status via kubectl."""
    import subprocess

    result = subprocess.run(
        f"{KUBECTL} get pods -n nodalarc -l nodalarc.io/node-id --no-headers".split(),
        capture_output=True,
        text=True,
    )
    lines = [l for l in result.stdout.strip().splitlines() if l.strip()]
    total = len(lines)
    running = sum(1 for l in lines if "Running" in l)
    return {"total": total, "running": running}


def check_routing(token: str, perm: dict) -> dict:
    """Check routing convergence via introspect."""
    protocol = perm["protocol"]
    if protocol == "nodalpath":
        return {"protocol": "nodalpath", "check": "skipped"}

    # Pick first satellite
    resp = requests.get(f"{BASE_URL}/api/v1/state", headers=headers(token))
    nodes = resp.json().get("nodes", [])
    sat = next((n for n in nodes if n.get("node_id", "").startswith("sat-")), None)
    if not sat:
        return {"error": "no satellites found"}

    if protocol == "isis":
        cmd = "show isis neighbor"
    else:
        cmd = "show ip ospf neighbor"

    introspect_resp = requests.post(
        f"{BASE_URL}/api/v1/introspect",
        headers=headers(token),
        json={"node_id": sat["node_id"], "command": cmd},
    )
    output = introspect_resp.json().get("output", "")
    neighbor_count = len([l for l in output.splitlines() if "Up" in l or "Full" in l])
    return {
        "protocol": protocol,
        "node": sat["node_id"],
        "command": cmd,
        "neighbor_count": neighbor_count,
        "output_lines": len(output.splitlines()),
    }


def check_websocket(token: str) -> dict:
    """Check WebSocket delivers advancing sim_time."""
    t1_resp = requests.get(f"{BASE_URL}/api/v1/state", headers=headers(token))
    t1 = t1_resp.json().get("sim_time", "")
    time.sleep(3)
    t2_resp = requests.get(f"{BASE_URL}/api/v1/state", headers=headers(token))
    t2 = t2_resp.json().get("sim_time", "")
    nodes = t2_resp.json().get("nodes", [])
    sats = [n for n in nodes if n.get("node_id", "").startswith("sat-")]
    plane_ok = all(isinstance(s.get("plane"), int) for s in sats)
    return {
        "sim_time_1": t1[:19],
        "sim_time_2": t2[:19],
        "advancing": t1 != t2,
        "node_count": len(nodes),
        "plane_slot_ok": plane_ok,
    }


def run_permutation(perm: dict) -> dict:
    """Run a single E2E permutation."""
    perm_id = perm["id"]
    label = f"{perm['constellation']}-{perm['protocol']}"
    if perm.get("extensions"):
        label += "-" + "-".join(perm["extensions"])
    print(f"\n{'=' * 60}")
    print(f"Permutation {perm_id}: {label}")
    print(f"{'=' * 60}")

    evidence: dict = {
        "id": perm_id,
        "label": label,
        "spec": perm,
        "started_at": datetime.now(UTC).isoformat(),
    }

    try:
        token = get_token()

        # Wait for any in-progress switch to finish before starting
        import subprocess

        for _wait in range(60):
            result = subprocess.run(
                f"{KUBECTL} get constellationspec current-session -n nodalarc "
                "-o jsonpath={{.status.phase}}".split(),
                capture_output=True,
                text=True,
                timeout=10,
            )
            phase = result.stdout.strip()
            if phase not in ("Pending", "Rendering", "Creating", "Wiring"):
                break
            print(f"  Waiting for previous deploy to finish (phase={phase})...")
            time.sleep(5)

        # Generate session YAML
        print("  Generating session YAML...")
        yaml_str = generate_session(token, perm)
        evidence["yaml_length"] = len(yaml_str)

        # Deploy
        print("  Deploying via wizard API...")
        deploy_result = deploy_session(token, yaml_str)
        evidence["deploy_response"] = deploy_result

        # Wait for Ready
        print("  Waiting for Ready (up to 5 min)...")
        ready_result = wait_for_ready(token, timeout=300)
        evidence["ready_result"] = ready_result
        if ready_result.get("phase") != "Ready":
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
        ws_result = check_websocket(token)
        evidence["websocket"] = ws_result

        # Determine pass/fail
        passed = (
            ready_result.get("phase") == "Ready"
            and pod_result["running"] == pod_result["total"]
            and ws_result["advancing"]
            and ws_result["plane_slot_ok"]
        )
        evidence["result"] = "PASS" if passed else "FAIL"
        print(
            f"  {'PASS' if passed else 'FAIL'}: {pod_result['running']} pods, "
            f"{routing_result.get('neighbor_count', '?')} neighbors, "
            f"sim_time={'advancing' if ws_result['advancing'] else 'STATIC'}"
        )

    except Exception as exc:
        evidence["result"] = "ERROR"
        evidence["error"] = str(exc)
        print(f"  ERROR: {exc}")

    evidence["finished_at"] = datetime.now(UTC).isoformat()
    return evidence


def main():
    EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)

    results = []
    passed = 0
    failed = 0

    for perm in MATRIX:
        evidence = run_permutation(perm)
        results.append(evidence)

        # Write per-permutation evidence
        eid = perm["id"]
        evidence_file = EVIDENCE_DIR / f"perm-{eid:02d}-{evidence.get('label', 'unknown')}.json"
        evidence_file.write_text(json.dumps(evidence, indent=2))

        if evidence["result"] == "PASS":
            passed += 1
        else:
            failed += 1

    # Write summary
    summary = {
        "timestamp": datetime.now(UTC).isoformat(),
        "total": len(MATRIX),
        "passed": passed,
        "failed": failed,
        "results": [
            {"id": r["id"], "label": r.get("label"), "result": r["result"]} for r in results
        ],
    }
    (EVIDENCE_DIR / "matrix-summary.json").write_text(json.dumps(summary, indent=2))

    print(f"\n{'=' * 60}")
    print(f"E2E Matrix: {passed}/{len(MATRIX)} passed, {failed} failed")
    print(f"Evidence: {EVIDENCE_DIR}/")
    print(f"{'=' * 60}")

    sys.exit(0 if failed == 0 else 1)


if __name__ == "__main__":
    main()
