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
from datetime import UTC, datetime, timedelta
from pathlib import Path

import requests

VS_API_HOST = os.environ.get("VS_API_HOST", "192.168.10.201:8080")
BASE_URL = f"http://{VS_API_HOST}"
KUBECTL = "sudo KUBECONFIG=/etc/rancher/k3s/k3s.yaml kubectl"


# Helper to build inline constellation dicts matching the wizard's geometry presets.
def _inline(
    name, alt, inc, pattern, planes, spp, phase, sat_type="starlink-v2", seam=False, seam_lat=70
):
    d = {
        "mode": "parametric",
        "name": name,
        "satellite_type": sat_type,
        "orbit": {"altitude_km": alt, "inclination_deg": inc, "pattern": pattern},
        "planes": {
            "count": planes,
            "sats_per_plane": spp,
            "raan_spacing_deg": round(360 / planes, 2),
            "phase_offset_deg": phase,
        },
    }
    if seam:
        d["polar_seam"] = {"enabled": True, "latitude_threshold_deg": seam_lat}
    return d


# Real-world constellation geometries from the wizard's GEOMETRY_PRESETS
STARLINK_53 = _inline("starlink-53", 550, 53, "walker-delta", 8, 11, 4.1)
STARLINK_70 = _inline("starlink-70", 570, 70, "walker-delta", 6, 11, 5.45)
STARLINK_POLAR = _inline(
    "starlink-polar", 560, 97.6, "walker-star", 6, 12, 5.0, seam=True, seam_lat=80
)
KUIPER_51 = _inline("kuiper-51", 630, 51.9, "walker-delta", 6, 11, 5.45)
ONEWEB = _inline("oneweb", 1200, 87.9, "walker-star", 6, 10, 6.0, seam=True, seam_lat=75)
IRIDIUM = _inline(
    "iridium-next",
    780,
    86.4,
    "walker-star",
    6,
    11,
    5.45,
    sat_type="iridium-next",
    seam=True,
    seam_lat=75,
)
TELESAT = _inline("telesat-polar", 1015, 98.98, "walker-star", 6, 13, 4.6, seam=True, seam_lat=80)
SDA_T1 = _inline("sda-t1", 1000, 80, "walker-star", 6, 10, 6.0, seam=True, seam_lat=70)
GLOBALSTAR = _inline("globalstar", 1414, 52, "walker-delta", 8, 6, 7.5)

MATRIX = [
    # --- Inclined LEO constellations (Walker-delta, no polar seam) ---
    {
        "id": 1,
        "constellation": "starlink-early-44",  # preset for defaults
        "protocol": "isis",
        "extensions": ["te"],
        "gs": "configs/ground-stations/sets/global-8.yaml",
        "custom_constellation": STARLINK_53,
    },
    {
        "id": 2,
        "constellation": "starlink-early-44",
        "protocol": "ospf",
        "extensions": ["te"],
        "gs": "configs/ground-stations/sets/transatlantic.yaml",
        "custom_constellation": KUIPER_51,
    },
    {
        "id": 3,
        "constellation": "starlink-early-44",
        "protocol": "isis",
        "extensions": [],
        "gs": "configs/ground-stations/sets/transpacific.yaml",
        "custom_constellation": GLOBALSTAR,
    },
    # --- Polar/near-polar constellations (Walker-star, polar seam) ---
    {
        "id": 4,
        "constellation": "starlink-early-44",
        "protocol": "isis",
        "extensions": ["sr"],
        "gs": "configs/ground-stations/sets/polar-emphasis.yaml",
        "custom_constellation": IRIDIUM,
    },
    {
        "id": 5,
        "constellation": "starlink-early-44",
        "protocol": "ospf",
        "extensions": [],
        "gs": "configs/ground-stations/sets/polar-emphasis.yaml",
        "custom_constellation": ONEWEB,
    },
    {
        "id": 6,
        "constellation": "starlink-early-44",
        "protocol": "isis",
        "extensions": ["te"],
        "gs": "configs/ground-stations/sets/global.yaml",
        "custom_constellation": TELESAT,
    },
    # --- Sun-synchronous / high-inclination ---
    {
        "id": 7,
        "constellation": "starlink-early-44",
        "protocol": "isis",
        "extensions": ["te"],
        "gs": ["ashburn", "frankfurt", "tokyo", "sydney"],
        "custom_constellation": STARLINK_POLAR,
    },
    {
        "id": 8,
        "constellation": "starlink-early-44",
        "protocol": "ospf",
        "extensions": ["te", "mpls"],
        "gs": "configs/ground-stations/sets/global-8.yaml",
        "custom_constellation": SDA_T1,
    },
    # --- Satellite type override (orthogonal selection) ---
    {
        "id": 9,
        "constellation": "starlink-early-44",
        "protocol": "isis",
        "extensions": ["te"],
        "gs": "configs/ground-stations/sets/transatlantic.yaml",
        "satellite_type": "iridium-next",
        "custom_constellation": STARLINK_53,
    },
    {
        "id": 10,
        "constellation": "starlink-early-44",
        "protocol": "isis",
        "extensions": [],
        "gs": "configs/ground-stations/sets/us-conus.yaml",
        "satellite_type": "generic-2isl",
        "custom_constellation": STARLINK_70,
    },
    # --- Area strategies ---
    {
        "id": 11,
        "constellation": "starlink-early-44",
        "protocol": "isis",
        "extensions": ["te"],
        "gs": "configs/ground-stations/sets/global.yaml",
        "area": "per-plane",
        "custom_constellation": KUIPER_51,
    },
    {
        "id": 12,
        "constellation": "starlink-early-44",
        "protocol": "ospf",
        "extensions": ["te"],
        "gs": "configs/ground-stations/sets/global-8.yaml",
        "area": "stripe",
        "custom_constellation": STARLINK_53,
    },
    # --- NodalPath (xfail) ---
    {
        "id": 13,
        "constellation": "starlink-early-44",
        "protocol": "nodalpath",
        "extensions": [],
        "gs": "configs/ground-stations/sets/global.yaml",
        "custom_constellation": STARLINK_53,
        "xfail": "NodalPath in-band terrestrial interface not yet implemented.",
    },
]

MBB_ACCEPTANCE_SESSION = Path("configs/sessions/earth-leo-handover-mbb.yaml")
MBB_BAD_OPS_CODES = {
    "KERNEL_DIRTY",
    "ACTUATION_BLOCKED",
    "ACTUATION_HALTED",
    "AUTHORITY_SUBSET_VIOLATION",
    "OPERATOR_REPAIR_REQUESTED",
    "OPERATOR_REPAIR_SUCCEEDED",
    "OPERATOR_REPAIR_FAILED",
}


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


def phase6_progress(message: str) -> None:
    print(f"[phase6] {message}", flush=True)


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
    if perm.get("satellite_type"):
        body["satellite_type"] = perm["satellite_type"]
    if perm.get("custom_constellation"):
        body["custom_constellation"] = perm["custom_constellation"]
    payload = request_json("POST", "/api/v1/session/generate", token=token, json=body, retries=3)
    return payload.get("yaml", "")


def deploy_session(token: str, yaml_str: str) -> dict:
    """Deploy session via wizard API."""
    return request_json(
        "POST",
        "/api/v1/session/deploy",
        token=token,
        json={"yaml": yaml_str},
        retries=3,
    )


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

    # Phase 2: Wait for VS-API to expose a live, non-empty state snapshot.
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

    # Pick first satellite
    nodes = request_json("GET", "/api/v1/state", token=token).get("nodes", [])
    sat = next((n for n in nodes if n.get("node_id", "").startswith("sat-")), None)
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


def check_websocket(token: str) -> dict:
    """Check WebSocket delivers advancing sim_time."""
    state1 = request_json("GET", "/api/v1/state", token=token)
    t1 = state1.get("sim_time", "")
    time.sleep(3)
    state2 = request_json("GET", "/api/v1/state", token=token)
    t2 = state2.get("sim_time", "")
    nodes = state2.get("nodes", [])
    sats = [n for n in nodes if n.get("node_id", "").startswith("sat-")]
    plane_ok = all(isinstance(s.get("plane"), int) for s in sats)

    # Retry plane/slot check — PositionEvents may not have reached all nodes yet
    retries = 0
    while not plane_ok and retries < 3:
        time.sleep(10)
        nodes = request_json("GET", "/api/v1/state", token=token).get("nodes", [])
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


def _derive_loopback_ip(node_id: str, gs_nodes: list) -> str | None:
    """Derive loopback IP from node_id using addressing scheme."""
    import re

    sat_match = re.match(r"sat-P(\d+)S(\d+)", node_id, re.IGNORECASE)
    if sat_match:
        return f"10.{int(sat_match.group(1))}.{int(sat_match.group(2))}.1"
    # GS — find index from position in sorted gs list
    gs_ids = sorted(n["node_id"] for n in gs_nodes)
    try:
        gs_idx = gs_ids.index(node_id)
        return f"10.255.{gs_idx + 1}.1"
    except ValueError:
        return None


def check_ping(token: str, perm: dict) -> dict:
    """Prove routed connectivity for the declared topology.

    Ground sessions must prove a ground-originated routed ping and routing adjacency.
    Satellite-only sessions fall back to an ISL loopback ping. SKIP is valid only when
    the session declares no ground endpoint and no connected satellite pair exists.
    """
    import subprocess

    protocol = perm["protocol"]
    if protocol == "nodalpath":
        return check_nodalpath_mpls(token, perm)

    state = request_json("GET", "/api/v1/state", token=token)
    nodes = state.get("nodes", [])
    links = state.get("links", [])
    if isinstance(links, dict):
        links = list(links.values())

    gs_nodes = [n for n in nodes if n.get("node_id", "").startswith("gs-")]
    sat_nodes = [n for n in nodes if n.get("node_id", "").startswith("sat-")]
    declares_ground = _perm_declares_ground(perm)

    if declares_ground or gs_nodes:
        if declares_ground and not gs_nodes:
            return {
                "result": "FAIL",
                "mode": "ground_to_ground",
                "reason": "declared ground topology materialized no ground nodes",
                "ground_declared": True,
                "ground_node_count": 0,
                "active_link_count": len([l for l in links if l.get("state") == "active"]),
            }
        ground_probe = _find_routed_ground_probe(token, protocol=protocol, wait_s=120)
        if ground_probe and ground_probe.get("result") == "PASS":
            return {
                **ground_probe,
                "ground_declared": declares_ground,
                "ground_node_count": len(gs_nodes),
            }
        return {
            "result": "FAIL",
            "mode": "ground_to_ground",
            "reason": (ground_probe or {}).get("reason", "ground connectivity was not proven"),
            "ground_declared": declares_ground,
            "ground_node_count": len(gs_nodes),
            "active_link_count": len([l for l in links if l.get("state") == "active"]),
            "last_probe": ground_probe,
        }

    # Find active links to identify connected pairs in satellite-only sessions.
    active_links = [l for l in links if l.get("state") == "active"]

    # Strategy 1: Find two satellites connected by an ISL
    src = None
    dst = None
    for link in active_links:
        a = link.get("node_a", "")
        b = link.get("node_b", "")
        if a.startswith("sat-") and b.startswith("sat-"):
            src = a
            dst = b
            break

    # Strategy 2: If no ISL link, find a satellite connected to a GS
    if not src:
        for link in active_links:
            a = link.get("node_a", "")
            b = link.get("node_b", "")
            if a.startswith("gs-") and b.startswith("sat-"):
                src, dst = b, a
                break
            if a.startswith("sat-") and b.startswith("gs-"):
                src, dst = a, b
                break

    if not src or not dst:
        return {
            "result": "SKIP",
            "reason": f"No connected node pairs found ({len(active_links)} active links, "
            f"{len(sat_nodes)} sats, {len(gs_nodes)} gs)",
            "active_link_count": len(active_links),
        }

    dst_ip = _derive_loopback_ip(dst, gs_nodes)
    if not dst_ip:
        return {"result": "FAIL", "reason": f"Cannot derive IP for {dst}", "src": src, "dst": dst}

    # Ping with retries
    attempts = []
    deadline = time.monotonic() + 120  # 2 minutes
    while time.monotonic() < deadline:
        result = subprocess.run(
            f"{KUBECTL} exec -n nodalarc {src.lower()} -c frr -- ping -c 3 -W 2 {dst_ip}",
            capture_output=True,
            text=True,
            timeout=30,
            shell=True,
        )
        attempts.append(
            {
                "elapsed_s": round(120 - (deadline - time.monotonic()), 1),
                "rc": result.returncode,
                "stdout": result.stdout[-300:],
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
        time.sleep(10)

    return {
        "result": "FAIL",
        "src": src,
        "dst": dst,
        "dst_ip": dst_ip,
        "attempts": len(attempts),
        "last_stdout": attempts[-1]["stdout"] if attempts else "",
    }


def check_nodalpath_mpls(token: str, perm: dict) -> dict:
    """Check MPLS route entries for NodalPath sessions.

    NodalPath installs MPLS routes in the kernel via pyroute2 (not through FRR),
    so we check 'ip -f mpls route show' via kubectl exec (not vtysh introspect).
    """
    import subprocess

    deadline = time.monotonic() + 120
    attempts = 0
    output = ""
    while time.monotonic() < deadline:
        result = subprocess.run(
            f"{KUBECTL} exec -n nodalarc sat-p00s00 -c frr -- ip -f mpls route show",
            capture_output=True,
            text=True,
            timeout=10,
            shell=True,
        )
        output = result.stdout
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


def _active_ground_pair(token: str) -> tuple[str, str, str] | None:
    state = request_json("GET", "/api/v1/state", token=token)
    nodes = state.get("nodes", [])
    links = state.get("links", [])
    if isinstance(links, dict):
        links = list(links.values())
    gs_nodes = [n for n in nodes if n.get("node_id", "").startswith("gs-")]
    for link in links:
        if link.get("state") != "active":
            continue
        a = link.get("node_a", "")
        b = link.get("node_b", "")
        if a.startswith("gs-") and b.startswith("sat-"):
            dst_ip = _derive_loopback_ip(b, gs_nodes)
            if dst_ip:
                return a, b, dst_ip
        if a.startswith("sat-") and b.startswith("gs-"):
            dst_ip = _derive_loopback_ip(a, gs_nodes)
            if dst_ip:
                return b, a, dst_ip
    return None


def _kubectl_exec(node_id: str, command: str, *, timeout: int = 20) -> dict:
    import subprocess

    result = subprocess.run(
        f"{KUBECTL} exec -n nodalarc {node_id.lower()} -c frr -- {command}",
        capture_output=True,
        text=True,
        timeout=timeout,
        shell=True,
    )
    return {
        "rc": result.returncode,
        "stdout": result.stdout[-1000:],
        "stderr": result.stderr[-1000:],
    }


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
    return f"_g{int(gs_ifname[4:])}-{gs_id.removeprefix('gs-')}"


def _force_ground_host_interface_down(gs_id: str, gs_ifname: str, *, timeout: int = 20) -> dict:
    host_ifname = _host_ground_ifname(gs_id, gs_ifname)
    node_result = _run_shell(
        f"{KUBECTL} get pod -n nodalarc {gs_id.lower()} -o jsonpath={{.spec.nodeName}}",
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


def check_mbb_lifecycle_and_ops(token: str, *, wait_s: int = 180) -> dict:
    deadline = time.monotonic() + wait_s
    last_events: list[dict] = []
    while time.monotonic() < deadline:
        events = request_json("GET", "/api/v1/ops/events?limit=500", token=token)
        last_events = events
        lifecycle = [
            event
            for event in events
            if event.get("source") == "ome" and event.get("code") == "MBB_TEARDOWN_TERMINAL"
        ]
        completed = [
            event
            for event in lifecycle
            if (event.get("details") or {}).get("terminal_outcome") == "teardown_completed"
        ]
        bad = [event for event in events if event.get("code") in MBB_BAD_OPS_CODES]
        if completed or bad:
            return {
                "result": "PASS" if completed and not bad else "FAIL",
                "lifecycle_count": len(lifecycle),
                "completed_count": len(completed),
                "bad_ops_codes": [event.get("code") for event in bad],
                "last_lifecycle": lifecycle[-3:],
            }
        time.sleep(5)
    return {
        "result": "FAIL",
        "reason": f"No completed MBB lifecycle event within {wait_s}s",
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
    by_gs: dict[str, list[dict]] = {}
    for link in links:
        if link.get("state") != "active":
            continue
        a = link.get("node_a", "")
        b = link.get("node_b", "")
        if a.startswith("gs-") and b.startswith("sat-"):
            by_gs.setdefault(a, []).append(link)
        elif b.startswith("gs-") and a.startswith("sat-"):
            by_gs.setdefault(b, []).append(link)
    return by_gs


def _node_loopback_ip(node_id: str) -> str | None:
    if node_id.startswith("sat-"):
        return _derive_loopback_ip(node_id, [])
    out = _kubectl_exec(node_id, "ip -4 -o addr show lo", timeout=10)
    if out["rc"] != 0:
        return None
    for part in out["stdout"].split():
        if part.startswith("10.255.") and "/" in part:
            return part.split("/", 1)[0]
    return None


def _ground_node_ids(state: dict) -> list[str]:
    return sorted(
        n.get("node_id", "")
        for n in state.get("nodes", [])
        if n.get("node_id", "").startswith("gs-")
    )


def _find_routed_ground_probe(
    token: str, *, protocol: str = "isis", wait_s: int = 180
) -> dict | None:
    deadline = time.monotonic() + wait_s
    last_reason = "no routed ground probe found"
    while time.monotonic() < deadline:
        state = request_json("GET", "/api/v1/state", token=token)
        ground_ids = _ground_node_ids(state)
        by_gs = _ground_links_by_gs(state)
        for src in sorted(by_gs):
            for dst_gs in ground_ids:
                if dst_gs == src:
                    continue
                dst_ip = _node_loopback_ip(dst_gs)
                if not dst_ip:
                    last_reason = f"could not read loopback for {dst_gs}"
                    continue
                route = _kubectl_exec(src, f"ip route get {dst_ip}", timeout=10)
                neigh = _kubectl_exec(
                    src, f"vtysh -c '{_routing_neighbor_command(protocol)}'", timeout=10
                )
                ping = _kubectl_exec(src, f"ping -c 1 -W 1 {dst_ip}", timeout=10)
                fib_ready = route["rc"] == 0 and dst_ip in route["stdout"]
                neighbor_up = neigh["rc"] == 0 and _routing_neighbor_up(neigh["stdout"], protocol)
                packet_ready = ping["rc"] == 0 and "0% packet loss" in ping["stdout"]
                if fib_ready and neighbor_up and packet_ready:
                    return {
                        "result": "PASS",
                        "mode": "ground_to_ground",
                        "protocol": protocol,
                        "key": f"{src}->{dst_gs}",
                        "src": src,
                        "dst_gs": dst_gs,
                        "dst": dst_gs,
                        "dst_ip": dst_ip,
                        "active_ground_links": by_gs[src],
                        "fib_ready": fib_ready,
                        "neighbor_up": neighbor_up,
                        "packet_ready": packet_ready,
                        "route_stdout": route["stdout"],
                        "isis_stdout": neigh["stdout"],
                        "ping_stdout": ping["stdout"],
                    }
                last_reason = (
                    f"{src}->{dst_gs} fib={fib_ready} neighbor={neighbor_up} packet={packet_ready}"
                )
        time.sleep(3)
    return {"result": "FAIL", "reason": last_reason}


def _find_all_routed_ground_probes(token: str, *, protocol: str = "isis") -> list[dict]:
    state = request_json("GET", "/api/v1/state", token=token)
    ground_ids = _ground_node_ids(state)
    by_gs = _ground_links_by_gs(state)
    probes: list[dict] = []
    for src in sorted(by_gs):
        for dst_gs in ground_ids:
            if dst_gs == src:
                continue
            dst_ip = _node_loopback_ip(dst_gs)
            if not dst_ip:
                continue
            route = _kubectl_exec(src, f"ip route get {dst_ip}", timeout=10)
            neigh = _kubectl_exec(
                src, f"vtysh -c '{_routing_neighbor_command(protocol)}'", timeout=10
            )
            ping = _kubectl_exec(src, f"ping -c 1 -W 1 {dst_ip}", timeout=10)
            fib_ready = route["rc"] == 0 and dst_ip in route["stdout"]
            neighbor_up = neigh["rc"] == 0 and _routing_neighbor_up(neigh["stdout"], protocol)
            packet_ready = ping["rc"] == 0 and "0% packet loss" in ping["stdout"]
            if fib_ready and neighbor_up and packet_ready:
                probes.append(
                    {
                        "mode": "ground_to_ground",
                        "protocol": protocol,
                        "key": f"{src}->{dst_gs}",
                        "src": src,
                        "dst_gs": dst_gs,
                        "dst_ip": dst_ip,
                        "active_ground_links": by_gs[src],
                        "fib_ready": fib_ready,
                        "neighbor_up": neighbor_up,
                        "packet_ready": packet_ready,
                        "route_stdout": route["stdout"],
                        "isis_stdout": neigh["stdout"],
                        "ping_stdout": ping["stdout"],
                    }
                )
    return probes


def check_mbb_convergence_preconditions(token: str) -> dict:
    probe = _find_routed_ground_probe(token, wait_s=180)
    if not probe or probe.get("result") == "FAIL":
        return probe or {"result": "FAIL", "reason": "No routed ground probe found"}
    return {"result": "PASS", **probe}


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


def _seq_near_ranges(
    seq: int | None, ranges: list[list[int]], *, tolerance: int = 5
) -> bool | None:
    if seq is None:
        return None
    return any(start - tolerance <= seq <= end + tolerance for start, end in ranges)


def _packet_handover_correlation(output: dict, terminal_observation: dict | None) -> dict:
    missing_ranges = output.get("missing_ranges") or []
    overlap_seq = (output.get("overlap_observation") or {}).get("estimated_ping_seq")
    terminal_seq = (terminal_observation or {}).get("estimated_ping_seq")
    return {
        "missing_ranges": missing_ranges,
        "overlap_estimated_ping_seq": overlap_seq,
        "terminal_estimated_ping_seq": terminal_seq,
        "loss_near_overlap": _seq_near_ranges(overlap_seq, missing_ranges),
        "loss_near_terminal": _seq_near_ranges(terminal_seq, missing_ranges),
    }


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
        probe for probe in probes if overlap_by_key.get(probe["key"], {}).get("successor_fib_ready")
    ]
    with_overlap = [probe for probe in probes if probe["key"] in overlap_by_key]
    selected = (with_ready_fib or with_overlap or probes)[0]
    return selected["key"], overlap_by_key.get(selected["key"])


def _ping_packet_outcome(stdout: str, stderr: str, returncode: int | None) -> dict:
    stats = ""
    for line in stdout.splitlines():
        if "packets transmitted" in line:
            stats = line.strip()
            zero_loss = "0% packet loss" in line
            return {
                "packet_outcome": "zero_loss" if zero_loss else "loss_observed",
                "zero_loss": zero_loss,
                "protocol_observed": True,
                "stats": stats,
                "reply_count": None,
                "missing_ranges": [],
            }
    if "Network unreachable" in stderr or "Network unreachable" in stdout:
        return {
            "packet_outcome": "routing_unreachable",
            "zero_loss": False,
            "protocol_observed": True,
            "stats": "network unreachable",
            "reply_count": None,
            "missing_ranges": [],
        }
    seqs: list[int] = []
    for line in stdout.splitlines():
        marker = "seq="
        if marker not in line:
            continue
        tail = line.split(marker, 1)[1]
        token = tail.split(None, 1)[0]
        try:
            seqs.append(int(token))
        except ValueError:
            continue
    if not seqs:
        return {
            "packet_outcome": "no_replies" if not stderr.strip() else "probe_error",
            "zero_loss": False,
            "protocol_observed": not stderr.strip(),
            "stats": "no ICMP replies observed",
            "returncode": returncode,
        }
    expected = list(range(min(seqs), max(seqs) + 1))
    if seqs == expected and not stderr.strip():
        return {
            "packet_outcome": "zero_loss",
            "zero_loss": True,
            "protocol_observed": True,
            "stats": f"{len(seqs)} contiguous replies seq={seqs[0]}..{seqs[-1]}",
            "reply_count": len(seqs),
            "first_reply_seq": seqs[0],
            "last_reply_seq": seqs[-1],
            "missing_ranges": [],
        }
    missing = sorted(set(expected) - set(seqs))
    return {
        "packet_outcome": "loss_observed",
        "zero_loss": False,
        "protocol_observed": True,
        "stats": f"missing ICMP sequence(s): {missing[:20]}",
        "reply_count": len(seqs),
        "first_reply_seq": seqs[0],
        "last_reply_seq": seqs[-1],
        "missing_ranges": _sequence_ranges(missing),
    }


def _route_dev(route_stdout: str) -> str | None:
    parts = route_stdout.split()
    for idx, part in enumerate(parts[:-1]):
        if part == "dev":
            return parts[idx + 1]
    return None


def _successor_interface(active_links: list[dict]) -> str | None:
    gained = [link for link in active_links if link.get("link_reason") == "vis_gained"]
    if len(gained) == 1:
        return gained[0].get("interface_a")
    return None


def _run_mbb_packet_window(token: str, *, count: int = 1200, interval_s: float = 0.2) -> dict:
    import signal
    import subprocess

    probes = _find_all_routed_ground_probes(token)
    if not probes:
        return {"result": "FAIL", "reason": "No routed ground probes found"}

    started_at = datetime.now(UTC)
    deadline = time.monotonic() + min(max(count * interval_s + 30, 120), 300)
    procs: dict[str, subprocess.Popen] = {}
    proc_started_mono: dict[str, float] = {}
    proc_started_wall: dict[str, str] = {}
    probe_by_key = {probe["key"]: probe for probe in probes}
    probes_by_src: dict[str, list[dict]] = {}
    for probe in probes:
        probes_by_src.setdefault(probe["src"], []).append(probe)
    overlap_by_key: dict[str, dict] = {}
    terminal_by_src: dict[str, dict] = {}
    terminal_observation_by_key: dict[str, dict] = {}
    bad_events: list[dict] = []
    selected_key: str | None = None
    terminal_seen_at: float | None = None

    for probe in probes:
        key = probe["key"]
        src = probe["src"]
        dst_ip = probe["dst_ip"]
        cmd = (
            f"{KUBECTL} exec -n nodalarc {src.lower()} -c frr -- "
            f"ping -c {count} -i {interval_s} -W 1 {dst_ip}"
        )
        proc_started_mono[key] = time.monotonic()
        proc_started_wall[key] = datetime.now(UTC).isoformat()
        procs[key] = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            shell=True,
            start_new_session=True,
        )

    try:
        while time.monotonic() < deadline:
            state = request_json("GET", "/api/v1/state", token=token)
            by_gs = _ground_links_by_gs(state)
            for src, active_for_src in by_gs.items():
                if src not in probes_by_src or len(active_for_src) < 2:
                    continue
                successor_if = _successor_interface(active_for_src)
                if successor_if is None:
                    continue
                neigh = _kubectl_exec(src, "vtysh -c 'show isis neighbor'", timeout=10)
                neighbor_up = neigh["rc"] == 0 and "Up" in neigh["stdout"]
                for probe in probes_by_src[src]:
                    key = probe["key"]
                    if key in overlap_by_key:
                        continue
                    fib = _kubectl_exec(src, f"ip route get {probe['dst_ip']}", timeout=10)
                    route_dev = _route_dev(fib["stdout"])
                    successor_fib_ready = (
                        fib["rc"] == 0
                        and probe["dst_ip"] in fib["stdout"]
                        and route_dev == successor_if
                    )
                    overlap_observed_mono = time.monotonic()
                    overlap_by_key[key] = {
                        "sim_time": state.get("sim_time"),
                        "observed_wall_time": datetime.now(UTC).isoformat(),
                        "estimated_ping_seq": int(
                            (overlap_observed_mono - proc_started_mono[key]) / interval_s
                        ),
                        "active_ground_links": active_for_src,
                        "successor_interface": successor_if,
                        "route_dev": route_dev,
                        "neighbor_up": neighbor_up,
                        "successor_fib_ready": successor_fib_ready,
                        "isis_stdout": neigh["stdout"],
                        "fib_stdout": fib["stdout"],
                    }

            events = request_json("GET", "/api/v1/ops/events?limit=500", token=token)
            for event in events:
                if not _event_at_or_after(event, started_at):
                    continue
                if event.get("code") in MBB_BAD_OPS_CODES:
                    bad_events.append(event)
                details = event.get("details") or {}
                src = details.get("gs_id")
                if (
                    event.get("source") == "ome"
                    and event.get("code") == "MBB_TEARDOWN_TERMINAL"
                    and src in probes_by_src
                    and details.get("terminal_outcome") == "teardown_completed"
                ):
                    terminal_by_src[src] = event
                    selected_terminal_key, selected_overlap = _select_terminal_probe(
                        probes_by_src[src], overlap_by_key
                    )
                    if selected_terminal_key is None:
                        continue
                    observed_mono = time.monotonic()
                    for probe in probes_by_src[src]:
                        key = probe["key"]
                        overlap = overlap_by_key.get(key)
                        terminal_observation_by_key[key] = {
                            "event_timestamp": event.get("timestamp"),
                            "observed_wall_time": datetime.now(UTC).isoformat(),
                            "estimated_ping_seq": int(
                                (observed_mono - proc_started_mono[key]) / interval_s
                            ),
                            "routing_layer_outcome": _routing_layer_outcome(overlap),
                        }
                    if selected_key is None:
                        selected_key = selected_terminal_key
                        terminal_seen_at = observed_mono
                        if selected_overlap is None:
                            overlap_by_key.setdefault(
                                selected_terminal_key,
                                {
                                    "observed_wall_time": datetime.now(UTC).isoformat(),
                                    "routing_layer_outcome": "overlap_not_sampled",
                                    "successor_fib_ready": False,
                                },
                            )
            if selected_key is not None and terminal_seen_at is not None:
                if time.monotonic() - terminal_seen_at >= 5:
                    break
            if all(proc.poll() is not None for proc in procs.values()):
                break
            time.sleep(0.5)
    finally:
        outputs: dict[str, dict] = {}
        for key, proc in procs.items():
            if proc.poll() is None:
                try:
                    os.killpg(proc.pid, signal.SIGINT)
                except ProcessLookupError:
                    pass
            try:
                stdout, stderr = proc.communicate(timeout=15)
            except subprocess.TimeoutExpired:
                proc.kill()
                stdout, stderr = proc.communicate(timeout=5)
            packet_result = _ping_packet_outcome(stdout, stderr, proc.returncode)
            outputs[key] = {
                **packet_result,
                "returncode": proc.returncode,
                "started_wall_time": proc_started_wall[key],
                "overlap_observation": overlap_by_key.get(key),
                "terminal_observation": terminal_observation_by_key.get(key),
                "stdout": stdout[-4000:],
                "stderr": stderr[-1000:],
            }

    if selected_key is None:
        return {
            "result": "FAIL",
            "reason": "No probed GS completed an MBB teardown during the packet window",
            "probes": probes,
            "overlap_by_key": overlap_by_key,
            "terminal_gs_ids": sorted(terminal_by_src),
            "bad_ops_codes": [event.get("code") for event in bad_events],
            "probe_outputs": outputs,
        }

    overlap = overlap_by_key.get(selected_key)
    output = outputs[selected_key]
    overlap_ready = bool(overlap and overlap.get("successor_fib_ready"))
    passed = _mbb_packet_window_passed(output, overlap, bad_events)
    probe = probe_by_key[selected_key]
    terminal_observation = terminal_observation_by_key.get(selected_key)
    return {
        "result": "PASS" if passed else "FAIL",
        "src": probe["src"],
        "dst_gs": probe["dst_gs"],
        "dst_ip": probe["dst_ip"],
        "count": count,
        "interval_s": interval_s,
        "stats": output["stats"],
        "packet_outcome": output["packet_outcome"],
        "zero_loss": output["zero_loss"],
        "protocol_observed": output["protocol_observed"],
        "overlap_required": True,
        "overlap_ready": overlap_ready,
        "packet_loss_policy": "recorded_not_gated",
        "reply_count": output.get("reply_count"),
        "missing_ranges": output.get("missing_ranges"),
        "overlap_proof": overlap,
        "routing_layer_outcome": _routing_layer_outcome(overlap),
        "terminal_event": terminal_by_src.get(probe["src"]),
        "terminal_observation": terminal_observation,
        "packet_handover_correlation": _packet_handover_correlation(output, terminal_observation),
        "bad_ops_codes": [event.get("code") for event in bad_events],
        "stdout": output["stdout"],
        "stderr": output["stderr"],
    }


def check_mbb_packet_behavior(
    token: str,
    *,
    count: int = 1200,
    interval_s: float = 0.2,
    max_wait_s: int = 900,
) -> dict:
    deadline = time.monotonic() + max_wait_s
    attempts: list[dict] = []
    while time.monotonic() < deadline:
        remaining_s = deadline - time.monotonic()
        if remaining_s < 60:
            break
        window_count = min(count, max(300, int(min(remaining_s, 300) / interval_s)))
        evidence = _run_mbb_packet_window(token, count=window_count, interval_s=interval_s)
        attempts.append(
            {
                "result": evidence.get("result"),
                "reason": evidence.get("reason"),
                "src": evidence.get("src"),
                "dst_gs": evidence.get("dst_gs"),
                "stats": evidence.get("stats"),
                "packet_outcome": evidence.get("packet_outcome"),
                "terminal_gs_ids": evidence.get("terminal_gs_ids"),
                "routing_layer_outcome": evidence.get("routing_layer_outcome"),
            }
        )
        if evidence.get("result") == "PASS":
            evidence["attempts"] = attempts
            return evidence
        if evidence.get("terminal_event") is not None:
            evidence["attempts"] = attempts
            return evidence
    return {
        "result": "FAIL",
        "reason": "No qualifying MBB handover packet observation before timeout",
        "max_wait_s": max_wait_s,
        "attempts": attempts,
    }


def _acceptance_session_yaml(
    *,
    session_name: str,
    clean_kernel_audit_interval_s: float | None = None,
    mbb_overlap_ticks: int | None = None,
) -> str:
    import yaml

    data = yaml.safe_load(MBB_ACCEPTANCE_SESSION.read_text())
    data.setdefault("session", {})["name"] = session_name
    if clean_kernel_audit_interval_s is not None:
        data.setdefault("dispatch", {})["clean_kernel_audit_interval_s"] = (
            clean_kernel_audit_interval_s
        )
    if mbb_overlap_ticks is not None:
        data.setdefault("scheduling", {}).setdefault("ground", {})["mbb_overlap_ticks"] = (
            mbb_overlap_ticks
        )
    return yaml.safe_dump(data, sort_keys=False)


def _active_ground_link_with_interfaces(token: str, *, wait_s: int = 180) -> dict | None:
    deadline = time.monotonic() + wait_s
    while time.monotonic() < deadline:
        state = request_json("GET", "/api/v1/state", token=token)
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
            if a.startswith("gs-") and b.startswith("sat-"):
                row = {"gs_id": a, "sat_id": b, "gs_ifname": ia, "sat_ifname": ib}
            elif a.startswith("sat-") and b.startswith("gs-"):
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
            node.get("node_id")
            for node in state.get("nodes", [])
            if str(node.get("node_id", "")).startswith("gs-")
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


def run_phase6_dirty_repair_acceptance() -> dict:
    evidence: dict = {
        "id": "P6-REPAIR",
        "label": "forced-kernel-dirty-operator-repair",
        "session_file": str(MBB_ACCEPTANCE_SESSION),
        "started_at": datetime.now(UTC).isoformat(),
    }
    if not MBB_ACCEPTANCE_SESSION.exists():
        return {**evidence, "result": "ERROR", "error": "MBB acceptance session missing"}
    try:
        phase6_progress("dirty-repair: acquiring token")
        token = get_token()
        phase6_progress("dirty-repair: deploying session")
        yaml_str = _acceptance_session_yaml(
            session_name=f"phase6-dirty-repair-{int(time.time())}",
            clean_kernel_audit_interval_s=2.0,
        )
        evidence["deploy_response"] = deploy_session(token, yaml_str)
        if evidence["deploy_response"].get("status") != "switching":
            evidence["result"] = "FAIL"
            evidence["error"] = f"Deploy rejected: {evidence['deploy_response']}"
            return evidence
        phase6_progress("dirty-repair: waiting for session readiness")
        ready_result = wait_for_ready(token, timeout=600)
        evidence["ready_result"] = ready_result
        phase6_progress(f"dirty-repair: readiness result {ready_result}")
        if ready_result.get("phase") != "Ready":
            evidence["result"] = "FAIL"
            evidence["error"] = f"Did not reach Ready: {ready_result}"
            return evidence

        phase6_progress("dirty-repair: waiting for scheduler actuation roster")
        roster = _wait_for_scheduler_actuation_roster(token, wait_s=180)
        evidence["scheduler_actuation_roster"] = roster
        phase6_progress(f"dirty-repair: roster result {roster.get('result')}")
        if roster.get("result") != "PASS":
            evidence["result"] = "FAIL"
            evidence["error"] = "Scheduler actuation startup roster did not complete"
            return evidence

        phase6_progress("dirty-repair: waiting for active ground link candidate")
        time.sleep(20)
        token = get_token()
        pair = _active_ground_link_with_interfaces(token, wait_s=240)
        phase6_progress(f"dirty-repair: selected pair {pair}")
        evidence["selected_pair"] = pair
        if not pair:
            evidence["result"] = "FAIL"
            evidence["error"] = "No active ground link with interfaces found"
            return evidence

        gs_id = pair["gs_id"]
        sat_id = pair["sat_id"]
        gs_ifname = pair["gs_ifname"]
        break_started = datetime.now(UTC)
        phase6_progress(f"dirty-repair: forcing host peer for {gs_id} {gs_ifname} down")
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

        phase6_progress(f"dirty-repair: waiting for {gs_id} kernel_dirty")
        dirty = _wait_for_actuation_state(token, gs_id, "kernel_dirty", wait_s=240)
        evidence["dirty_observation"] = dirty
        phase6_progress(f"dirty-repair: dirty observation {dirty.get('result')}")
        evidence["events_after_forced_mutation"] = _events_since(token, break_started)
        if dirty.get("result") != "PASS":
            evidence["result"] = "FAIL"
            evidence["error"] = "Forced kernel mutation did not produce kernel_dirty state"
            return evidence

        intervention_id = f"phase6-repair-{int(time.time())}"
        phase6_progress(f"dirty-repair: requesting repair {intervention_id}")
        repair_response = request_json(
            "POST",
            "/api/v1/ops/repair",
            token=token,
            json={
                "gs_id": gs_id,
                "reason": "Phase 6 acceptance: repair a deliberately induced kernel mismatch",
                "intervention_id": intervention_id,
            },
            retries=3,
        )
        evidence["repair_response"] = repair_response
        if repair_response.get("status") != "accepted":
            evidence["result"] = "FAIL"
            evidence["error"] = "Operator repair was not accepted"
            return evidence

        phase6_progress(f"dirty-repair: waiting for {gs_id} clean after repair")
        clean = _wait_for_actuation_state(token, gs_id, "clean", wait_s=180)
        phase6_progress(f"dirty-repair: clean observation {clean.get('result')}")
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

    def pair_for_gs(link: dict, gs_id: str) -> list[str] | None:
        a = link.get("node_a", "")
        b = link.get("node_b", "")
        if a == gs_id and b.startswith("sat-"):
            return [gs_id, b]
        if b == gs_id and a.startswith("sat-"):
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
        by_gs = _ground_links_by_gs(state)
        teardown_candidates = []
        multi_link_candidates = []
        for gs_id, links in sorted(by_gs.items()):
            links_by_pair = {
                tuple(pair): link
                for link in links
                if (pair := pair_for_gs(link, gs_id)) is not None
            }
            for link in links:
                is_teardown = (
                    link.get("scheduling_state") == "teardown"
                    or link.get("teardown_remaining_ticks") is not None
                    or bool(link.get("successor_pair"))
                )
                if not is_teardown:
                    continue
                old_pair = pair_for_gs(link, gs_id)
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
            gs_id = old_pair[0] if str(old_pair[0]).startswith("gs-") else old_pair[1]
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
            phase6_progress(
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


def run_phase6_seek_during_mbb_acceptance() -> dict:
    evidence: dict = {
        "id": "P6-SEEK-MBB",
        "label": "seek-during-mbb-overlap",
        "session_file": str(MBB_ACCEPTANCE_SESSION),
        "started_at": datetime.now(UTC).isoformat(),
    }
    if not MBB_ACCEPTANCE_SESSION.exists():
        return {**evidence, "result": "ERROR", "error": "MBB acceptance session missing"}
    try:
        phase6_progress("seek-mbb: acquiring token")
        token = get_token()
        yaml_str = _acceptance_session_yaml(
            session_name=f"phase6-seek-mbb-{int(time.time())}",
            mbb_overlap_ticks=600,
        )
        phase6_progress("seek-mbb: deploying session")
        evidence["deploy_response"] = deploy_session(token, yaml_str)
        if evidence["deploy_response"].get("status") != "switching":
            evidence["result"] = "FAIL"
            evidence["error"] = f"Deploy rejected: {evidence['deploy_response']}"
            return evidence
        ready_result = wait_for_ready(token, timeout=600)
        evidence["ready_result"] = ready_result
        if ready_result.get("phase") != "Ready":
            evidence["result"] = "FAIL"
            evidence["error"] = f"Did not reach Ready: {ready_result}"
            return evidence

        time.sleep(20)
        token = get_token()
        phase6_progress("seek-mbb: waiting for OME teardown-state overlap")
        overlap = _wait_for_mbb_overlap(token, wait_s=900)
        evidence["overlap_observation"] = overlap
        phase6_progress(f"seek-mbb: overlap observation {overlap.get('result')}")
        if overlap.get("result") != "PASS":
            evidence["result"] = "FAIL"
            evidence["error"] = "No MBB overlap available for seek test"
            return evidence

        current_sim = _parse_api_datetime(overlap["sim_time"])
        seek_target = current_sim + timedelta(seconds=30)
        phase6_progress(f"seek-mbb: requesting seek to {seek_target.isoformat()}")
        seek_response = request_json(
            "POST",
            "/api/v1/playback",
            token=token,
            json={"action": "seek", "target_sim_time": seek_target.isoformat()},
            retries=3,
        )
        evidence["seek_request"] = {
            "target_sim_time": seek_target.isoformat(),
            "response": seek_response,
        }
        if seek_response.get("state") != "seeking" or "epoch_id" not in seek_response:
            evidence["result"] = "FAIL"
            evidence["error"] = "Seek was not accepted into seeking state"
            return evidence

        phase6_progress("seek-mbb: waiting for playback to resume")
        resumed = _wait_for_playback_not_seeking(token, int(seek_response["epoch_id"]), wait_s=120)
        evidence["resume_observation"] = resumed
        phase6_progress(f"seek-mbb: resume observation {resumed.get('result')}")
        events = request_json("GET", "/api/v1/ops/events?limit=500", token=token)
        lifecycle = [
            event
            for event in events
            if event.get("source") == "ome" and event.get("code") == "MBB_TEARDOWN_TERMINAL"
        ]
        expected_old_pair = overlap.get("old_pair")
        expected_successor_pair = overlap.get("successor_pair")
        invalidated = [
            event
            for event in lifecycle
            if (event.get("details") or {}).get("terminal_outcome")
            == "teardown_invalidated_by_epoch"
            and (event.get("details") or {}).get("old_pair") == expected_old_pair
            and (event.get("details") or {}).get("successor_pair") == expected_successor_pair
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


def run_mbb_acceptance() -> dict:
    evidence: dict = {
        "id": "C-J",
        "label": "mbb-routing-packet-observation",
        "session_file": str(MBB_ACCEPTANCE_SESSION),
        "started_at": datetime.now(UTC).isoformat(),
    }
    if not MBB_ACCEPTANCE_SESSION.exists():
        return {**evidence, "result": "ERROR", "error": "MBB acceptance session missing"}
    try:
        token = get_token()
        yaml_str = _acceptance_session_yaml(
            session_name=f"phase6-cj-mbb-{int(time.time())}",
            mbb_overlap_ticks=60,
        )
        evidence["yaml_length"] = len(yaml_str)
        evidence["deploy_response"] = deploy_session(token, yaml_str)
        if evidence["deploy_response"].get("status") != "switching":
            evidence["result"] = "FAIL"
            evidence["error"] = f"Deploy rejected: {evidence['deploy_response']}"
            return evidence
        phase6_progress("seek-mbb: waiting for session readiness")
        ready_result = wait_for_ready(token, timeout=600)
        evidence["ready_result"] = ready_result
        phase6_progress(f"seek-mbb: readiness result {ready_result}")
        if ready_result.get("phase") != "Ready":
            evidence["result"] = "FAIL"
            evidence["error"] = f"Did not reach Ready: {ready_result}"
            return evidence

        time.sleep(30)
        token = get_token()
        evidence["convergence_preconditions"] = check_mbb_convergence_preconditions(token)
        evidence["mbb_packet_behavior"] = check_mbb_packet_behavior(token)
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

        # Check declared connectivity. Ground sessions must prove a GS-originated path;
        # satellite-only sessions may fall back to an ISL loopback path.
        print("  Checking declared connectivity...")
        ping_result = check_ping(token, perm)
        evidence["ping"] = ping_result
        print(
            f"  Ping: {ping_result.get('result', '?')}"
            f" ({ping_result.get('src', '?')} -> {ping_result.get('dst', '?')})"
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

    # Clean previous evidence by default. Targeted acceptance runs can append
    # retained artifacts without destroying prior proof by setting
    # NODALARC_PRESERVE_EVIDENCE=1.
    evidence_root = Path("tests/integration/e2e-evidence")
    preserve_evidence = os.environ.get("NODALARC_PRESERVE_EVIDENCE") == "1"
    if evidence_root.exists() and not preserve_evidence:
        shutil.rmtree(evidence_root)
        print(f"Deleted previous evidence at {evidence_root}")
    elif preserve_evidence:
        print(f"Preserving previous evidence at {evidence_root}")

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
        xfailed = 0
        xpassed = 0

        if os.environ.get("NODALARC_PHASE6_ONLY") != "1":
            for perm in MATRIX:
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
                evidence_file = evidence_dir / f"perm-{eid:02d}-{label}.json"
                evidence_file.write_text(json.dumps(evidence, indent=2))

        if os.environ.get("NODALARC_RUN_MBB_ACCEPTANCE") == "1":
            evidence = run_mbb_acceptance()
            results.append(evidence)
            evidence_file = evidence_dir / "phase6-cj-mbb-packet-behavior.json"
            evidence_file.write_text(json.dumps(evidence, indent=2))
            if evidence["result"] == "PASS":
                passed += 1
            else:
                failed += 1

        if os.environ.get("NODALARC_RUN_PHASE6_DIRTY_REPAIR") == "1":
            evidence = run_phase6_dirty_repair_acceptance()
            results.append(evidence)
            evidence_file = evidence_dir / "phase6-dirty-repair.json"
            evidence_file.write_text(json.dumps(evidence, indent=2))
            if evidence["result"] == "PASS":
                passed += 1
            else:
                failed += 1

        if os.environ.get("NODALARC_RUN_PHASE6_SEEK_MBB") == "1":
            evidence = run_phase6_seek_during_mbb_acceptance()
            results.append(evidence)
            evidence_file = evidence_dir / "phase6-seek-during-mbb.json"
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
            "total": len(results),
            "passed": passed,
            "failed": failed,
            "xfailed": xfailed,
            "xpassed": xpassed,
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
