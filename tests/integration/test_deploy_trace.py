"""Integration test: deploy constellation, verify traces work.

Runs 10 iterations across different constellations, protocols, and extensions.
Each iteration: teardown → deploy → wait for convergence → run traces → report.

Usage: sudo KUBECONFIG=/etc/rancher/k3s/k3s.yaml .venv/bin/python tests/integration/test_deploy_trace.py
"""

import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

os.chdir(Path(__file__).parent.parent.parent)
os.environ.setdefault("KUBECONFIG", "/etc/rancher/k3s/k3s.yaml")

# Ensure platform config is initialized
from nodalarc.platform import init_platform_config
init_platform_config(Path("configs/platform.yaml"))


def get_api_key() -> str:
    try:
        r = urllib.request.urlopen("http://localhost:8080/api/v1/auth/token", timeout=5)
        return json.loads(r.read()).get("token", "")
    except Exception:
        return ""


def api_get(path: str) -> dict:
    key = get_api_key()
    req = urllib.request.Request(
        f"http://localhost:8080{path}",
        headers={"Authorization": f"Bearer {key}"},
    )
    try:
        r = urllib.request.urlopen(req, timeout=10)
        return json.loads(r.read())
    except Exception:
        return {}


def api_post(path: str, data: dict) -> dict:
    key = get_api_key()
    req = urllib.request.Request(
        f"http://localhost:8080{path}",
        data=json.dumps(data).encode(),
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        r = urllib.request.urlopen(req, timeout=10)
        return json.loads(r.read())
    except Exception:
        return {}


def teardown():
    subprocess.run(
        ["bash", "tools/na-teardown.sh"],
        capture_output=True, timeout=180,
        env={**os.environ, "KUBECONFIG": "/etc/rancher/k3s/k3s.yaml"},
    )
    time.sleep(3)


def deploy(session_path: str) -> bool:
    env = {**os.environ, "KUBECONFIG": "/etc/rancher/k3s/k3s.yaml"}
    result = subprocess.run(
        [sys.executable, "-m", "tools.na_deploy",
         "--session", session_path, "--skip-teardown"],
        capture_output=True, text=True, timeout=600,
        env=env,
    )
    if result.returncode != 0:
        print(f"  DEPLOY FAILED: {result.stderr[-500:]}")
        return False
    return True


def generate_session(constellation: str, protocol: str, extensions: list[str]) -> str:
    from nodalarc.session_generator import generate_session_yaml
    ext_str = "-".join(extensions) if extensions else "plain"
    name = f"{constellation}-{protocol}-{ext_str}"
    path = f"configs/sessions/_test-{name}.yaml"
    yaml_str, _ = generate_session_yaml(constellation, protocol, extensions)
    Path(path).write_text(yaml_str)
    return path


def do_trace(src: str, dst: str, timeout_s: int = 30) -> tuple[bool, str]:
    """Run a trace and return (success, description)."""
    api_post("/api/v1/trace/stop", {})
    time.sleep(1)
    api_post("/api/v1/trace/start", {"src_node": src, "dst_node": dst})

    for _ in range(timeout_s // 3):
        time.sleep(3)
        state = api_get("/api/v1/state")
        paths = state.get("traced_paths", [])
        if paths:
            p = paths[0]
            hops = p.get("hops", [])
            if len(hops) > 1:
                rtt = p.get("rtt_ms", 0)
                api_post("/api/v1/trace/stop", {})
                return True, f"PASS {len(hops)} hops {rtt:.0f}ms"

    api_post("/api/v1/trace/stop", {})
    return False, "FAIL (no multi-hop result)"


def run_test(
    constellation: str, protocol: str, extensions: list[str],
    label: str, wait_s: int = 90,
) -> tuple[int, int]:
    print(f"\n{'='*50}")
    print(f"  {label}")
    print(f"{'='*50}")

    teardown()

    session_path = generate_session(constellation, protocol, extensions)
    print(f"  Generated: {session_path}")

    if not deploy(session_path):
        return 0, 3

    print(f"  Waiting {wait_s}s for convergence...")
    time.sleep(wait_s)

    # State check
    state = api_get("/api/v1/state")
    nodes = state.get("nodes", [])
    links = state.get("links", [])
    active = [l for l in links if l.get("state") == "active"]
    gs_active = [l for l in active if l["node_a"].startswith("gs-") or l["node_b"].startswith("gs-")]
    connected_gs = sorted(set(
        l["node_a"] if l["node_a"].startswith("gs-") else l["node_b"]
        for l in gs_active
    ))
    sats = [n for n in nodes if n.get("node_type") == "satellite"]
    print(f"  Sats: {len(sats)}, Active: {len(active)} ({len(active)-len(gs_active)} ISL, {len(gs_active)} GS)")
    print(f"  Connected GS: {connected_gs}")

    passed = 0
    failed = 0

    # Sat-to-sat traces
    print(f"  sat-P00S00 -> sat-P02S05: ", end="", flush=True)
    ok, desc = do_trace("sat-P00S00", "sat-P02S05")
    print(desc)
    if ok: passed += 1
    else: failed += 1

    print(f"  sat-P01S00 -> sat-P03S05: ", end="", flush=True)
    ok, desc = do_trace("sat-P01S00", "sat-P03S05")
    print(desc)
    if ok: passed += 1
    else: failed += 1

    # GS-to-GS if 2+ connected
    if len(connected_gs) >= 2:
        src, dst = connected_gs[0], connected_gs[1]
        print(f"  {src} -> {dst} (GS-GS): ", end="", flush=True)
        ok, desc = do_trace(src, dst)
        print(desc)
        if ok: passed += 1
        else:
            failed += 1
            # DIAGNOSE: check IS-IS state on both GS
            print(f"    --- GS-GS FAILURE DIAGNOSIS ---")
            for gs in (src, dst):
                try:
                    r = subprocess.run(
                        ["kubectl", "exec", "-n", "nodalarc", gs, "-c", "frr", "--",
                         "vtysh", "-c", "show isis neighbor"],
                        capture_output=True, text=True, timeout=10,
                        env={**os.environ, "KUBECONFIG": "/etc/rancher/k3s/k3s.yaml"},
                    )
                    neighbor_lines = [l for l in r.stdout.splitlines() if "Up" in l or "Down" in l]
                    print(f"    {gs} IS-IS: {neighbor_lines[0] if neighbor_lines else 'NO NEIGHBOR'}")
                except Exception as e:
                    print(f"    {gs} IS-IS: error ({e})")
            try:
                r = subprocess.run(
                    ["kubectl", "exec", "-n", "nodalarc", src, "-c", "frr", "--",
                     "vtysh", "-c", "show ip route summary"],
                    capture_output=True, text=True, timeout=10,
                    env={**os.environ, "KUBECONFIG": "/etc/rancher/k3s/k3s.yaml"},
                )
                isis_line = [l for l in r.stdout.splitlines() if "isis" in l.lower() or "ospf" in l.lower()]
                print(f"    {src} routes: {isis_line[0].strip() if isis_line else 'NO IGP ROUTES'}")
            except Exception:
                pass
    else:
        print(f"  (No GS-GS pair — {len(connected_gs)} GS connected)")

    print(f"  RESULT: {passed} passed, {failed} failed")
    return passed, failed


if __name__ == "__main__":
    total_pass = 0
    total_fail = 0

    tests = [
        ("starlink-early-44", "isis", ["sr"], "Starlink-44 ISIS+SR"),
        ("starlink-early-44", "ospf", [], "Starlink-44 OSPF plain"),
        ("starlink-early-44", "isis", [], "Starlink-44 ISIS plain"),
        ("starlink-early-44", "ospf", ["te"], "Starlink-44 OSPF+TE"),
        ("starlink-early-44", "isis", ["te"], "Starlink-44 ISIS+TE"),
        ("iridium-small-36", "isis", ["sr"], "Iridium-36 ISIS+SR"),
        ("iridium-small-36", "ospf", [], "Iridium-36 OSPF plain"),
        ("kuiper-50", "isis", ["sr"], "Kuiper-50 ISIS+SR"),
        ("kuiper-50", "ospf", ["te", "mpls"], "Kuiper-50 OSPF+TE+MPLS"),
        ("starlink-early-44", "nodalpath", [], "Starlink-44 NodalPath"),
    ]

    for constellation, protocol, extensions, label in tests:
        p, f = run_test(constellation, protocol, extensions, label)
        total_pass += p
        total_fail += f

    print(f"\n{'='*50}")
    print(f"  FINAL: {total_pass} passed, {total_fail} failed / {total_pass+total_fail} total")
    print(f"{'='*50}")

    sys.exit(1 if total_fail > 0 else 0)
