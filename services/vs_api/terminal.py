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
import codecs
import contextlib
import logging
import select
import threading

import asyncssh
import kubernetes.client
from kubernetes.stream.ws_client import (
    ABNF,
    RESIZE_CHANNEL,
    STDERR_CHANNEL,
    STDIN_CHANNEL,
    STDOUT_CHANNEL,
)
from nodalarc.workload_target import NODE_ID_LABEL, TERMINAL_ACCESS_ANNOTATION
from starlette.websockets import WebSocket
from websocket import WebSocketException

from vs_api import k8s

log = logging.getLogger(__name__)

# Cached SSH private key object (loaded lazily from K8s Secret, kept in memory only).
# The cache key includes the Secret resourceVersion. The Operator can recreate
# terminal keys when the owning ConstellationSpec is replaced, while VS-API stays
# up; a process-lifetime key cache would then keep authenticating with stale
# credentials.
_ssh_key: asyncssh.SSHKey | None = None
_ssh_key_cache_key: tuple[str, str] | None = None


def _load_ssh_key(namespace: str) -> asyncssh.SSHKey:
    """Load the SSH private key from the K8s Secret into memory.

    Returns an asyncssh.SSHKey object. The imported key is cached while the
    Secret revision is unchanged. The key NEVER touches disk — it stays in
    memory only. Uses the cached K8s client to avoid blocking on
    load_incluster_config().
    """
    global _ssh_key, _ssh_key_cache_key

    v1 = k8s.core_v1()

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

    private_key_b64 = (secret.data or {}).get("id_ed25519")
    if not private_key_b64:
        raise RuntimeError("Secret nodalarc-terminal-keys missing id_ed25519 key")

    metadata = getattr(secret, "metadata", None)
    resource_version = str(getattr(metadata, "resource_version", "") or "")
    cache_key = (namespace, resource_version or private_key_b64)
    if _ssh_key is not None and _ssh_key_cache_key == cache_key:
        return _ssh_key

    private_key_pem = base64.b64decode(private_key_b64).decode()
    _ssh_key = asyncssh.import_private_key(private_key_pem)
    _ssh_key_cache_key = cache_key
    log.info(
        "SSH private key loaded from Secret %s resourceVersion=%s "
        "(in-memory only, never written to disk)",
        "nodalarc-terminal-keys",
        resource_version or "unknown",
    )
    return _ssh_key


import re

# Node ID must match the pattern: sat-P00S00 or gs-name (alphanumeric + hyphens)
_NODE_ID_PATTERN = re.compile(r"^[a-zA-Z][a-zA-Z0-9\-]{0,62}$")


def parse_terminal_contract(annotation: str | None) -> dict | None:
    """Parse the pod's terminal contract annotation, refusing malformed data.

    Returns the contract dict ({"surface": "ssh"} or
    {"surface": "exec", "container": ..., "command": [...]}) or None when
    the workload declares no terminal access.
    """
    if not annotation:
        return None
    import json as _json

    try:
        contract = _json.loads(annotation)
    except ValueError:
        log.warning("Malformed terminal contract annotation: %r", annotation[:120])
        return None
    if not isinstance(contract, dict):
        return None
    surface = contract.get("surface")
    if surface == "ssh":
        return {"surface": "ssh"}
    if surface == "exec":
        container = contract.get("container")
        command = contract.get("command")
        if isinstance(container, str) and isinstance(command, list) and command:
            return {"surface": "exec", "container": container, "command": command}
    log.warning("Unknown terminal contract: %r", annotation[:120])
    return None


def _resolve_pod_terminal_sync(node_id: str, namespace: str) -> tuple[str, str, dict | None] | None:
    """Synchronous pod name + IP + terminal-contract resolution.

    Returns (pod_name, pod_ip, contract). A pod without a valid terminal
    contract declares no terminal surface: callers refuse immediately
    instead of dialing a pod that cannot answer.
    """
    if not _NODE_ID_PATTERN.match(node_id):
        log.warning("Invalid node_id rejected: %r", node_id)
        return None
    try:
        v1 = k8s.core_v1()
        pods = v1.list_namespaced_pod(
            namespace,
            label_selector=f"{NODE_ID_LABEL}={node_id}",
        )
        if pods.items and pods.items[0].status.pod_ip:
            pod = pods.items[0]
            annotations = pod.metadata.annotations or {}
            contract = parse_terminal_contract(annotations.get(TERMINAL_ACCESS_ANNOTATION))
            return pod.metadata.name, pod.status.pod_ip, contract
    except Exception:
        log.exception("Failed to resolve pod IP for %s", node_id)
    return None


async def resolve_pod_terminal(node_id: str, namespace: str) -> tuple[str, str, dict | None] | None:
    """Resolve a constellation node_id to (pod name, pod IP, contract).

    Runs the synchronous K8s API call in a thread executor so it doesn't
    block the async event loop (which would stall active SSH sessions).
    Validates node_id against a strict pattern to prevent label selector
    injection.
    """
    return await asyncio.get_running_loop().run_in_executor(
        None, _resolve_pod_terminal_sync, node_id, namespace
    )


class ExecTerminalSession:
    """A terminal attached inside a pod container via the Kubernetes exec
    API — the landing surface for workloads that run no SSH daemon.

    Mirrors TerminalSession's lifecycle (connect/send/resize/receive/close)
    so the WebSocket endpoint treats both surfaces identically. The
    underlying kubernetes-client stream is synchronous; every operation
    runs in a thread executor.

    Output is read one whole WebSocket frame at a time from the stream's
    connection. The Kubernetes client's own ``update()`` polls the socket
    first, and a frame that arrived in the same TLS record as the one before
    it is already inside the TLS layer, where a poll does not see it: output
    read that way waited for the next byte from the pod. The read here asks
    the TLS layer first.

    Reads and writes run in different executor threads. One lock serializes
    every call that enters the TLS layer; the wait for new data is a poll of
    the socket and holds no lock.
    """

    # How long one read waits for data before it reports no output.
    _READ_WAIT_S = 1.0

    def __init__(self, namespace: str, pod_name: str, container: str, command: list[str]):
        self._namespace = namespace
        self._pod_name = pod_name
        self._container = container
        self._command = command
        self._stream = None
        self._ended = False
        self._tls_lock = threading.Lock()
        # A character may be split across two frames.
        self._decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")

    async def connect(self) -> None:
        from kubernetes.stream import stream as k8s_stream

        def _connect():
            v1 = k8s.core_v1()
            return k8s_stream(
                v1.connect_get_namespaced_pod_exec,
                self._pod_name,
                self._namespace,
                container=self._container,
                command=self._command,
                stdin=True,
                stdout=True,
                stderr=True,
                tty=True,
                _preload_content=False,
            )

        self._stream = await asyncio.get_running_loop().run_in_executor(None, _connect)

    def _write(self, stream, channel: int, data: str) -> None:
        with self._tls_lock:
            stream.write_channel(channel, data)

    async def send(self, data: str) -> None:
        stream = self._stream
        if stream is None:
            raise RuntimeError("exec terminal is not connected")
        await asyncio.get_running_loop().run_in_executor(
            None, self._write, stream, STDIN_CHANNEL, data
        )

    async def resize(self, cols: int, rows: int) -> None:
        stream = self._stream
        if stream is None:
            return
        import json as _json

        payload = _json.dumps({"Width": int(cols), "Height": int(rows)})
        await asyncio.get_running_loop().run_in_executor(
            None, self._write, stream, RESIZE_CHANNEL, payload
        )

    @property
    def ended(self) -> bool:
        """True once the exec stream closed: the shell exited or the connection broke."""
        return self._ended

    async def read_output(self) -> str | None:
        """One frame of terminal output; None when none arrived or the stream ended."""
        stream = self._stream
        if stream is None or self._ended:
            return None
        return await asyncio.get_running_loop().run_in_executor(None, self._read_frame, stream)

    def _read_frame(self, stream) -> str | None:
        try:
            tls_socket = stream.sock.sock
            if tls_socket is None:
                raise OSError("the exec connection is closed")
            with self._tls_lock:
                held = tls_socket.pending()
            if not held:
                poller = select.poll()
                poller.register(tls_socket, select.POLLIN)
                if not poller.poll(self._READ_WAIT_S * 1000):
                    return None
            with self._tls_lock:
                op_code, frame = stream.sock.recv_data_frame(True)
        except (WebSocketException, OSError, ValueError) as exc:
            self._ended = True
            if self._stream is not None:  # close() was not called: the stream broke
                log.warning("Exec terminal stream to %s broke: %r", self._pod_name, exc)
            return None
        if op_code == ABNF.OPCODE_CLOSE:
            self._ended = True
            return None
        data = frame.data
        if op_code not in (ABNF.OPCODE_BINARY, ABNF.OPCODE_TEXT) or len(data) < 2:
            return None
        if data[0] not in (STDOUT_CHANNEL, STDERR_CHANNEL):
            return None
        return self._decoder.decode(data[1:]) or None

    def _close(self, stream) -> None:
        with self._tls_lock:
            stream.close()

    async def close(self) -> None:
        stream = self._stream
        self._stream = None
        if stream is not None:
            await asyncio.get_running_loop().run_in_executor(None, self._close, stream)


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

    @property
    def ended(self) -> bool:
        """True once the remote shell closed its output: the session is over."""
        return self._process is not None and self._process.stdout.at_eof()

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
            # loop-blocking-ok: asyncssh's async SSHClientConnection.run —
            # the name merely collides with a sync run() in another service.
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
