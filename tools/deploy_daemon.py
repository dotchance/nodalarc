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


def _handle_deploy(req: dict) -> dict:
    """Run na_deploy for a session."""
    session_path = req.get("session", "")
    if not session_path or not Path(session_path).exists():
        return {"ok": False, "error": f"Session file not found: {session_path}"}

    try:
        proc = subprocess.run(
            [sys.executable, "-u", "-m", "tools.na_deploy",
             "--session", session_path, "--skip-vsapi"],
            capture_output=True, text=True, timeout=600,
            env=_env_with_kubeconfig(),
        )
        return {
            "ok": proc.returncode == 0,
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
             "--session", session_path, "--skip-vsapi"],
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
            _send(conn, {"ok": True})
    except Exception as exc:
        _send(conn, {"ok": False, "error": str(exc)})


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
        elif action == "helm_list":
            resp = _handle_helm_list(req)
        elif action == "helm_uninstall":
            resp = _handle_helm_uninstall(req)
        elif action == "kubectl_wait":
            resp = _handle_kubectl_wait(req)
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
    # Allow VS-API (non-root) to connect
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
