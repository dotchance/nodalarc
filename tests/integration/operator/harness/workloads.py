"""What a session's workloads can do, asked from each workload's own shell.

A new kind of workload needs one finder here: it recognises the workload from the tools and
settings its shell has, the way a user who opens that shell does.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .client import Operator, Terminal
from .network import ShownNetwork


def run_in_shell(terminal: Terminal, command: str, *, timeout: float = 20.0) -> tuple[int, str]:
    """Run one shell command; returns its exit status and what it printed."""
    printed = terminal.run(f'{command}; echo "status $? end"', timeout=timeout)
    status = re.search(r"^status (\d+) end$", printed, re.M)
    assert status, f"the shell did not report an exit status: {printed!r}"
    return int(status.group(1)), printed[: status.start()]


@dataclass(frozen=True)
class QuicClient:
    node_id: str
    server_address: str


@dataclass(frozen=True)
class DtnEndpoint:
    node_id: str
    eid: str  # as the node's own bundle daemon states it, "dtn://name/"
    socket: str


def find_workloads(
    operator: Operator, shown: ShownNetwork
) -> tuple[list[QuicClient], list[DtnEndpoint]]:
    """Open the shell of every host node and see what it runs."""
    quic_clients: list[QuicClient] = []
    dtn_endpoints: list[DtnEndpoint] = []
    for node_id, node in sorted(shown.nodes.items()):
        if node["role"] != "host":
            continue
        with operator.terminal(node_id) as terminal:
            if terminal.is_routing_cli:
                continue
            _, quic = run_in_shell(
                terminal, 'command -v picoquicdemo >/dev/null && echo "server=$QUIC_SERVER"'
            )
            if server := re.search(r"^server=(\S+)$", quic, re.M):
                quic_clients.append(QuicClient(node_id, server.group(1)))
            # A DTN endpoint is told where its relay is; the relay itself is not.
            _, dtn = run_in_shell(
                terminal,
                'command -v aap2-send >/dev/null && [ -n "$DTN_RELAY" ] '
                "&& find /run -name aap2.socket",
            )
            if socket := re.search(r"^(/\S+aap2\.socket)$", dtn, re.M):
                _, welcome = run_in_shell(
                    terminal,
                    f"aap2-receive --socket {socket.group(1)} --agentid whoami -vv --count 1 "
                    "& sleep 2; kill $!; wait",
                )
                eid = re.search(r"EID = (dtn://\S+)", welcome)
                assert eid, f"{node_id}: the bundle daemon did not state its EID: {welcome!r}"
                dtn_endpoints.append(DtnEndpoint(node_id, eid.group(1), socket.group(1)))
    return quic_clients, dtn_endpoints


def expect_a_bundle(inbox: Terminal, receiver: DtnEndpoint, agent: str) -> None:
    """In the receiver's shell, register `agent` with its bundle daemon and wait for one bundle."""
    inbox.start(f"aap2-receive --socket {receiver.socket} --agentid {agent} --count 1 --newline -v")
    inbox.wait_for("Waiting for bundles", 20.0)


def send_a_bundle(
    operator: Operator, sender: DtnEndpoint, receiver: DtnEndpoint, agent: str, payload: str
) -> tuple[int, str]:
    """From the sender's shell, send one bundle to `agent` at the receiver."""
    with operator.terminal(sender.node_id) as outbox:
        return run_in_shell(
            outbox, f"aap2-send --socket {sender.socket} {receiver.eid}{agent} {payload}"
        )
