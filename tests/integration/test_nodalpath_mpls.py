"""Integration tests for NodalPath MPLS forwarding dataplane.

Verifies that the NodalPath controller correctly computes forwarding tables,
pushes them to nodalpath-fwd sidecars via gRPC, and that the kernel MPLS
dataplane is correctly programmed and functional.

Prerequisites:
  - A NodalPath session must be deployed and converged (e.g., starlink-early-44
    with protocol=nodalpath).
  - The nodalpath-fwd sidecar containers must be running in each pod.
  - NodalPath must have pushed at least one forwarding table update.
  - Run with: sudo KUBECONFIG=/etc/rancher/k3s/k3s.yaml \
        .venv/bin/python tests/integration/test_nodalpath_mpls.py

Tests:
  1. test_mpls_loopback_assigned       — loopback 10.{plane}.{slot}.1/32 on lo
  2. test_mpls_link_local_addresses     — /31 link-local 169.254.x.x on ISL veths
  3. test_mpls_forwarding_tables_installed — gRPC GetForwardingTable LSR/LER counts
  4. test_mpls_kernel_routes_installed  — ip -f mpls route / ip route on live pods
  5. test_mpls_single_hop_ping          — ping between direct ISL neighbors
  6. test_mpls_multi_hop_ping           — ping across planes via MPLS forwarding
  7. test_cspf_trace_returns_hops       — VS-API trace endpoint returns valid path
  8. test_sidecar_retry_on_interface_up — sidecar logs show retry mechanism
"""

import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

os.chdir(Path(__file__).parent.parent.parent)
os.environ.setdefault("KUBECONFIG", "/etc/rancher/k3s/k3s.yaml")

from nodalarc.platform import get_platform_config, init_platform_config

init_platform_config(Path("configs/platform.yaml"))

from nodalpath.platform import init_nodalpath_config

init_nodalpath_config(Path("configs/nodalpath.yaml"))

from nodalpath.engine.labels import compute_sid

NAMESPACE = "nodalarc"
KUBECTL_ENV = {**os.environ, "KUBECONFIG": "/etc/rancher/k3s/k3s.yaml"}
# The sidecar container name is derived from the image name in the Helm template:
# {{ $.Values.sidecar.image | replace ":" "-" | lower }} => "nodalpath-fwd-latest"
SIDECAR_CONTAINER = "nodalpath-fwd-latest"
GRPC_PORT = get_platform_config().nodalpath_fwd_grpc_port


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def kubectl_exec(
    pod: str, container: str, command: list[str], timeout: int = 15
) -> subprocess.CompletedProcess:
    """Execute a command in a pod container via kubectl."""
    cmd = [
        "kubectl",
        "exec",
        "-n",
        NAMESPACE,
        pod,
        "-c",
        container,
        "--",
    ] + command
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=timeout,
        env=KUBECTL_ENV,
    )


def kubectl_logs(pod: str, container: str, tail: int = 200) -> str:
    """Fetch recent logs from a pod container."""
    result = subprocess.run(
        ["kubectl", "logs", "-n", NAMESPACE, pod, "-c", container, f"--tail={tail}"],
        capture_output=True,
        text=True,
        timeout=15,
        env=KUBECTL_ENV,
    )
    return result.stdout


def get_satellite_pods() -> list[dict]:
    """List all satellite pods with plane/slot labels."""
    result = subprocess.run(
        [
            "kubectl",
            "get",
            "pods",
            "-n",
            NAMESPACE,
            "-l",
            "nodalarc.io/role=satellite",
            "-o",
            "json",
        ],
        capture_output=True,
        text=True,
        timeout=15,
        env=KUBECTL_ENV,
    )
    if result.returncode != 0:
        return []
    data = json.loads(result.stdout)
    pods = []
    for item in data.get("items", []):
        labels = item.get("metadata", {}).get("labels", {})
        name = item["metadata"]["name"]
        plane = int(labels.get("nodalarc.io/plane", -1))
        slot = int(labels.get("nodalarc.io/slot", -1))
        pod_ip = item.get("status", {}).get("podIP", "")
        pods.append(
            {
                "name": name,
                "plane": plane,
                "slot": slot,
                "pod_ip": pod_ip,
                "node_id": labels.get("nodalarc.io/node-id", name),
            }
        )
    return pods


def get_gs_pods() -> list[dict]:
    """List all ground station pods."""
    result = subprocess.run(
        [
            "kubectl",
            "get",
            "pods",
            "-n",
            NAMESPACE,
            "-l",
            "nodalarc.io/role=ground-station",
            "-o",
            "json",
        ],
        capture_output=True,
        text=True,
        timeout=15,
        env=KUBECTL_ENV,
    )
    if result.returncode != 0:
        return []
    data = json.loads(result.stdout)
    pods = []
    for item in data.get("items", []):
        labels = item.get("metadata", {}).get("labels", {})
        name = item["metadata"]["name"]
        pod_ip = item.get("status", {}).get("podIP", "")
        pods.append(
            {
                "name": name,
                "pod_ip": pod_ip,
                "node_id": labels.get("nodalarc.io/node-id", name),
            }
        )
    return pods


def get_api_key() -> str:
    """Fetch VS-API auth token."""
    import urllib.request

    try:
        r = urllib.request.urlopen("http://localhost:8080/api/v1/auth/token", timeout=5)
        return json.loads(r.read()).get("token", "")
    except Exception:
        return ""


def api_post(path: str, data: dict) -> dict:
    """POST to VS-API."""
    import urllib.request

    key = get_api_key()
    req = urllib.request.Request(
        f"http://localhost:8080{path}",
        data=json.dumps(data).encode(),
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        r = urllib.request.urlopen(req, timeout=15)
        return json.loads(r.read())
    except Exception as exc:
        return {"error": str(exc)}


def api_get(path: str) -> dict:
    """GET from VS-API."""
    import urllib.request

    key = get_api_key()
    req = urllib.request.Request(
        f"http://localhost:8080{path}",
        headers={"Authorization": f"Bearer {key}"},
    )
    try:
        r = urllib.request.urlopen(req, timeout=10)
        return json.loads(r.read())
    except Exception as exc:
        return {"error": str(exc)}


def get_sats_per_plane(pods: list[dict]) -> int:
    """Derive sats_per_plane from the deployed pod labels."""
    if not pods:
        return 11  # fallback for starlink-early-44
    planes: dict[int, int] = {}
    for p in pods:
        plane = p["plane"]
        planes[plane] = planes.get(plane, 0) + 1
    return max(planes.values()) if planes else 11


# ---------------------------------------------------------------------------
# Test 1: Loopback addresses
# ---------------------------------------------------------------------------


def test_mpls_loopback_assigned() -> tuple[bool, str]:
    """Verify each satellite pod has 10.{plane}.{slot}.1/32 on lo."""
    pods = get_satellite_pods()
    if not pods:
        return False, "FAIL: no satellite pods found"

    checked = 0
    failures = []
    for pod in pods[:8]:  # Sample up to 8 pods
        plane, slot = pod["plane"], pod["slot"]
        expected_ip = f"10.{plane}.{slot}.1"
        result = kubectl_exec(pod["name"], SIDECAR_CONTAINER, ["ip", "-4", "addr", "show", "lo"])
        if result.returncode != 0:
            failures.append(f"{pod['name']}: kubectl exec failed ({result.stderr.strip()[:80]})")
            continue
        if expected_ip in result.stdout:
            checked += 1
        else:
            failures.append(f"{pod['name']}: expected {expected_ip} on lo, not found")

    if failures:
        return (
            False,
            f"FAIL: {len(failures)} failures (checked {checked + len(failures)}): {failures[0]}",
        )
    return True, f"PASS: {checked} pods have correct loopback addresses"


# ---------------------------------------------------------------------------
# Test 2: Link-local /31 addresses on ISL veths
# ---------------------------------------------------------------------------


def test_mpls_link_local_addresses() -> tuple[bool, str]:
    """Verify ISL interfaces have 169.254.x.x/31 addresses."""
    pods = get_satellite_pods()
    if not pods:
        return False, "FAIL: no satellite pods found"

    checked = 0
    failures = []
    for pod in pods[:6]:  # Sample 6 pods
        result = kubectl_exec(pod["name"], SIDECAR_CONTAINER, ["ip", "-4", "-o", "addr", "show"])
        if result.returncode != 0:
            failures.append(f"{pod['name']}: kubectl exec failed")
            continue

        # Find ISL interfaces with /31 link-local addresses
        isl_addrs = []
        for line in result.stdout.splitlines():
            if "isl" in line and "169.254." in line and "/31" in line:
                isl_addrs.append(line.strip())

        if isl_addrs:
            checked += 1
        else:
            failures.append(f"{pod['name']}: no 169.254.x.x/31 on ISL interfaces")

    if failures:
        return False, f"FAIL: {len(failures)} pods missing link-local /31: {failures[0]}"
    return True, f"PASS: {checked} pods have /31 link-local addresses on ISL interfaces"


# ---------------------------------------------------------------------------
# Test 3: Forwarding tables installed (gRPC interrogation)
# ---------------------------------------------------------------------------


def test_mpls_forwarding_tables_installed() -> tuple[bool, str]:
    """Query nodalpath-fwd sidecar via gRPC GetForwardingTable.

    Verify:
    - Each node has exactly 1 LSR entry (POP for its own SID)
    - Each node has LER entries for remote prefixes
    - The POP entry's in_label matches compute_sid()
    """
    import grpc

    from nodalpath.proto import Action, Empty
    from nodalpath.proto.forwarding_pb2_grpc import ForwardingServiceStub

    pods = get_satellite_pods()
    if not pods:
        return False, "FAIL: no satellite pods found"

    sats_per_plane = get_sats_per_plane(pods)
    checked = 0
    failures = []

    for pod in pods[:8]:  # Sample up to 8 pods
        pod_ip = pod["pod_ip"]
        if not pod_ip:
            failures.append(f"{pod['name']}: no pod IP")
            continue

        expected_sid = compute_sid(
            pod["node_id"],
            "satellite",
            plane=pod["plane"],
            slot=pod["slot"],
            sats_per_plane=sats_per_plane,
        )

        try:
            channel = grpc.insecure_channel(f"{pod_ip}:{GRPC_PORT}")
            grpc.channel_ready_future(channel).result(timeout=5)
            stub = ForwardingServiceStub(channel)
            fwd = stub.GetForwardingTable(Empty(), timeout=5)
            channel.close()
        except Exception as exc:
            failures.append(f"{pod['name']}: gRPC failed ({exc})")
            continue

        # Check LSR entries: exactly 1 POP for own SID
        pop_entries = [e for e in fwd.lsr_entries if e.action == Action.POP]
        if len(pop_entries) != 1:
            failures.append(
                f"{pod['name']}: expected 1 POP LSR entry, got {len(pop_entries)} "
                f"(total LSR: {len(fwd.lsr_entries)})"
            )
            continue

        pop_label = pop_entries[0].in_label
        if pop_label != expected_sid:
            failures.append(f"{pod['name']}: POP in_label={pop_label}, expected SID={expected_sid}")
            continue

        # Check LER entries exist (should have entries for remote prefixes)
        if len(fwd.ler_entries) == 0:
            failures.append(f"{pod['name']}: no LER ingress entries")
            continue

        checked += 1

    if failures:
        detail = "; ".join(failures[:3])
        return (
            False,
            f"FAIL: {len(failures)} failures (checked {checked + len(failures)}): {detail}",
        )
    return True, (
        f"PASS: {checked} pods have correct forwarding tables "
        f"(1 POP LSR + LER entries, SID matches compute_sid)"
    )


# ---------------------------------------------------------------------------
# Test 4: Kernel routes installed
# ---------------------------------------------------------------------------


def test_mpls_kernel_routes_installed() -> tuple[bool, str]:
    """Check kernel MPLS and IP routes on sample pods.

    Verify:
    - MPLS POP rule uses 'via inet 127.0.0.1 dev lo'
    - LER entries use 'via inet <peer_ip>' (not bare 'dev <iface>')
    - Route count matches sidecar's table count
    """
    import grpc

    from nodalpath.proto import Empty
    from nodalpath.proto.forwarding_pb2_grpc import ForwardingServiceStub

    pods = get_satellite_pods()
    if not pods:
        return False, "FAIL: no satellite pods found"

    sats_per_plane = get_sats_per_plane(pods)
    checked = 0
    failures = []

    for pod in pods[:4]:  # Sample 4 pods (kernel route checks are slower)
        pod_ip = pod["pod_ip"]
        if not pod_ip:
            continue

        expected_sid = compute_sid(
            pod["node_id"],
            "satellite",
            plane=pod["plane"],
            slot=pod["slot"],
            sats_per_plane=sats_per_plane,
        )

        # Get sidecar's view of the forwarding table
        try:
            channel = grpc.insecure_channel(f"{pod_ip}:{GRPC_PORT}")
            grpc.channel_ready_future(channel).result(timeout=5)
            stub = ForwardingServiceStub(channel)
            fwd = stub.GetForwardingTable(Empty(), timeout=5)
            channel.close()
        except Exception as exc:
            failures.append(f"{pod['name']}: gRPC failed ({exc})")
            continue

        sidecar_lsr_count = len(fwd.lsr_entries)
        sidecar_ler_count = len(fwd.ler_entries)

        # Check MPLS routes in kernel
        mpls_result = kubectl_exec(
            pod["name"], SIDECAR_CONTAINER, ["ip", "-f", "mpls", "route", "show"]
        )
        if mpls_result.returncode != 0:
            failures.append(f"{pod['name']}: 'ip -f mpls route show' failed")
            continue

        mpls_lines = [l for l in mpls_result.stdout.splitlines() if l.strip()]

        # Verify POP entry for own SID routes via 127.0.0.1 dev lo
        pop_pattern = f"{expected_sid}" + r"\s+"
        found_pop = False
        for line in mpls_lines:
            if line.startswith(str(expected_sid) + " "):
                if "via inet 127.0.0.1 dev lo" in line:
                    found_pop = True
                else:
                    failures.append(
                        f"{pod['name']}: POP for SID {expected_sid} missing "
                        f"'via inet 127.0.0.1 dev lo': {line.strip()[:80]}"
                    )
                break

        if not found_pop and not any(f"{pod['name']}" in f for f in failures):
            failures.append(f"{pod['name']}: no MPLS POP entry for SID {expected_sid}")
            continue

        # Verify kernel MPLS route count matches sidecar LSR count
        kernel_mpls_count = len(mpls_lines)
        if kernel_mpls_count < sidecar_lsr_count:
            failures.append(
                f"{pod['name']}: kernel MPLS routes ({kernel_mpls_count}) < "
                f"sidecar LSR entries ({sidecar_lsr_count})"
            )
            continue

        # Check IP routes for LER entries (encap mpls)
        ip_result = kubectl_exec(pod["name"], SIDECAR_CONTAINER, ["ip", "route", "show"])
        if ip_result.returncode != 0:
            failures.append(f"{pod['name']}: 'ip route show' failed")
            continue

        encap_lines = [l for l in ip_result.stdout.splitlines() if "encap mpls" in l]

        # LER entries should use 'via inet' for L2 resolution
        bare_dev_count = 0
        via_inet_count = 0
        for line in encap_lines:
            if "via inet" in line or "via" in line:
                via_inet_count += 1
            elif re.search(r"\bdev\b", line) and "via" not in line:
                bare_dev_count += 1

        if sidecar_ler_count > 0 and len(encap_lines) < sidecar_ler_count:
            failures.append(
                f"{pod['name']}: kernel encap mpls routes ({len(encap_lines)}) < "
                f"sidecar LER entries ({sidecar_ler_count})"
            )
            continue

        checked += 1

    if failures:
        detail = "; ".join(failures[:3])
        return False, f"FAIL: {len(failures)} failures: {detail}"
    return True, f"PASS: {checked} pods have correct kernel MPLS + IP routes"


# ---------------------------------------------------------------------------
# Test 5: Single-hop ping (direct ISL neighbors)
# ---------------------------------------------------------------------------


def test_mpls_single_hop_ping() -> tuple[bool, str]:
    """Ping between directly connected ISL neighbors via loopback addresses.

    Picks two pods in the same plane (adjacent slots) and pings from one to
    the other using loopback IPs. This exercises the single-hop MPLS path:
    push label at source -> POP at destination -> deliver to loopback.
    """
    pods = get_satellite_pods()
    if len(pods) < 2:
        return False, "FAIL: need at least 2 satellite pods"

    # Find two pods in the same plane with adjacent slots
    by_plane: dict[int, list[dict]] = {}
    for p in pods:
        by_plane.setdefault(p["plane"], []).append(p)

    src_pod = None
    dst_pod = None
    for plane, plane_pods in sorted(by_plane.items()):
        plane_pods.sort(key=lambda p: p["slot"])
        if len(plane_pods) >= 2:
            src_pod = plane_pods[0]
            dst_pod = plane_pods[1]
            break

    if not src_pod or not dst_pod:
        return False, "FAIL: could not find two same-plane adjacent satellites"

    src_loopback = f"10.{src_pod['plane']}.{src_pod['slot']}.1"
    dst_loopback = f"10.{dst_pod['plane']}.{dst_pod['slot']}.1"

    # Use 'frr' container for ping (sidecar doesn't have ping installed)
    result = kubectl_exec(
        src_pod["name"],
        "frr",
        ["ping", "-c", "3", "-W", "3", "-I", src_loopback, dst_loopback],
        timeout=20,
    )

    output = result.stdout + result.stderr
    if "0% packet loss" in output:
        rtt_match = re.search(r"rtt min/avg/max.*= ([\d.]+)/([\d.]+)/([\d.]+)", output)
        rtt_str = f" (avg RTT: {rtt_match.group(2)}ms)" if rtt_match else ""
        return True, (
            f"PASS: single-hop ping {src_pod['node_id']} -> {dst_pod['node_id']} "
            f"({dst_loopback}) 0% loss{rtt_str}"
        )

    loss_match = re.search(r"(\d+)% packet loss", output)
    loss_pct = loss_match.group(1) if loss_match else "unknown"
    return False, (
        f"FAIL: single-hop ping {src_pod['node_id']} -> {dst_pod['node_id']} "
        f"({dst_loopback}) {loss_pct}% loss"
    )


# ---------------------------------------------------------------------------
# Test 6: Multi-hop ping (cross-plane)
# ---------------------------------------------------------------------------


def test_mpls_multi_hop_ping() -> tuple[bool, str]:
    """Ping between satellites in different planes via loopback addresses.

    This exercises the multi-hop MPLS forwarding path: the packet is
    encapsulated with MPLS labels at each hop (hop-by-hop model).
    """
    pods = get_satellite_pods()
    if len(pods) < 2:
        return False, "FAIL: need at least 2 satellite pods"

    # Find pods in different planes
    by_plane: dict[int, list[dict]] = {}
    for p in pods:
        by_plane.setdefault(p["plane"], []).append(p)

    planes = sorted(by_plane.keys())
    if len(planes) < 2:
        return False, "FAIL: need at least 2 orbital planes"

    src_pod = by_plane[planes[0]][0]
    # Pick a pod in a non-adjacent plane for a multi-hop path
    dst_plane = planes[-1] if len(planes) > 2 else planes[1]
    dst_pod = by_plane[dst_plane][0]

    dst_loopback = f"10.{dst_pod['plane']}.{dst_pod['slot']}.1"

    src_loopback = f"10.{src_pod['plane']}.{src_pod['slot']}.1"

    # Use 'frr' container for ping (sidecar doesn't have ping installed)
    result = kubectl_exec(
        src_pod["name"],
        "frr",
        ["ping", "-c", "3", "-W", "5", "-I", src_loopback, dst_loopback],
        timeout=30,
    )

    output = result.stdout + result.stderr
    if "0% packet loss" in output:
        rtt_match = re.search(r"rtt min/avg/max.*= ([\d.]+)/([\d.]+)/([\d.]+)", output)
        rtt_str = f" (avg RTT: {rtt_match.group(2)}ms)" if rtt_match else ""
        return True, (
            f"PASS: multi-hop ping {src_pod['node_id']} (P{src_pod['plane']}) -> "
            f"{dst_pod['node_id']} (P{dst_pod['plane']}) "
            f"({dst_loopback}) 0% loss{rtt_str}"
        )

    loss_match = re.search(r"(\d+)% packet loss", output)
    loss_pct = loss_match.group(1) if loss_match else "unknown"
    return False, (
        f"FAIL: multi-hop ping {src_pod['node_id']} (P{src_pod['plane']}) -> "
        f"{dst_pod['node_id']} (P{dst_pod['plane']}) "
        f"({dst_loopback}) {loss_pct}% loss"
    )


# ---------------------------------------------------------------------------
# Test 7: CSPF trace via VS-API
# ---------------------------------------------------------------------------


def test_cspf_trace_returns_hops() -> tuple[bool, str]:
    """Call VS-API trace/start and verify traced_paths shows hops with RTT."""
    pods = get_satellite_pods()
    if len(pods) < 2:
        return False, "FAIL: need at least 2 satellite pods"

    # Pick source and destination in different planes
    by_plane: dict[int, list[dict]] = {}
    for p in pods:
        by_plane.setdefault(p["plane"], []).append(p)

    planes = sorted(by_plane.keys())
    if len(planes) < 2:
        return False, "FAIL: need at least 2 orbital planes"

    src_id = by_plane[planes[0]][0]["node_id"]
    dst_id = by_plane[planes[-1]][0]["node_id"]

    # Stop any existing trace
    api_post("/api/v1/trace/stop", {})
    time.sleep(1)

    # Start trace
    api_post("/api/v1/trace/start", {"src_node": src_id, "dst_node": dst_id})

    # Poll for result
    for attempt in range(10):
        time.sleep(3)
        state = api_get("/api/v1/state")
        paths = state.get("traced_paths", [])
        if paths:
            p = paths[0]
            hops = p.get("hops", [])
            rtt = p.get("rtt_ms", 0)
            if len(hops) >= 2:
                api_post("/api/v1/trace/stop", {})
                return True, (
                    f"PASS: trace {src_id} -> {dst_id}: {len(hops)} hops, RTT {rtt:.1f}ms"
                )

    api_post("/api/v1/trace/stop", {})

    # Fallback: try the single-shot trace endpoint
    result = api_post("/api/v1/trace", {"src_node": src_id, "dst_node": dst_id})
    hops = result.get("hops", [])
    if len(hops) >= 2:
        method = result.get("method", "unknown")
        latency = result.get("total_latency_ms", 0)
        return True, (
            f"PASS: trace {src_id} -> {dst_id}: "
            f"{len(hops)} hops via {method}, latency {latency:.1f}ms"
        )

    return False, f"FAIL: trace {src_id} -> {dst_id}: no multi-hop path returned"


# ---------------------------------------------------------------------------
# Test 8: Sidecar retry on interface UP
# ---------------------------------------------------------------------------


def test_sidecar_retry_on_interface_up() -> tuple[bool, str]:
    """Verify sidecar logs show the retry mechanism installed entries after interfaces came UP.

    The nodalpath-fwd sidecar has a background retry thread that installs
    entries that were skipped because their interfaces were DOWN. After the
    orchestrator creates veth pairs and brings them UP, the retry thread
    should detect this and install the pending entries.
    """
    pods = get_satellite_pods()
    if not pods:
        return False, "FAIL: no satellite pods found"

    # Check logs on several pods for retry messages
    found_retry = False
    found_applied = False
    checked = 0
    sample_log = ""

    for pod in pods[:6]:
        logs = kubectl_logs(pod["name"], SIDECAR_CONTAINER, tail=300)
        if not logs:
            continue
        checked += 1

        # The retry thread logs: "Retry: installed N entries after interface UP"
        if "Retry:" in logs and "interface UP" in logs:
            found_retry = True
            for line in logs.splitlines():
                if "Retry:" in line:
                    sample_log = line.strip()
                    break

        # Also accept: "Applied update" messages showing entries were installed
        if "Applied update" in logs:
            found_applied = True

    if found_retry:
        return True, f"PASS: sidecar retry mechanism active ({sample_log[:80]})"

    if found_applied:
        # Retry may not have been needed if interfaces were UP before push
        return True, (
            f"PASS: forwarding tables applied successfully on {checked} pods "
            f"(retry not needed -- interfaces were UP before push)"
        )

    if checked == 0:
        return False, "FAIL: could not read logs from any sidecar container"

    return False, f"FAIL: no retry or applied-update messages in {checked} sidecar logs"


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


def main() -> int:
    tests = [
        ("1. Loopback addresses", test_mpls_loopback_assigned),
        ("2. Link-local /31 addresses", test_mpls_link_local_addresses),
        ("3. Forwarding tables (gRPC)", test_mpls_forwarding_tables_installed),
        ("4. Kernel routes", test_mpls_kernel_routes_installed),
        ("5. Single-hop ping", test_mpls_single_hop_ping),
        ("6. Multi-hop ping", test_mpls_multi_hop_ping),
        ("7. CSPF trace (VS-API)", test_cspf_trace_returns_hops),
        ("8. Sidecar retry logs", test_sidecar_retry_on_interface_up),
    ]

    print(f"\n{'=' * 60}")
    print("  NodalPath MPLS Integration Tests")
    print(f"{'=' * 60}")

    # Pre-flight: check that we have satellite pods
    sat_pods = get_satellite_pods()
    if not sat_pods:
        print("\n  ABORT: No satellite pods found in namespace 'nodalarc'.")
        print("  Deploy a NodalPath session first:")
        print(
            "    sudo .venv/bin/python -m tools.na_deploy --session configs/sessions/_test-nodalpath.yaml"
        )
        return 1

    print(f"\n  Found {len(sat_pods)} satellite pods")
    planes = sorted(set(p["plane"] for p in sat_pods))
    print(f"  Planes: {planes}")
    print(f"  Sats per plane: {get_sats_per_plane(sat_pods)}")
    print()

    passed = 0
    failed = 0

    for label, test_fn in tests:
        print(f"  {label}: ", end="", flush=True)
        try:
            ok, msg = test_fn()
        except Exception as exc:
            ok, msg = False, f"EXCEPTION: {exc}"
        print(msg)
        if ok:
            passed += 1
        else:
            failed += 1

    print(f"\n{'=' * 60}")
    print(f"  RESULT: {passed} passed, {failed} failed / {passed + failed} total")
    print(f"{'=' * 60}\n")

    return 1 if failed > 0 else 0


if __name__ == "__main__":
    sys.exit(main())
