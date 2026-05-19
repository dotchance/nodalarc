# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
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
import contextlib
import logging

import asyncssh
import kubernetes.client
from starlette.websockets import WebSocket

log = logging.getLogger(__name__)

# Cached SSH private key object (loaded lazily from K8s Secret, kept in memory only)
_ssh_key: asyncssh.SSHKey | None = None


def _load_ssh_key(namespace: str) -> asyncssh.SSHKey:
    """Load the SSH private key from the K8s Secret into memory.

    Returns an asyncssh.SSHKey object. Called once, cached for the VS-API
    process lifetime. The key NEVER touches disk — it stays in memory only.
    Uses the cached K8s client to avoid blocking on load_incluster_config().
    """
    global _ssh_key
    if _ssh_key is not None:
        return _ssh_key

    v1 = _get_k8s_client()

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
    _ssh_key = asyncssh.import_private_key(private_key_pem)
    log.info("SSH private key loaded from Secret (in-memory only, never written to disk)")
    return _ssh_key


import re

# Node ID must match the pattern: sat-P00S00 or gs-name (alphanumeric + hyphens)
_NODE_ID_PATTERN = re.compile(r"^[a-zA-Z][a-zA-Z0-9\-]{0,62}$")

# Cached K8s API client (initialized once, reused for all pod lookups)
_k8s_v1: kubernetes.client.CoreV1Api | None = None


def _get_k8s_client() -> kubernetes.client.CoreV1Api:
    """Get or create the cached K8s API client."""
    global _k8s_v1
    if _k8s_v1 is None:
        kubernetes.config.load_incluster_config()
        _k8s_v1 = kubernetes.client.CoreV1Api()
    return _k8s_v1


def _resolve_pod_ip_sync(node_id: str, namespace: str) -> str | None:
    """Synchronous pod IP resolution (runs in thread executor)."""
    if not _NODE_ID_PATTERN.match(node_id):
        log.warning("Invalid node_id rejected: %r", node_id)
        return None
    try:
        v1 = _get_k8s_client()
        pods = v1.list_namespaced_pod(
            namespace,
            label_selector=f"nodalarc.io/node-id={node_id}",
        )
        if pods.items and pods.items[0].status.pod_ip:
            return pods.items[0].status.pod_ip
    except Exception:
        log.exception("Failed to resolve pod IP for %s", node_id)
    return None


async def resolve_pod_ip(node_id: str, namespace: str) -> str | None:
    """Resolve a constellation node_id to its K8s pod IP.

    Runs the synchronous K8s API call in a thread executor so it doesn't
    block the async event loop (which would stall active SSH sessions).
    Validates node_id against a strict pattern to prevent label selector
    injection.
    """
    return await asyncio.get_running_loop().run_in_executor(
        None, _resolve_pod_ip_sync, node_id, namespace
    )


class TerminalSession:
    """Manages a single SSH session to a constellation node.

    Lifecycle: connect() → send()/receive()/resize() → close().
    Used by the WebSocket endpoint for browser access, and by the
    config export endpoint for non-interactive command execution.
    """

    def __init__(self, pod_ip: str, ssh_key: asyncssh.SSHKey):
        self._pod_ip = pod_ip
        self._ssh_key = ssh_key
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
            client_keys=[self._ssh_key],
            known_hosts=None,
            # Disable all DNS lookups — pod IPs have no DNS records.
            # Without this, asyncssh attempts host canonicalization and
            # reverse DNS which times out against CoreDNS.
            canonical=False,
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
        if result.stdout is None:
            log.error("SSH exec returned None stdout for command: %s", command)
            raise ValueError(f"SSH exec returned None stdout for: {command}")
        return result.stdout

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


class TerminalManager:
    """Tracks active terminal sessions for lifecycle management.

    Keyed by unique connection_id (not node_id) — multiple users or
    tabs can open terminals to the same node without collision.
    """

    def __init__(self) -> None:
        self._sessions: dict[str, tuple[str, TerminalSession, WebSocket]] = {}
        self._lock = asyncio.Lock()
        self._next_id = 0

    def _gen_id(self) -> str:
        self._next_id += 1
        return f"term-{self._next_id}"

    async def register(self, node_id: str, session: TerminalSession, websocket: WebSocket) -> str:
        """Register a session. Returns unique connection_id for unregister."""
        async with self._lock:
            conn_id = self._gen_id()
            self._sessions[conn_id] = (node_id, session, websocket)
            return conn_id

    async def unregister(self, conn_id: str) -> None:
        async with self._lock:
            self._sessions.pop(conn_id, None)

    async def close_all(self, reason: str = "Session switched") -> None:
        """Close all active terminal sessions and their WebSockets."""
        async with self._lock:
            if not self._sessions:
                return
            log.info(
                "Closing %d terminal sessions: %s",
                len(self._sessions),
                reason,
            )
            for conn_id, (node_id, session, ws) in list(self._sessions.items()):
                try:
                    await session.close()
                except Exception as exc:
                    log.warning("Failed to close terminal %s (%s): %s", conn_id, node_id, exc)
                if ws is not None:
                    with contextlib.suppress(Exception):
                        await ws.close(code=4410, reason=reason)
            self._sessions.clear()
            log.info("All terminal sessions closed")
