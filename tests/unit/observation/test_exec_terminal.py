"""A workload shell reached through the Kubernetes exec stream.

The stand-in is the stream's connection to the API server: a WebSocket over a TLS socket. It
hands over real WebSocket frames. As with TLS, a whole record is read from the socket at once,
so the frames of one record after the first are held inside the TLS layer and the socket shows
nothing more to read.
"""

from __future__ import annotations

import asyncio
import socket
from types import SimpleNamespace

import pytest
import vs_api.terminal as terminal
from kubernetes.stream.ws_client import (
    ABNF,
    ERROR_CHANNEL,
    RESIZE_CHANNEL,
    STDERR_CHANNEL,
    STDIN_CHANNEL,
    STDOUT_CHANNEL,
)
from vs_api.terminal import ExecTerminalSession
from websocket import WebSocketConnectionClosedException


def _frame(channel: int, payload: bytes) -> ABNF:
    return ABNF.create_frame(bytes([channel]) + payload, ABNF.OPCODE_BINARY)


class _TlsSocket:
    def __init__(self) -> None:
        self.near, self.far = socket.socketpair()
        self.held: list[ABNF | Exception] = []

    def fileno(self) -> int:
        return self.near.fileno()

    def pending(self) -> int:
        return len(self.held)


class _Connection:
    def __init__(self) -> None:
        self.sock = _TlsSocket()
        self._records: list[list[ABNF | Exception]] = []
        self.reads = 0

    def record_arrives(self, *frames: ABNF | Exception) -> None:
        """One TLS record reaches the socket carrying these frames."""
        self._records.append(list(frames))
        self.sock.far.send(b"r")

    def recv_data_frame(self, control_frame: bool) -> tuple[int, ABNF]:
        assert control_frame, "the close frame has to be returned to the reader"
        self.reads += 1
        if not self.sock.held:
            assert self._records, "a read with nothing to read would block the terminal"
            self.sock.near.recv(1)
            self.sock.held = self._records.pop(0)
        arrival = self.sock.held.pop(0)
        if isinstance(arrival, Exception):
            raise arrival
        return arrival.opcode, arrival


@pytest.fixture
def exec_stream(monkeypatch: pytest.MonkeyPatch):
    connection = _Connection()
    written: list[tuple[int, str]] = []
    stream = SimpleNamespace(
        sock=connection,
        write_channel=lambda channel, data: written.append((channel, data)),
        close=lambda: None,
    )
    monkeypatch.setattr(
        terminal.k8s, "core_v1", lambda: SimpleNamespace(connect_get_namespaced_pod_exec=None)
    )
    monkeypatch.setattr("kubernetes.stream.stream", lambda *_args, **_kwargs: stream)
    monkeypatch.setattr(ExecTerminalSession, "_READ_WAIT_S", 0.05)
    session = ExecTerminalSession("nodalarc", "host-pod", "workload", ["/bin/bash"])
    asyncio.run(session.connect())
    return session, connection, written


def _read(session: ExecTerminalSession) -> str | None:
    return asyncio.run(session.read_output())


def test_frames_of_one_tls_record_are_each_delivered(exec_stream) -> None:
    """The prompt arrives in the same TLS record as the output before it. The socket shows
    nothing more to read and the prompt is still delivered."""
    session, connection, _ = exec_stream
    connection.record_arrives(
        _frame(STDOUT_CHANNEL, b"status 0 end\r\n"), _frame(STDOUT_CHANNEL, b"root@host:/# ")
    )

    assert _read(session) == "status 0 end\r\n"
    assert _read(session) == "root@host:/# "
    assert _read(session) is None
    assert not session.ended


def test_a_read_with_nothing_to_read_returns_without_touching_the_connection(exec_stream) -> None:
    session, connection, _ = exec_stream

    assert _read(session) is None
    assert connection.reads == 0
    assert not session.ended


def test_a_character_split_across_two_frames_arrives_whole(exec_stream) -> None:
    session, connection, _ = exec_stream
    encoded = "é".encode()
    connection.record_arrives(_frame(STDOUT_CHANNEL, b"caf" + encoded[:1]))
    connection.record_arrives(_frame(STDOUT_CHANNEL, encoded[1:]))

    assert _read(session) == "caf"
    assert _read(session) == "é"


def test_error_output_is_shown_and_the_status_channel_is_not(exec_stream) -> None:
    session, connection, _ = exec_stream
    connection.record_arrives(
        _frame(STDERR_CHANNEL, b"bash: nope: command not found\r\n"),
        _frame(ERROR_CHANNEL, b'{"status":"Success"}'),
    )

    assert _read(session) == "bash: nope: command not found\r\n"
    assert _read(session) is None
    assert not session.ended


@pytest.mark.parametrize(
    "ending",
    [
        ABNF.create_frame(b"", ABNF.OPCODE_CLOSE),
        WebSocketConnectionClosedException("Connection to remote host was lost."),
        OSError(9, "Bad file descriptor"),
    ],
    ids=["the shell exited", "the connection closed", "the socket failed"],
)
def test_an_ended_stream_is_reported_and_not_read_again(exec_stream, ending) -> None:
    session, connection, _ = exec_stream
    connection.record_arrives(_frame(STDOUT_CHANNEL, b"exit\r\n"), ending)

    assert _read(session) == "exit\r\n"
    assert not session.ended
    assert _read(session) is None
    assert session.ended
    assert _read(session) is None
    assert connection.reads == 2


def test_typing_goes_to_the_shell_and_a_resize_to_the_terminal_size(exec_stream) -> None:
    session, _, written = exec_stream

    asyncio.run(session.send("ls\n"))
    asyncio.run(session.resize(120, 40))

    assert written == [(STDIN_CHANNEL, "ls\n"), (RESIZE_CHANNEL, '{"Width": 120, "Height": 40}')]
