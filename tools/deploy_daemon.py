"""Deploy daemon — runs privileged deploy operations over a Unix domain socket.

This daemon runs as a separate process with sudo access, isolating privilege
from the VS-API web server. VS-API communicates deploy requests over the
socket without needing sudo itself.

Run: sudo python -m tools.deploy_daemon [--socket /tmp/nodal-deploy.sock]

Protocol: newline-delimited JSON over Unix stream socket.
Request:  {"action": "deploy"|"teardown"|"helm_list"|"helm_uninstall"|"kubectl_wait", ...}
Response: {"ok": true|false, "error": "...", ...}
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import signal
import socket
import subprocess
import sys
import threading
from pathlib import Path

from nodalarc.constants import LOG_FORMAT

log = logging.getLogger(__name__)

DEFAULT_SOCKET_PATH = "/tmp/nodal-deploy.sock"
_KUBECONFIG = os.environ.get("KUBECONFIG", "/etc/rancher/k3s/k3s.yaml")
_NAMESPACE = "nodalarc"
_shutdown = threading.Event()


def _env_with_kubeconfig() -> dict[str, str]:
    return {**os.environ, "KUBECONFIG": _KUBECONFIG}


def _chown_session_data(session_path: str) -> None:
    """chown the newest session data directory so non-root VS-API can write the DB."""
    try:
        import yaml
        from nodalarc.models.session import SessionConfig
        raw = yaml.safe_load(Path(session_path).read_text())
        cfg = SessionConfig.model_validate(raw)
        data_base = Path(cfg.session.data_dir)
        if not data_base.is_dir():
            return
        # Find newest subdirectory
        subdirs = sorted(
            [d for d in data_base.iterdir() if d.is_dir()],
            key=lambda d: d.stat().st_mtime, reverse=True,
        )
        if not subdirs:
            return
        newest = subdirs[0]
        # Get the real user (SUDO_UID) or fall back to owner of the project dir
        uid = os.environ.get("SUDO_UID")
        gid = os.environ.get("SUDO_GID")
        if not uid:
            stat = os.stat(Path(__file__).resolve().parent.parent)
            uid, gid = str(stat.st_uid), str(stat.st_gid)
        subprocess.run(
            ["chown", "-R", f"{uid}:{gid}", str(newest)],
            capture_output=True, timeout=30,
        )
        log.info(f"chown {newest.name} to {uid}:{gid}")
    except Exception as exc:
        log.warning(f"chown session data failed: {exc}")


def _handle_deploy(req: dict) -> dict:
    """Run na_deploy for a session."""
    session_path = req.get("session", "")
    if not session_path or not Path(session_path).exists():
        return {"ok": False, "error": f"Session file not found: {session_path}"}

    try:
        proc = subprocess.run(
            [sys.executable, "-u", "-m", "tools.na_deploy",
             "--session", session_path, "--skip-vsapi", "--skip-teardown"],
            capture_output=True, text=True, timeout=600,
            env=_env_with_kubeconfig(),
        )
        ok = proc.returncode == 0
        if ok:
            _chown_session_data(session_path)
        return {
            "ok": ok,
            "returncode": proc.returncode,
            "stdout": proc.stdout[-4096:] if len(proc.stdout) > 4096 else proc.stdout,
            "stderr": proc.stderr[-2048:] if len(proc.stderr) > 2048 else proc.stderr,
        }
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "Deploy timed out (600s)"}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def _handle_deploy_streaming(req: dict, conn: socket.socket) -> None:
    """Run na_deploy, streaming progress lines back over the socket."""
    session_path = req.get("session", "")
    if not session_path or not Path(session_path).exists():
        _send(conn, {"ok": False, "error": f"Session file not found: {session_path}"})
        return

    try:
        proc = subprocess.Popen(
            [sys.executable, "-u", "-m", "tools.na_deploy",
             "--session", session_path, "--skip-vsapi", "--skip-teardown"],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
            env=_env_with_kubeconfig(),
        )
        last_lines: list[str] = []
        for line in proc.stdout:
            line = line.rstrip()
            if line:
                last_lines.append(line)
                if len(last_lines) > 20:
                    last_lines.pop(0)
                _send(conn, {"type": "progress", "line": line})

        proc.wait()
        if proc.returncode != 0:
            tail = "\n".join(last_lines[-5:])
            _send(conn, {"ok": False, "error": f"Deploy failed (rc={proc.returncode}):\n{tail}"})
        else:
            # chown the data directory so the VS-API (non-root) can write to the DB
            _chown_session_data(session_path)
            _send(conn, {"ok": True})
    except Exception as exc:
        _send(conn, {"ok": False, "error": str(exc)})


def _handle_kill_processes(req: dict) -> dict:
    """Kill backend processes by matching known module names."""
    killed = 0
    for module in ("ome.main", "orchestrator.main", "vs_api.main", "measurement.mi_main"):
        result = subprocess.run(
            ["pgrep", "-f", f"python.*-m {module}"],
            capture_output=True, text=True,
        )
        for line in result.stdout.strip().splitlines():
            pid = line.strip()
            if not pid:
                continue
            # Don't kill the deploy daemon's own parent VS-API
            if req.get("exclude_vsapi") and module == "vs_api.main":
                continue
            subprocess.run(["kill", pid], capture_output=True)
            log.info(f"Killed {module} PID {pid}")
            killed += 1
    return {"ok": True, "killed": killed}


_VALID_POD_NAME = re.compile(r"^[a-z0-9][a-z0-9\-]{0,62}$")
_VALID_CONTAINERS = {"frr"}
_ALLOWED_COMMANDS = {
    ("vtysh", "-c", "show isis neighbor"),
    ("vtysh", "-c", "show ip route"),
    ("vtysh", "-c", "show isis database"),
    ("vtysh", "-c", "show interface brief"),
    ("vtysh", "-c", "show ip ospf neighbor"),
    ("vtysh", "-c", "show ip ospf route"),
    ("vtysh", "-c", "show mpls table"),
    ("vtysh", "-c", "show running-config"),
}


def _handle_kubectl_exec(req: dict) -> dict:
    """Run a whitelisted command inside a pod container via kubectl exec."""
    pod = req.get("pod", "")
    container = req.get("container", "frr")
    command = req.get("command", [])
    if not pod or not command:
        return {"ok": False, "error": "pod and command required"}

    # Validate pod name
    if not _VALID_POD_NAME.match(pod):
        return {"ok": False, "error": f"Invalid pod name: {pod}"}

    # Validate container
    if container not in _VALID_CONTAINERS:
        return {"ok": False, "error": f"Container not allowed: {container}"}

    # Validate command against whitelist
    cmd_tuple = tuple(command)
    if cmd_tuple not in _ALLOWED_COMMANDS:
        return {"ok": False, "error": f"Command not in whitelist: {command}"}

    try:
        result = subprocess.run(
            ["kubectl", "exec", "-n", _NAMESPACE, pod, "-c", container, "--"] + command,
            capture_output=True, text=True, timeout=15,
            env=_env_with_kubeconfig(),
        )
        return {
            "ok": result.returncode == 0,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "exit_code": result.returncode,
        }
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "Command timed out"}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def _handle_helm_list(req: dict) -> dict:
    """List helm releases in nodalarc namespace."""
    try:
        result = subprocess.run(
            ["helm", "list", "-n", _NAMESPACE, "-q"],
            capture_output=True, text=True, timeout=30,
            env=_env_with_kubeconfig(),
        )
        releases = [r.strip() for r in result.stdout.strip().split("\n") if r.strip()]
        return {"ok": True, "releases": releases}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def _handle_helm_uninstall(req: dict) -> dict:
    """Uninstall a helm release."""
    release = req.get("release", "")
    if not release:
        return {"ok": False, "error": "release required"}
    try:
        result = subprocess.run(
            ["helm", "uninstall", release, "-n", _NAMESPACE],
            capture_output=True, text=True, timeout=60,
            env=_env_with_kubeconfig(),
        )
        return {"ok": result.returncode == 0, "output": result.stdout}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def _handle_kubectl_wait(req: dict) -> dict:
    """Wait for pods to terminate."""
    try:
        result = subprocess.run(
            ["kubectl", "wait", "--for=delete", "pod",
             "-l", "nodalarc.io/node-id", "-n", _NAMESPACE, "--timeout=60s"],
            capture_output=True, text=True, timeout=90,
            env=_env_with_kubeconfig(),
        )
        return {"ok": True}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def _handle_modprobe_mpls(req: dict) -> dict:
    """Load MPLS kernel modules via modprobe."""
    modules = req.get("modules", ["mpls_router", "mpls_iptunnel"])
    loaded = []
    for mod in modules:
        # Validate module name (alphanumeric + underscore only)
        if not re.match(r"^[a-z0-9_]+$", mod):
            return {"ok": False, "error": f"Invalid module name: {mod}"}
        try:
            subprocess.run(
                ["modprobe", mod],
                capture_output=True, text=True, timeout=15, check=True,
            )
            loaded.append(mod)
        except subprocess.CalledProcessError as exc:
            return {"ok": False, "error": f"modprobe {mod} failed: {exc.stderr}"}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}
    return {"ok": True, "modules": loaded}


def _handle_get_pod_ip(req: dict) -> dict:
    """Get the IP address of a pod."""
    pod = req.get("pod", "")
    namespace = req.get("namespace", _NAMESPACE)
    if not pod:
        return {"ok": False, "error": "pod required"}
    if not _VALID_POD_NAME.match(pod):
        return {"ok": False, "error": f"Invalid pod name: {pod}"}
    try:
        result = subprocess.run(
            ["kubectl", "get", "pod", pod, "-n", namespace,
             "-o", "jsonpath={.status.podIP}"],
            capture_output=True, text=True, timeout=15,
            env=_env_with_kubeconfig(),
        )
        if result.returncode != 0:
            return {"ok": False, "error": result.stderr}
        pod_ip = result.stdout.strip()
        if not pod_ip:
            return {"ok": False, "error": f"Pod {pod} has no IP"}
        return {"ok": True, "pod_ip": pod_ip}
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "kubectl timed out"}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def _send(conn: socket.socket, msg: dict) -> None:
    """Send a JSON message terminated by newline."""
    data = json.dumps(msg) + "\n"
    conn.sendall(data.encode())


def _recv(conn: socket.socket) -> dict | None:
    """Receive a newline-terminated JSON message."""
    buf = b""
    while True:
        chunk = conn.recv(4096)
        if not chunk:
            return None
        buf += chunk
        if b"\n" in buf:
            line = buf[:buf.index(b"\n")]
            return json.loads(line)


def _handle_client(conn: socket.socket, addr: str) -> None:
    """Handle a single client connection."""
    try:
        req = _recv(conn)
        if req is None:
            return

        action = req.get("action", "")
        log.info(f"Deploy daemon: action={action}")

        if action == "deploy_streaming":
            _handle_deploy_streaming(req, conn)
            return
        elif action == "deploy":
            resp = _handle_deploy(req)
        elif action == "kill_processes":
            resp = _handle_kill_processes(req)
        elif action == "kubectl_exec":
            resp = _handle_kubectl_exec(req)
        elif action == "helm_list":
            resp = _handle_helm_list(req)
        elif action == "helm_uninstall":
            resp = _handle_helm_uninstall(req)
        elif action == "kubectl_wait":
            resp = _handle_kubectl_wait(req)
        elif action == "modprobe_mpls":
            resp = _handle_modprobe_mpls(req)
        elif action == "get_pod_ip":
            resp = _handle_get_pod_ip(req)
        elif action == "ping":
            resp = {"ok": True, "pong": True}
        else:
            resp = {"ok": False, "error": f"Unknown action: {action}"}

        _send(conn, resp)
    except Exception as exc:
        log.error(f"Client handler error: {exc}")
        try:
            _send(conn, {"ok": False, "error": str(exc)})
        except Exception:
            pass
    finally:
        conn.close()


def _signal_handler(signum: int, frame: object) -> None:
    log.info("Deploy daemon shutting down")
    _shutdown.set()


def main() -> None:
    logging.basicConfig(format=LOG_FORMAT, level=logging.INFO)
    parser = argparse.ArgumentParser(description="Nodal Arc deploy daemon")
    parser.add_argument("--socket", default=DEFAULT_SOCKET_PATH,
                        help="Unix socket path")
    args = parser.parse_args()

    sock_path = args.socket

    # Clean up stale socket
    if os.path.exists(sock_path):
        os.unlink(sock_path)

    signal.signal(signal.SIGTERM, _signal_handler)
    signal.signal(signal.SIGINT, _signal_handler)

    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(sock_path)
    # Allow VS-API (non-root) to connect via group ownership.
    # Set socket group to the real user's group (SUDO_GID) so only the
    # deploying user and root can connect — not every user on the system.
    gid = os.environ.get("SUDO_GID")
    if gid:
        os.chown(sock_path, 0, int(gid))
    os.chmod(sock_path, 0o660)
    server.listen(2)
    server.settimeout(1.0)

    log.info(f"Deploy daemon listening on {sock_path}")

    while not _shutdown.is_set():
        try:
            conn, addr = server.accept()
            t = threading.Thread(target=_handle_client, args=(conn, addr), daemon=True)
            t.start()
        except socket.timeout:
            continue
        except OSError:
            break

    server.close()
    if os.path.exists(sock_path):
        os.unlink(sock_path)
    log.info("Deploy daemon stopped")


if __name__ == "__main__":
    main()
