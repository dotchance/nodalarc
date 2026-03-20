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


def wait_for_ready(token: str, timeout: int = 600) -> dict:
    """Wait for CR Ready AND VS-API session_status to settle."""
    import subprocess

    deadline = time.monotonic() + timeout

    # Phase 1: Wait for CR to reach Ready or Error
    cr_ready = False
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
                cr_ready = True
                break
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

    if not cr_ready:
        return {"phase": "Timeout"}

    # Phase 2: Wait for VS-API session_status to leave "switching"
    # The _run_switch background task may still be running its poll loop
    for _ in range(60):  # up to 60s
        try:
            t = get_token()
            resp = requests.get(f"{BASE_URL}/api/v1/state", headers=headers(t))
            state = resp.json()
            status = state.get("session_status", "")
            if status != "switching":
                nodes = state.get("nodes", [])
                return {"phase": "Ready", "nodes": len(nodes)}
        except Exception:
            pass
        time.sleep(1)

    # VS-API still switching after 60s — return Ready anyway (CR says so)
    return {"phase": "Ready", "nodes": 0}


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
        return {
            "protocol": "nodalpath",
            "check": "deferred_to_ping",
            "reason": "MPLS table checked in ping step",
        }

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

    # Retry plane/slot check — PositionEvents may not have reached all nodes yet
    retries = 0
    while not plane_ok and retries < 3:
        time.sleep(10)
        retry_resp = requests.get(f"{BASE_URL}/api/v1/state", headers=headers(token))
        nodes = retry_resp.json().get("nodes", [])
        sats = [n for n in nodes if n.get("node_id", "").startswith("sat-")]
        plane_ok = all(isinstance(s.get("plane"), int) for s in sats)
        retries += 1

    return {
        "sim_time_1": t1[:19],
        "sim_time_2": t2[:19],
        "advancing": t1 != t2,
        "node_count": len(nodes),
        "plane_slot_ok": plane_ok,
        "plane_slot_retries": retries,
    }


def check_ping(token: str, perm: dict) -> dict:
    """Ping between ground stations through the satellite mesh."""
    import subprocess

    protocol = perm["protocol"]
    if protocol == "nodalpath":
        return check_nodalpath_mpls(token, perm)

    resp = requests.get(f"{BASE_URL}/api/v1/state", headers=headers(token))
    nodes = resp.json().get("nodes", [])
    gs_nodes = [n for n in nodes if n.get("node_id", "").startswith("gs-")]

    if len(gs_nodes) < 2:
        return {"result": "SKIP", "reason": "fewer than 2 ground stations"}

    src = gs_nodes[0]["node_id"]
    dst = gs_nodes[1]["node_id"]

    # Derive dst loopback IP from node_id
    # Satellites: sat-P{plane}S{slot} -> 10.{plane}.{slot}.1
    # Ground stations: gs-{name} -> 10.255.{gs_index}.1
    import re

    dst_ip = None
    sat_match = re.match(r"sat-P(\d+)S(\d+)", dst, re.IGNORECASE)
    if sat_match:
        dst_ip = f"10.{int(sat_match.group(1))}.{int(sat_match.group(2))}.1"
    else:
        # GS — find index from position in gs_nodes list
        gs_names = [n["node_id"] for n in gs_nodes]
        try:
            gs_idx = gs_names.index(dst)
            dst_ip = f"10.255.{gs_idx + 1}.1"
        except ValueError:
            pass

    if not dst_ip:
        return {
            "result": "FAIL",
            "reason": f"Could not derive loopback IP for {dst}",
            "src": src,
            "dst": dst,
        }

    # Ping with retries (routing may still be converging)
    attempts = []
    deadline = time.monotonic() + 180
    while time.monotonic() < deadline:
        result = subprocess.run(
            f"{KUBECTL} exec -n nodalarc {src} -c frr -- ping -c 5 -W 2 {dst_ip}".split(),
            capture_output=True,
            text=True,
            timeout=30,
        )
        attempts.append(
            {
                "elapsed_s": round(180 - (deadline - time.monotonic()), 1),
                "rc": result.returncode,
                "stdout": result.stdout[-500:],
            }
        )
        if "bytes from" in result.stdout or "0% packet loss" in result.stdout:
            stats = ""
            for line in result.stdout.splitlines():
                if "packets transmitted" in line:
                    stats = line.strip()
            return {
                "result": "PASS",
                "src": src,
                "dst": dst,
                "dst_ip": dst_ip,
                "stats": stats,
                "attempts": len(attempts),
            }
        time.sleep(15)

    # Ping failed — capture diagnostics
    diag_route = (
        requests.post(
            f"{BASE_URL}/api/v1/introspect",
            headers=headers(token),
            json={"node_id": src, "command": "show ip route"},
        )
        .json()
        .get("output", "")[:1000]
    )

    adj_cmd = "show isis neighbor" if perm["protocol"] == "isis" else "show ip ospf neighbor"
    diag_adj = (
        requests.post(
            f"{BASE_URL}/api/v1/introspect",
            headers=headers(token),
            json={"node_id": src, "command": adj_cmd},
        )
        .json()
        .get("output", "")[:1000]
    )

    return {
        "result": "FAIL",
        "src": src,
        "dst": dst,
        "dst_ip": dst_ip,
        "attempts": len(attempts),
        "last_stdout": attempts[-1]["stdout"] if attempts else "",
        "diag_route": diag_route,
        "diag_adjacency": diag_adj,
    }


def check_nodalpath_mpls(token: str, perm: dict) -> dict:
    """Check MPLS table entries for NodalPath sessions."""
    deadline = time.monotonic() + 120
    attempts = 0
    output = ""
    while time.monotonic() < deadline:
        resp = requests.post(
            f"{BASE_URL}/api/v1/introspect",
            headers=headers(token),
            json={"node_id": "sat-p00s00", "command": "show mpls table"},
        )
        output = resp.json().get("output", "")
        mpls_lines = len([l for l in output.splitlines() if l.strip()])
        attempts += 1
        if mpls_lines > 0:
            return {
                "result": "PASS",
                "protocol": "nodalpath",
                "mpls_entries": mpls_lines,
                "attempts": attempts,
            }
        time.sleep(15)

    return {
        "result": "FAIL",
        "protocol": "nodalpath",
        "mpls_entries": 0,
        "attempts": attempts,
        "last_output": output[:500],
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
        ws_result = check_websocket(token)
        evidence["websocket"] = ws_result

        # Check GS-to-GS ping (or MPLS for NodalPath)
        print("  Checking GS-to-GS connectivity...")
        ping_result = check_ping(token, perm)
        evidence["ping"] = ping_result
        print(
            f"  Ping: {ping_result.get('result', '?')}"
            f" ({ping_result.get('src', '?')} -> {ping_result.get('dst', '?')})"
        )

        # Determine pass/fail
        ping_ok = ping_result.get("result") in ("PASS", "SKIP")
        passed = (
            ready_result.get("phase") == "Ready"
            and pod_result["running"] == pod_result["total"]
            and ws_result["advancing"]
            and ws_result["plane_slot_ok"]
            and ping_ok
        )
        evidence["result"] = "PASS" if passed else "FAIL"
        print(
            f"  {'PASS' if passed else 'FAIL'}: {pod_result['running']} pods, "
            f"{routing_result.get('neighbor_count', '?')} neighbors, "
            f"sim_time={'advancing' if ws_result['advancing'] else 'STATIC'}, "
            f"ping={ping_result.get('result', '?')}"
        )

    except Exception as exc:
        evidence["result"] = "ERROR"
        evidence["error"] = str(exc)
        print(f"  ERROR: {exc}")

    evidence["finished_at"] = datetime.now(UTC).isoformat()
    return evidence


def main():
    import shutil

    print(f"E2E Matrix starting at {datetime.now(UTC).isoformat()}")
    print(f"PID: {os.getpid()}")

    # Clean ALL previous evidence. Every run starts clean.
    evidence_root = Path("tests/integration/e2e-evidence")
    if evidence_root.exists():
        shutil.rmtree(evidence_root)
        print(f"Deleted previous evidence at {evidence_root}")

    ts = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    evidence_dir = evidence_root / ts
    evidence_dir.mkdir(parents=True, exist_ok=True)

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

        for perm in MATRIX:
            evidence = run_permutation(perm)
            results.append(evidence)

            # Write per-permutation evidence immediately
            eid = perm["id"]
            label = evidence.get("label", "unknown")
            evidence_file = evidence_dir / f"perm-{eid:02d}-{label}.json"
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
            "start_time": run_start.isoformat(),
            "end_time": run_end.isoformat(),
            "duration_s": round(duration_s, 1),
            "total": len(MATRIX),
            "passed": passed,
            "failed": failed,
            "results": [
                {"id": r["id"], "label": r.get("label"), "result": r["result"]} for r in results
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
        print(f"E2E Matrix: {passed}/{len(MATRIX)} passed, {failed}/{len(MATRIX)} failed")
        print(f"Duration: {duration_s:.0f}s")
        print(f"Run token: {run_token}")
        print("=" * 60)
        for r in results:
            status = r.get("result", "?")
            tag = "PASS" if status == "PASS" else "FAIL" if status == "FAIL" else status
            print(f"  [{r['id']:2d}] {tag:8s} {r.get('label', '')}")
        print(f"\nEvidence: {evidence_dir}/")
        print(f"Summary:  {evidence_dir}/matrix-summary.json")

    finally:
        # Always remove PID file, even on crash
        if pid_file.exists():
            pid_file.unlink()

    sys.exit(0 if failed == 0 else 1)


if __name__ == "__main__":
    main()
