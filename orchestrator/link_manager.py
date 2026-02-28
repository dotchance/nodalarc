"""Link manager — all pyroute2 netlink operations.

Creates/destroys veth pairs, creates dummy interfaces, sets interface
state, applies tc netem/tbf. Does NOT compute latency, decide when
links change, or know about OME events.

All netlink operations use pyroute2 directly (PRD 13.6).
"""

from __future__ import annotations

import json
import logging
import subprocess

import kubernetes.client
import kubernetes.config
from pyroute2 import IPRoute, NetNS

log = logging.getLogger(__name__)


def discover_pod_pids(
    namespace: str = "nodalarc",
    label_selector: str = "nodalarc.io/role",
) -> dict[str, int]:
    """Discover container PIDs for all pods in a namespace.

    Uses K8s API → container ID → crictl inspect → PID.
    Returns {node_id: pid}.
    """
    try:
        kubernetes.config.load_kube_config()
    except kubernetes.config.config_exception.ConfigException:
        kubernetes.config.load_incluster_config()
    v1 = kubernetes.client.CoreV1Api()
    pods = v1.list_namespaced_pod(namespace, label_selector=label_selector)

    result: dict[str, int] = {}
    for pod in pods.items:
        node_id = pod.metadata.labels.get("nodalarc.io/node-id")
        if not node_id:
            continue
        container_id = pod.status.container_statuses[0].container_id
        # Strip containerd:// prefix
        raw_id = container_id.split("://", 1)[-1]
        # crictl inspect → parse JSON → info.pid
        proc = subprocess.run(
            ["crictl", "inspect", raw_id],
            capture_output=True, text=True, check=True,
        )
        info = json.loads(proc.stdout)
        pid = info["info"]["pid"]
        result[node_id] = pid
        log.info(f"Discovered {node_id} → PID {pid}")

    return result


def create_veth_pair(
    pid_a: int,
    pid_b: int,
    ifname_a: str,
    ifname_b: str,
    mtu: int = 9000,
) -> None:
    """Create a veth pair and move ends into the given namespaces.

    Each end is renamed to the specified ifname inside its namespace.
    """
    # Create veth pair in the host namespace with temp names
    tmp_a = f"_na_{ifname_a}_{pid_a}"[:15]
    tmp_b = f"_na_{ifname_b}_{pid_b}"[:15]

    ipr = IPRoute()
    try:
        ipr.link("add", ifname=tmp_a, peer={"ifname": tmp_b}, kind="veth")

        # Move end A into pid_a's namespace
        idx_a = ipr.link_lookup(ifname=tmp_a)[0]
        ipr.link("set", index=idx_a, net_ns_pid=pid_a)

        # Move end B into pid_b's namespace
        idx_b = ipr.link_lookup(ifname=tmp_b)[0]
        ipr.link("set", index=idx_b, net_ns_pid=pid_b)
    finally:
        ipr.close()

    # Rename and configure inside namespace A
    ns_a = NetNS(f"/proc/{pid_a}/ns/net")
    try:
        idx = ns_a.link_lookup(ifname=tmp_a)[0]
        ns_a.link("set", index=idx, ifname=ifname_a, mtu=mtu)
    finally:
        ns_a.close()

    # Rename and configure inside namespace B
    ns_b = NetNS(f"/proc/{pid_b}/ns/net")
    try:
        idx = ns_b.link_lookup(ifname=tmp_b)[0]
        ns_b.link("set", index=idx, ifname=ifname_b, mtu=mtu)
    finally:
        ns_b.close()

    log.info(f"Created veth pair: ns({pid_a})/{ifname_a} <-> ns({pid_b})/{ifname_b}")


def create_dummy_interface(
    pid: int,
    ifname: str,
    addresses: list[str],
) -> None:
    """Create a dummy interface inside a namespace with given addresses.

    Used for terrestrial prefix interfaces (terr0) on ground station pods.
    """
    ns = NetNS(f"/proc/{pid}/ns/net")
    try:
        ns.link("add", ifname=ifname, kind="dummy")
        idx = ns.link_lookup(ifname=ifname)[0]
        ns.link("set", index=idx, state="up")
        for addr in addresses:
            ns.addr("add", index=idx, address=addr.split("/")[0],
                    prefixlen=int(addr.split("/")[1]))
    finally:
        ns.close()
    log.info(f"Created dummy {ifname} in ns({pid}) with {len(addresses)} addrs")


def set_interface_up(pid: int, ifname: str) -> None:
    """Bring an interface up inside a namespace."""
    ns = NetNS(f"/proc/{pid}/ns/net")
    try:
        idx = ns.link_lookup(ifname=ifname)[0]
        ns.link("set", index=idx, state="up")
    finally:
        ns.close()


def set_interface_down(pid: int, ifname: str) -> None:
    """Bring an interface down inside a namespace."""
    ns = NetNS(f"/proc/{pid}/ns/net")
    try:
        idx = ns.link_lookup(ifname=ifname)[0]
        ns.link("set", index=idx, state="down")
    finally:
        ns.close()


def apply_link_shaping(
    pid: int,
    ifname: str,
    delay_ms: float,
    rate_mbps: float,
) -> None:
    """Apply tc tbf root + netem child for bandwidth and delay.

    Called once when a link goes up. Subsequent delay changes use
    update_delay().

    PRD Appendix A: tbf root qdisc, netem child.
    """
    rate_bps = int(rate_mbps * 1_000_000)
    # tbf burst: at least 1 MTU, typically rate / 250 Hz
    burst = max(9000, rate_bps // 250)
    # tbf latency: buffer time in microseconds
    latency_us = 50000  # 50ms buffer
    delay_us = int(delay_ms * 1000)

    ns = NetNS(f"/proc/{pid}/ns/net")
    try:
        idx = ns.link_lookup(ifname=ifname)[0]
        # Root: tbf for bandwidth shaping (handle 1:0)
        ns.tc("add", kind="tbf", index=idx, handle=0x00010000,
              rate=rate_bps, burst=burst, latency=latency_us)
        # Child: netem for delay (under class 1:1)
        ns.tc("add", kind="netem", index=idx, handle=0x00100000,
              parent=0x00010001, delay=delay_us)
    finally:
        ns.close()
    log.info(f"Applied shaping on ns({pid})/{ifname}: {delay_ms}ms, {rate_mbps}Mbps")


def update_delay(pid: int, ifname: str, delay_ms: float) -> None:
    """Update netem delay on an existing qdisc chain."""
    delay_us = int(delay_ms * 1000)
    ns = NetNS(f"/proc/{pid}/ns/net")
    try:
        idx = ns.link_lookup(ifname=ifname)[0]
        ns.tc("change", kind="netem", index=idx, handle=0x00100000,
              parent=0x00010001, delay=delay_us)
    finally:
        ns.close()


def remove_link_shaping(pid: int, ifname: str) -> None:
    """Remove all tc qdiscs from an interface."""
    ns = NetNS(f"/proc/{pid}/ns/net")
    try:
        idx = ns.link_lookup(ifname=ifname)[0]
        ns.tc("del", index=idx, handle=0x00010000, parent=0xFFFF0000)
    finally:
        ns.close()
