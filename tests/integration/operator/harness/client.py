"""What an operator can do to a NodalArc cluster: the REST API, the state feed and the terminal.

Nothing here reaches around VS-API. A test built on this client can only do what a user at the
page can do.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import httpx
from websockets.sync.client import ClientConnection, connect

PROJECT_ROOT = Path(__file__).resolve().parents[4]

_ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")


def vs_api_base_url() -> str:
    """The address the page would use: VS_API_HOST, or the one the lifecycle scripts find."""
    host = os.environ.get("VS_API_HOST", "")
    if host:
        return f"http://{host}" if ":" in host else f"http://{host}:8080"
    found = subprocess.run(
        [
            "bash",
            "-c",
            '. scripts/na-lib.sh && discover_vs_api "$1" 30 >&2 && printf "%s" "$api_base"',
            "_",
            os.environ.get("NAMESPACE", "nodalarc"),
        ],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        env={**os.environ, "LIB_PREFIX": "operator-tests"},
    )
    if found.returncode != 0 or not found.stdout:
        raise RuntimeError(f"VS-API was not found on the cluster: {found.stderr.strip()}")
    return found.stdout


class Refused(Exception):
    """VS-API refused a request. Carries the status and the body it answered with."""

    def __init__(self, status: int, body: Any) -> None:
        super().__init__(f"{status}: {body}")
        self.status = status
        self.body = body


class Terminal:
    """A router's CLI, reached the way the page reaches it."""

    def __init__(self, socket: ClientConnection, node_id: str) -> None:
        self._socket = socket
        self._prompt = re.compile(rf"{re.escape(node_id)}[#>] ?$")
        self.banner = self._read_to_prompt(15.0)
        self.run("terminal length 0")

    def run(
        self, command: str, *, interrupt_after: float | None = None, timeout: float = 20.0
    ) -> str:
        """Type one command and return what the router printed.

        `interrupt_after` sends Ctrl-C after that many seconds, for a command that runs until
        it is stopped (ping).
        """
        self._send(f"{command}\n")
        printed = ""
        if interrupt_after is not None:
            printed = self._read_for(interrupt_after)
            self._send("\x03")
        printed += self._read_to_prompt(timeout)
        lines = printed.replace("\r", "").split("\n")
        return "\n".join(lines[1:-1])

    def _send(self, data: str) -> None:
        self._socket.send(json.dumps({"type": "input", "data": data}))

    def _receive(self, wait: float) -> str:
        try:
            message = json.loads(self._socket.recv(timeout=wait))
        except TimeoutError:
            return ""
        return _ANSI.sub("", message.get("data", ""))

    def _read_for(self, seconds: float) -> str:
        printed = ""
        deadline = time.monotonic() + seconds
        while (remaining := deadline - time.monotonic()) > 0:
            printed += self._receive(min(remaining, 0.5))
        return printed

    def _read_to_prompt(self, timeout: float) -> str:
        printed = ""
        deadline = time.monotonic() + timeout
        while not self._prompt.search(printed):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError(f"no router prompt within {timeout} s; received: {printed!r}")
            printed += self._receive(min(remaining, 0.5))
        return printed


class Operator:
    """One user of one NodalArc cluster."""

    def __init__(self, base_url: str) -> None:
        self.base_url = base_url.rstrip("/")
        self._http = httpx.Client(base_url=self.base_url, timeout=60.0)
        self.token: str = self._http.get("/api/v1/auth/token").raise_for_status().json()["token"]
        self._http.headers["Authorization"] = f"Bearer {self.token}"

    # --- REST -----------------------------------------------------------------------------

    def get(self, path: str, **params: Any) -> Any:
        return self._answer(self._http.get(path, params=params))

    def post(self, path: str, body: dict[str, Any]) -> Any:
        return self._answer(self._http.post(path, json=body))

    @staticmethod
    def _answer(response: httpx.Response) -> Any:
        if response.status_code >= 400:
            try:
                body = response.json()
            except ValueError:
                body = response.text
            raise Refused(response.status_code, body)
        return response.json()

    def state(self) -> dict[str, Any]:
        return self.get("/api/v1/state")

    # --- Sessions -------------------------------------------------------------------------

    def sessions(self) -> list[dict[str, Any]]:
        return self.get("/api/v1/sessions")

    def switch(
        self, session: dict[str, Any], *, record_history: bool = False, **changed: str
    ) -> str:
        """Ask for `session` (an entry of the session list) to run. Returns the operation id.

        `changed` replaces a reviewed digest, to ask for something the list did not offer.
        """
        body = {
            "source": session["source_id"],
            "expected_source_revision": session["source_revision"],
            "expected_document_digest": session["document_digest"],
            "expected_dependency_digest": session["dependency_digest"],
            "record_history": record_history,
            **changed,
        }
        return self.post("/api/v1/sessions/switch", body)["operation_id"]

    def run_session(self, session: dict[str, Any], *, timeout: float = 900.0) -> dict[str, Any]:
        """Switch to `session` and wait until NodalArc says it is ready. Returns the state."""
        operation_id = self.switch(session)
        deadline = time.monotonic() + timeout
        while True:
            transition = self.get(f"/api/v1/session-transitions/{operation_id}")
            if transition["state"] in ("succeeded", "failed"):
                break
            assert time.monotonic() < deadline, (
                f"the switch did not finish in {timeout} s: {transition}"
            )
            time.sleep(2.0)
        assert transition["state"] == "succeeded", f"the switch failed: {transition.get('failure')}"
        while True:
            state = self.state()
            if (
                state["constellation_name"] == session["name"]
                and state["session_status"] == "ready"
            ):
                return state
            assert time.monotonic() < deadline, (
                f"the switch succeeded and the session is not ready after {timeout} s: "
                f"{state['constellation_name']!r} {state['session_status']!r} "
                f"{state['session_status_detail']!r}"
            )
            time.sleep(2.0)

    # --- Time -----------------------------------------------------------------------------

    def playback(self, action: str, **fields: Any) -> dict[str, Any]:
        return self.post("/api/v1/playback", {"action": action, **fields})

    # --- Live feeds -----------------------------------------------------------------------

    def socket_url(self, path: str, token: str | None = None) -> str:
        chosen = self.token if token is None else token
        return f"{self.base_url.replace('http://', 'ws://')}{path}?token={chosen}"

    def watch_state(
        self,
        seconds: float,
        until: Callable[[list[tuple[float, dict[str, Any]]]], bool] | None = None,
    ) -> list[tuple[float, dict[str, Any]]]:
        """Listen to the state feed for `seconds`, or until `until` says enough was heard.

        Returns each message with the wall time it arrived.
        """
        messages: list[tuple[float, dict[str, Any]]] = []
        with connect(self.socket_url("/ws/v1/state"), max_size=None) as socket:
            deadline = time.monotonic() + seconds
            while (remaining := deadline - time.monotonic()) > 0:
                try:
                    raw = socket.recv(timeout=remaining)
                except TimeoutError:
                    break
                messages.append((time.monotonic(), json.loads(raw)))
                if until is not None and until(messages):
                    break
        return messages

    @contextmanager
    def terminal(self, node_id: str) -> Iterator[Terminal]:
        with connect(self.socket_url(f"/ws/v1/terminal/{node_id}")) as socket:
            yield Terminal(socket, node_id)
