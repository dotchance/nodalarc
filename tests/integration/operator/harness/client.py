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
from websockets.exceptions import ConnectionClosed
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
    """A node's own command line, reached the way the page reaches it.

    A router answers with its routing CLI. Any other workload answers with its own shell.
    """

    def __init__(self, socket: ClientConnection, node_id: str) -> None:
        self._socket = socket
        self._node_id = node_id
        # "node# " from a routing CLI, "user@node:/path$ " from a shell.
        self._prompt = re.compile(rf"{re.escape(node_id)}(?::\S*)?[#>$] ?$")
        self._prompt_text = re.compile(rf"(?:\S+@)?{re.escape(node_id)}(?::\S*)?[#>$] ?$")
        self.banner = self._read_to_prompt(15.0)
        self.is_routing_cli = re.search(rf"{re.escape(node_id)}[#>] ?$", self.banner) is not None
        if self.is_routing_cli:
            self.run("terminal length 0")

    def run(
        self, command: str, *, interrupt_after: float | None = None, timeout: float = 20.0
    ) -> str:
        """Type one command and return what the node printed.

        `interrupt_after` sends Ctrl-C after that many seconds, for a command that runs until
        it is stopped (ping).
        """
        self.start(command)
        printed = ""
        if interrupt_after is not None:
            printed = self._read_for(interrupt_after)
            self._send("\x03")
        return self._printed(printed + self._read_to_prompt(timeout))

    def start(self, command: str) -> None:
        """Type one command and leave it running. `wait_for` and `finish` read what it prints."""
        self._send(f"{command}\n")
        self._started = ""

    def wait_for(self, text: str, timeout: float) -> None:
        """Read until the running command has printed `text` on a line of its own output."""
        deadline = time.monotonic() + timeout
        while text not in self._started.replace("\r", "").split("\n", 1)[-1]:
            remaining = deadline - time.monotonic()
            assert remaining > 0, f"no {text!r} within {timeout} s; received: {self._started!r}"
            self._started += self._receive(min(remaining, 0.5))

    def finish(self, timeout: float) -> str:
        """Wait for the started command to end and return everything it printed."""
        deadline = time.monotonic() + timeout
        while not self._prompt.search(self._started):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError(
                    f"no prompt from {self._node_id} within {timeout} s; "
                    f"received: {self._started!r}"
                )
            self._started += self._receive(min(remaining, 0.5))
        return self._printed(self._started)

    def leave(self) -> None:
        """End a shell the way a user does: stop what runs in it, then say exit.

        A routing CLI ends with its connection.
        """
        if self.is_routing_cli:
            return
        try:
            for _ in range(3):
                self._send("\x03")
                self._send("exit\n")
                self._read_for(1.5)  # the terminal closes when the shell has ended
        except ConnectionClosed:
            pass

    def _printed(self, received: str) -> str:
        """What the command printed: without the echoed command and the next prompt.

        Output that does not end in a newline shares its last line with the prompt.
        """
        lines = received.replace("\r", "").split("\n")
        before_prompt = self._prompt_text.sub("", lines[-1])
        return "\n".join([*lines[1:-1], *([before_prompt] if before_prompt else [])])

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
                raise TimeoutError(
                    f"no prompt from {self._node_id} within {timeout} s; received: {printed!r}"
                )
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
        return self.wait_for_session(session, self.switch(session), timeout=timeout)

    def wait_for_session(
        self, session: dict[str, Any], operation_id: str, *, timeout: float = 900.0
    ) -> dict[str, Any]:
        """Follow an accepted switch to its end, then wait until the session is ready."""
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

    def session_yaml(self, session: dict[str, Any]) -> str:
        """Download one catalog session (an entry of the session list) as the YAML it is stored as."""
        response = self._http.get(
            "/api/v1/sessions/yaml", params={"session_ref": session["source_id"]["session_ref"]}
        )
        if response.status_code >= 400:
            raise Refused(response.status_code, response.text)
        return response.text

    # --- Authoring ------------------------------------------------------------------------

    def save_session(self, compiled: dict[str, Any]) -> dict[str, Any]:
        """Save a compiled draft to the user catalog, replacing an earlier save of the same name."""
        request = {"draft": compiled["draft"], "target_ref": compiled["target_ref"]}
        try:
            existing = self.post("/api/v1/builder/catalog/get", {"ref": compiled["target_ref"]})
        except Refused as refusal:
            if refusal.status != 404:
                raise
        else:
            request["expected_session_revision"] = existing["revision"]
        return self.post("/api/v1/builder/session/save", request)

    def run_saved_session(self, saved: dict[str, Any], *, timeout: float = 900.0) -> dict[str, Any]:
        """Deploy a saved user session and wait until NodalArc says it is ready."""
        accepted = self.post(
            "/api/v1/builder/session/deploy",
            {
                "session_ref": saved["session"]["ref"],
                "expected_session_revision": saved["session"]["revision"],
                "expected_document_digest": saved["digests"]["document"],
                "expected_dependency_digest": saved["digests"]["dependency"],
                "record_history": False,
            },
        )
        name = saved["session"]["canonical_json"]["session"]["name"]
        return self.wait_for_session({"name": name}, accepted["operation_id"], timeout=timeout)

    def catalog_object(self, ref: str) -> dict[str, Any]:
        """One catalog object as the Builder library shows it."""
        return self.post("/api/v1/builder/catalog/get", {"ref": ref})["canonical_json"]

    def fork_catalog_object(
        self, source_ref: str, target_ref: str, changes: dict[str, Any]
    ) -> None:
        """Fork one catalog object into the user catalog with some fields replaced.

        `changes` maps a JSON pointer in the object to its new value. These are the calls the
        Builder library makes: open a draft on the fork target, edit it, save it.
        """
        self._save_catalog_draft({"source_ref": source_ref, "target_ref": target_ref}, changes)

    def edit_catalog_object(self, ref: str, changes: dict[str, Any]) -> None:
        """Replace some fields of one object of the user catalog, as the Builder library does."""
        self._save_catalog_draft({"source_ref": ref}, changes)

    def _save_catalog_draft(self, opening: dict[str, str], changes: dict[str, Any]) -> None:
        draft = self.post("/api/v1/builder/catalog/draft/open", opening)
        draft = self.post(
            "/api/v1/builder/catalog/draft/patch",
            {
                "draft": draft,
                "expected_draft_revision": draft["draft_revision"],
                "commands": [
                    {"operation": "replace", "pointer": pointer, "value": value}
                    for pointer, value in changes.items()
                ],
            },
        )
        assert not draft["issues"], f"the draft of {opening} has issues: {draft['issues']}"
        self.post(
            "/api/v1/builder/catalog/draft/save",
            {"draft": draft, "expected_draft_revision": draft["draft_revision"]},
        )

    def delete_user_object(self, ref: str) -> None:
        """Delete one object of the user catalog. A ref that is already gone is left alone."""
        assert ref.startswith("user:"), f"only user catalog objects are deleted, not {ref}"
        try:
            document = self.post("/api/v1/builder/catalog/get", {"ref": ref})
        except Refused as refusal:
            if refusal.status == 404:
                return
            raise
        impact = self.post("/api/v1/builder/catalog/dependents", {"ref": ref})
        self.post(
            "/api/v1/builder/catalog/delete",
            {
                "ref": ref,
                "expected_revision": document["revision"],
                "impact_acknowledgement": impact["acknowledgement"],
            },
        )

    # --- Time -----------------------------------------------------------------------------

    def playback(self, action: str, **fields: Any) -> dict[str, Any]:
        return self.post("/api/v1/playback", {"action": action, **fields})

    # --- Systems --------------------------------------------------------------------------

    def trace(self, src_node: str, dst_node: str) -> dict[str, Any]:
        """Trace Path once between two nodes, both directions."""
        return self.post("/api/v1/trace", {"src_node": src_node, "dst_node": dst_node})

    def introspect(self, node_id: str, command: str) -> str:
        """Run one of the commands the page offers on a node; returns what the router printed."""
        return self.post("/api/v1/introspect", {"node_id": node_id, "command": command})["output"]

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
            terminal = Terminal(socket, node_id)
            try:
                yield terminal
            finally:
                terminal.leave()
