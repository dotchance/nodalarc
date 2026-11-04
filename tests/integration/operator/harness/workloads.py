"""What a session's workloads can do, asked from each workload's own shell.

A new kind of workload needs one finder here: it recognises the workload from the tools and
settings its shell has, the way a user who opens that shell does.
"""

from __future__ import annotations

import json
import re
import uuid
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
class BundleFileEndpoint:
    """A node running a DTN stack's file receiver, packaged by one of the stack images."""

    stack: str  # "hdtn" or "ion": the image whose launcher runs the node
    node_id: str
    member: str  # the network member the node's launcher found itself to be
    receiver_eid: str  # files sent here land in the node's received directory
    sender_eid: str  # the endpoint a sending application on this node uses

    @property
    def directory(self) -> str:
        return f"/run/{self.stack}"


def find_bundle_file_endpoints(operator: Operator, shown: ShownNetwork) -> list[BundleFileEndpoint]:
    """Open the shell of every host node and keep those running an HDTN or ION endpoint.

    Each stack's launcher writes the network numbering into members in its directory, the same
    table it prints when it starts, and the node's own number into the configuration it wrote:
    HDTN's hdtn.json names it, ION's node.ionrc starts with ION's initialize command.
    """
    endpoints: list[BundleFileEndpoint] = []
    for node_id, node in sorted(shown.nodes.items()):
        if node["role"] != "host":
            continue
        with operator.terminal(node_id) as terminal:
            if terminal.is_routing_cli:
                continue
            _, found = run_in_shell(
                terminal,
                "if command -v hdtn-one-process >/dev/null; then "
                '  sed -n \'s/.*"hdtnConfigName": "[a-z0-9]* (ipn:\\([0-9]*\\))".*/'
                "self=hdtn \\1/p' /run/hdtn/hdtn.json && cat /run/hdtn/members; "
                "  test -d /run/hdtn/received && echo has-received; "
                "elif command -v ionadmin >/dev/null; then "
                "  sed -n 's/^1 \\([0-9]*\\) .*/self=ion \\1/p' /run/ion/node.ionrc "
                "&& cat /run/ion/members; test -d /run/ion/received && echo has-received; fi",
            )
            me = re.search(r"^self=(hdtn|ion) (\d+)$", found, re.M)
            if me is None:
                continue
            stack, number = me.group(1), int(me.group(2))
            row = re.search(rf"^(\S+) ipn:{number} (ipn:\d+\.1)$", found, re.M)
            if row is None:
                continue  # a relay: the members table names no receiver for it
            assert re.search(r"^has-received$", found, re.M), (
                f"{node_id}: the members table names a receiver for {row.group(1)}, but the "
                f"node has no /run/{stack}/received"
            )
            # HDTN applications are nodes of their own, numbered 2000 + the node; ION
            # applications attach to the node and send from its service 2.
            sender = f"ipn:{2000 + number}.1" if stack == "hdtn" else f"ipn:{number}.2"
            endpoints.append(BundleFileEndpoint(stack, node_id, row.group(1), row.group(2), sender))
    return endpoints


def send_a_file(
    operator: Operator, sender: BundleFileEndpoint, receiver: BundleFileEndpoint, payload: str
) -> tuple[float | None, bool, str]:
    """From the sender's shell, send one file holding `payload` to the receiver.

    Returns the sender's clock when it sent, whether the sender's node accepted the bundle, and
    what the shell printed. Each stack reports acceptance its own way:
    - HDTN's bpsendfile keeps running after it sends, so it is stopped after 15 s with an
      interrupt, on which it prints its totals; the bundle bytes its node acknowledged show
      acceptance. The totals go to a log file, read afterwards.
    - ION's bpsendfile sends one bundle and exits 0 on most failures; it writes "sent" with
      the file's name into the node's ion.log only when the node took the bundle.
    """
    outbox = f"{sender.directory}/outbox"
    name = f"optest-{uuid.uuid4().hex[:12]}.txt"
    path = f"{outbox}/{name}"
    write = f"mkdir -p {outbox} && printf '%s' '{payload}' > {path} && echo sent-at $(date +%s.%N)"
    if sender.stack == "hdtn":
        log = f"{path}.log"
        send = (
            f"timeout -s INT 15 bpsendfile --use-bp-version-7 "
            f"--bundle-lifetime-milliseconds=86400000 --my-uri-eid={sender.sender_eid} "
            f"--dest-uri-eid={receiver.receiver_eid} "
            f"--outducts-config-file=/run/hdtn/app-outduct.json "
            f"--file-or-folder-path={path} > {log} 2>&1; grep totalBundleBytesAcked {log}"
        )
    else:
        send = (
            f"cd /run/ion && bpsendfile {sender.sender_eid} {receiver.receiver_eid} {path} 0 86400"
            f"; grep -c \"bpsendfile sent '{path}'\" ion.log"
        )
    with operator.terminal(sender.node_id) as terminal:
        _, printed = run_in_shell(terminal, f"{write} && {send}", timeout=40.0)
    sent = re.search(r"^sent-at (\d+\.\d+)$", printed, re.M)
    if sender.stack == "hdtn":
        acked = re.search(r"totalBundleBytesAcked (\d+)", printed)
        accepted = acked is not None and int(acked.group(1)) > 0
    else:
        accepted = re.search(r"^1$", printed, re.M) is not None
    return (float(sent.group(1)) if sent else None, accepted, printed)


def received_files(operator: Operator, receiver: BundleFileEndpoint) -> dict[str, float]:
    """The files in the receiver's received directory: content -> its clock when written.

    Files are told apart by content: HDTN's receiver keeps the sent name, ION's names files
    testfileN in arrival order and writes its own ion.log beside them.
    """
    files_glob = "testfile*" if receiver.stack == "ion" else "*"
    with operator.terminal(receiver.node_id) as inbox:
        _, printed = run_in_shell(
            inbox,
            f'for f in {receiver.directory}/received/{files_glob}; do [ -f "$f" ] && '
            'echo "file $(date -r "$f" +%s.%N) $(cat "$f")"; done; true',
        )
    files: dict[str, float] = {}
    for line in printed.splitlines():
        if found := re.match(r"^file (\d+\.\d+) (.*)$", line):
            files[found.group(2)] = float(found.group(1))
    return files


@dataclass(frozen=True)
class HdtnLink:
    peer: str  # the neighbour member
    index: int  # HDTN's outduct index: the node's neighbours in name order
    timeout_s: float  # LTP's wait before it retries a ping: 2 x (light time + margin)


@dataclass(frozen=True)
class HdtnNode:
    node_id: str
    member: str
    links: tuple[HdtnLink, ...]


_BEGIN, _END = "--begin--", "--end--"


def find_hdtn_nodes(operator: Operator, shown: ShownNetwork) -> list[HdtnNode]:
    """Every host node running HDTN, relays included, with the LTP links its configuration names."""
    nodes: list[HdtnNode] = []
    for node_id, node in sorted(shown.nodes.items()):
        if node["role"] != "host":
            continue
        with operator.terminal(node_id) as terminal:
            if terminal.is_routing_cli:
                continue
            _, printed = run_in_shell(
                terminal,
                f"command -v hdtn-one-process >/dev/null && echo {_BEGIN} "
                f"&& tr -d '\\n' < /run/hdtn/hdtn.json && echo && echo {_END}",
            )
        if _BEGIN not in printed:
            continue
        body = printed.split(_BEGIN, 1)[1].split(_END, 1)[0].replace("\r", "").strip()
        config = json.loads(body)
        member = re.match(r"(\S+) \(ipn:\d+\)", config["hdtnConfigName"])
        assert member, f"{node_id}: hdtn.json names no member: {config['hdtnConfigName']!r}"
        links = []
        for index, outduct in enumerate(config["outductsConfig"]["outductVector"]):
            peer = re.match(r"ltp-to-(\S+)$", outduct["name"])
            if peer is None:
                continue  # the node's own file receiver
            links.append(
                HdtnLink(
                    peer.group(1),
                    index,
                    2 * (outduct["oneWayLightTimeMs"] + outduct["oneWayMarginTimeMs"]) / 1000.0,
                )
            )
        nodes.append(HdtnNode(node_id, member.group(1), tuple(links)))
    return nodes


def hdtn_links_up(operator: Operator, node: HdtnNode) -> set[int]:
    """The outduct indexes whose last link event in the node's log is LINK UP."""
    with operator.terminal(node.node_id) as terminal:
        _, printed = run_in_shell(
            terminal,
            "grep -o 'LINK \\(UP\\|DOWN\\) Event sent for outductArrayIndex=[0-9]*' "
            "/run/hdtn/hdtn.log; true",
        )
    state: dict[int, bool] = {}
    for event, index in re.findall(
        r"LINK (UP|DOWN) Event sent for outductArrayIndex=(\d+)", printed
    ):
        state[int(index)] = event == "UP"
    return {index for index, up in state.items() if up}
