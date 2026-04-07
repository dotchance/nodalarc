# Copyright 2024-2026 .chance (dotchance)
# Licensed under the NodalArc Source Available License 1.0. See LICENSE file.
"""SSH-over-WebSocket terminal proxy for interactive vtysh access.

Bridges browser WebSocket connections to dropbear SSH sessions in
constellation node pods. Users land in vtysh (FRR CLI) — same experience
as SSHing directly to a real router.

The VS-API is a convenience proxy for browser users. Power users can SSH
directly to pod_ip:22 with their own terminal software (PuTTY, iTerm, etc).
When physical nodes are added, the same proxy connects to their management
IP instead of a pod IP — no code change needed.

Security: key-only SSH auth, no passwords, root login disabled in dropbear.
The SSH private key is read from the nodalarc-terminal-keys K8s Secret.
"""

from __future__ import annotations

import asyncio
import logging
import tempfile
from pathlib import Path

import asyncssh
import kubernetes.client

log = logging.getLogger(__name__)

# Cached SSH private key (loaded lazily from K8s Secret on first terminal request)
_ssh_key_path: str | None = None


def _load_ssh_key(namespace: str) -> str:
    """Load the SSH private key from the K8s Secret into a temp file.

    Returns the path to the temp file. Called once, cached for the VS-API
    process lifetime. The temp file persists until VS-API restart.
    """
    global _ssh_key_path
    if _ssh_key_path and Path(_ssh_key_path).exists():
        return _ssh_key_path

    kubernetes.config.load_incluster_config()
    v1 = kubernetes.client.CoreV1Api()

    try:
        secret = v1.read_namespaced_secret("nodalarc-terminal-keys", namespace)
    except kubernetes.client.rest.ApiException as e:
        if e.status == 404:
            raise RuntimeError(
                "Terminal SSH keys not found (Secret nodalarc-terminal-keys). "
                "Deploy a session first — the Operator generates keys at session creation."
            ) from e
        raise

    import base64

    private_key_b64 = secret.data.get("id_ed25519")
    if not private_key_b64:
        raise RuntimeError("Secret nodalarc-terminal-keys missing id_ed25519 key")

    private_key_pem = base64.b64decode(private_key_b64).decode()

    # Write to a temp file (asyncssh needs a file path for client_keys)
    tmpf = tempfile.NamedTemporaryFile(mode="w", suffix=".key", delete=False)
    tmpf.write(private_key_pem)
    tmpf.close()
    Path(tmpf.name).chmod(0o600)

    _ssh_key_path = tmpf.name
    log.info("SSH private key loaded from Secret (cached at %s)", _ssh_key_path)
    return _ssh_key_path


def resolve_pod_ip(node_id: str, namespace: str) -> str | None:
    """Resolve a constellation node_id to its K8s pod IP."""
    try:
        kubernetes.config.load_incluster_config()
        v1 = kubernetes.client.CoreV1Api()
        pods = v1.list_namespaced_pod(
            namespace,
            label_selector=f"nodalarc.io/node-id={node_id}",
        )
        if pods.items and pods.items[0].status.pod_ip:
            return pods.items[0].status.pod_ip
    except Exception:
        log.exception("Failed to resolve pod IP for %s", node_id)
    return None


class TerminalSession:
    """Manages a single SSH session to a constellation node.

    Lifecycle: connect() → send()/receive()/resize() → close().
    Used by the WebSocket endpoint for browser access, and by the
    config export endpoint for non-interactive command execution.
    """

    def __init__(self, pod_ip: str, ssh_key_path: str):
        self._pod_ip = pod_ip
        self._ssh_key_path = ssh_key_path
        self._conn: asyncssh.SSHClientConnection | None = None
        self._process: asyncssh.SSHClientProcess | None = None

    async def connect(self, term_size: tuple[int, int] = (80, 24)) -> None:
        """Open SSH connection and start interactive vtysh session."""
        # known_hosts=None: pods are ephemeral K8s containers. Host keys are
        # generated on each tmpfs at boot and change on every pod restart.
        # Pinning them would cause connection failures after any restart.
        # The SSH connection is pod-IP to pod-IP within the K8s management
        # network — no MITM vector exists within the cluster. When physical
        # nodes are added, they'll have stable host keys and this should be
        # revisited with proper known_hosts management.
        self._conn = await asyncssh.connect(
            self._pod_ip,
            port=22,
            username="operator",
            client_keys=[self._ssh_key_path],
            known_hosts=None,
        )
        self._process = await self._conn.create_process(
            term_type="xterm-256color",
            term_size=term_size,
        )
        log.info("Terminal session opened to %s", self._pod_ip)

    async def send(self, data: str) -> None:
        """Send input to the SSH session (keyboard data from browser)."""
        if self._process and self._process.stdin:
            self._process.stdin.write(data)

    async def resize(self, cols: int, rows: int) -> None:
        """Resize the terminal (window resize from browser)."""
        if self._process:
            self._process.change_terminal_size(cols, rows)

    async def read_output(self) -> str | None:
        """Read output from the SSH session. Returns None on EOF/timeout."""
        if not self._process or not self._process.stdout:
            return None
        try:
            data = await asyncio.wait_for(
                self._process.stdout.read(4096),
                timeout=0.1,
            )
            return data if data else None
        except TimeoutError:
            return None
        except asyncssh.misc.BreakReceived:
            return None

    async def run_command(self, command: str, timeout: float = 10.0) -> str:
        """Run a single vtysh command and return output (non-interactive).

        Used by config export endpoint. Opens a fresh channel, runs the
        command, returns stdout.
        """
        if not self._conn:
            raise RuntimeError("Not connected")
        result = await asyncio.wait_for(
            self._conn.run(command),
            timeout=timeout,
        )
        return result.stdout or ""

    async def close(self) -> None:
        """Clean up SSH connection."""
        if self._process:
            try:
                self._process.close()
                await self._process.wait_closed()
            except Exception:
                pass
        if self._conn:
            try:
                self._conn.close()
                await self._conn.wait_closed()
            except Exception:
                pass
        log.info("Terminal session closed to %s", self._pod_ip)
