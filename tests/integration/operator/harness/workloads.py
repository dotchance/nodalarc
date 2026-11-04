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


@dataclass(frozen=True)
class HdtnEndpoint:
    node_id: str
    member: str  # the network member the node's HDTN launcher found itself to be
    receiver_eid: str  # files sent here land in the node's /run/hdtn/received
    sender_eid: str  # the number a sending application on this node uses


def find_hdtn_endpoints(operator: Operator, shown: ShownNetwork) -> list[HdtnEndpoint]:
    """Open the shell of every host node and keep those running an HDTN endpoint.

    The node's HDTN launcher writes which member it is into its configuration and the numbering
    into /run/hdtn/members, the same table it prints when it starts.
    """
    endpoints: list[HdtnEndpoint] = []
    for node_id, node in sorted(shown.nodes.items()):
        if node["role"] != "host":
            continue
        with operator.terminal(node_id) as terminal:
            if terminal.is_routing_cli:
                continue
            _, found = run_in_shell(
                terminal,
                "command -v hdtn-one-process >/dev/null && test -d /run/hdtn/received "
                '&& sed -n \'s/.*"hdtnConfigName": "\\([a-z0-9]*\\) (ipn:\\([0-9]*\\))".*/'
                "self=\\1 \\2/p' /run/hdtn/hdtn.json && cat /run/hdtn/members",
            )
            me = re.search(r"^self=(\S+) (\d+)$", found, re.M)
            if me is None:
                continue
            row = re.search(rf"^{me.group(1)} ipn:{me.group(2)} (ipn:\d+\.1)$", found, re.M)
            assert row, f"{node_id}: /run/hdtn/members has no receiver row for {me.group(1)}"
            endpoints.append(
                HdtnEndpoint(node_id, me.group(1), row.group(1), f"ipn:{2000 + int(me.group(2))}.1")
            )
    return endpoints


def send_a_file(
    operator: Operator, sender: HdtnEndpoint, receiver: HdtnEndpoint, name: str, payload: str
) -> tuple[float | None, int, str]:
    """From the sender's shell, send one file to the receiver.

    Returns the sender's clock when it sent, the bundle bytes its node accepted, and what the
    shell printed. bpsendfile keeps running after it sends, so it is stopped after 15 s.
    """
    with operator.terminal(sender.node_id) as outbox:
        _, printed = run_in_shell(
            outbox,
            f"mkdir -p /run/hdtn/outbox && printf '%s' '{payload}' > /run/hdtn/outbox/{name} "
            f"&& echo sent-at $(date +%s.%N) && timeout 15 bpsendfile --use-bp-version-7 "
            f"--bundle-lifetime-milliseconds=86400000 --my-uri-eid={sender.sender_eid} "
            f"--dest-uri-eid={receiver.receiver_eid} "
            f"--outducts-config-file=/run/hdtn/app-outduct.json "
            f"--file-or-folder-path=/run/hdtn/outbox/{name} 2>&1 | grep totalBundleBytesAcked",
            timeout=40.0,
        )
    sent = re.search(r"^sent-at (\d+\.\d+)$", printed, re.M)
    acked = re.search(r"totalBundleBytesAcked (\d+)", printed)
    return (
        float(sent.group(1)) if sent else None,
        int(acked.group(1)) if acked else 0,
        printed,
    )


def received_files(operator: Operator, receiver: HdtnEndpoint) -> dict[str, tuple[float, str]]:
    """The files in the receiver's /run/hdtn/received: name -> (its clock when written, content)."""
    with operator.terminal(receiver.node_id) as inbox:
        _, printed = run_in_shell(
            inbox,
            'for f in /run/hdtn/received/*; do [ -f "$f" ] && '
            'echo "file $(basename "$f") $(date -r "$f" +%s.%N) $(cat "$f")"; done; true',
        )
    files: dict[str, tuple[float, str]] = {}
    for line in printed.splitlines():
        if found := re.match(r"^file (\S+) (\d+\.\d+) (.*)$", line):
            files[found.group(1)] = (float(found.group(2)), found.group(3))
    return files
