"""Link manager — all pyroute2 netlink operations.

Creates/destroys veth pairs, creates dummy interfaces, sets interface
state, applies tc netem/tbf. Does NOT compute latency, decide when
links change, or know about OME events.

All netlink operations use pyroute2 directly (PRD 13.6).
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import subprocess

import kubernetes.client
import kubernetes.config
from pyroute2 import IPRoute, NetNS

log = logging.getLogger(__name__)


def discover_pod_pids(
    namespace: str | None = None,
    label_selector: str = "nodalarc.io/role",
) -> dict[str, int]:
    """Discover container PIDs for all pods in a namespace.

    Uses K8s API → container ID → crictl inspect → PID.
    Returns {node_id: pid}.
    """
    if namespace is None:
        from nodalarc.platform import get_platform_config
        namespace = get_platform_config().kubernetes_namespace
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
    mtu: int | None = None,
    node_id_a: str = "",
    node_id_b: str = "",
) -> None:
    """Create a veth pair and move ends into the given namespaces.

    Each end is renamed to the specified ifname inside its namespace.
    PRD 13.6: sets MTU, disables IPv6 autoconfig, sets deterministic MACs.
    """
    if mtu is None:
        from nodalarc.platform import get_platform_config
        mtu = get_platform_config().veth_interface_mtu_bytes
    # Clean up stale interfaces if they exist in the target namespaces
    for pid, ifname in [(pid_a, ifname_a), (pid_b, ifname_b)]:
        ns = NetNS(f"/proc/{pid}/ns/net")
        try:
            links = ns.link_lookup(ifname=ifname)
            if links:
                ns.link("del", index=links[0])
                log.info(f"Cleaned stale {ifname} in ns({pid})")
        except Exception:
            pass
        finally:
            ns.close()

    # Use random temp names to avoid collisions with stale interfaces
    rand = os.urandom(3).hex()
    tmp_a = f"_na_a{rand}"[:15]
    tmp_b = f"_na_b{rand}"[:15]

    # Clean stale temp names from host namespace (just in case)
    ipr = IPRoute()
    try:
        for tmp in [tmp_a, tmp_b]:
            links = ipr.link_lookup(ifname=tmp)
            if links:
                ipr.link("del", index=links[0])
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

    # PRD 13.6 step 5: disable IPv6 autoconfig, set deterministic MACs
    if node_id_a:
        configure_interface(pid_a, ifname_a, node_id_a)
    if node_id_b:
        configure_interface(pid_b, ifname_b, node_id_b)

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


def disable_ipv6_autoconfig(pid: int, ifname: str) -> None:
    """Disable IPv6 autoconfig on an interface (PRD 13.6 step 5).

    Uses nsenter to enter the network namespace because K3s mounts
    /proc/sys read-only inside containers.
    """
    for param in ("accept_ra", "autoconf"):
        sysctl_key = f"net.ipv6.conf.{ifname}.{param}"
        result = subprocess.run(
            ["nsenter", "--target", str(pid), "--net", "--",
             "sysctl", "-w", f"{sysctl_key}=0"],
            capture_output=True, text=True,
        )
        if result.returncode != 0:
            log.warning(f"Failed to set {param}=0 for {ifname} in ns({pid}): {result.stderr.strip()}")


def deterministic_mac(node_id: str, ifname: str) -> str:
    """Derive a deterministic locally-administered unicast MAC address.

    Format: 02:XX:XX:XX:XX:XX where XX bytes come from SHA-256 of
    node_id + ifname. The 02 prefix sets the locally-administered bit.
    """
    digest = hashlib.sha256(f"{node_id}:{ifname}".encode()).digest()
    return f"02:{digest[0]:02x}:{digest[1]:02x}:{digest[2]:02x}:{digest[3]:02x}:{digest[4]:02x}"


def configure_interface(pid: int, ifname: str, node_id: str) -> None:
    """Apply PRD 13.6 step 5 configuration to a veth interface.

    Disables IPv6 autoconfig and sets a deterministic MAC address.
    MTU is set during veth creation.
    """
    disable_ipv6_autoconfig(pid, ifname)
    mac = deterministic_mac(node_id, ifname)
    ns = NetNS(f"/proc/{pid}/ns/net")
    try:
        idx = ns.link_lookup(ifname=ifname)[0]
        ns.link("set", index=idx, address=mac)
    finally:
        ns.close()
    log.debug(f"Configured {ifname} in ns({pid}): mac={mac}, ipv6_autoconfig=off")


def destroy_veth_pair(pid: int, ifname: str) -> None:
    """Destroy a veth pair by deleting one end inside a namespace.

    Deleting one end automatically removes the peer end.
    """
    ns = NetNS(f"/proc/{pid}/ns/net")
    try:
        links = ns.link_lookup(ifname=ifname)
        if links:
            ns.link("del", index=links[0])
    finally:
        ns.close()
    log.info(f"Destroyed veth {ifname} in ns({pid})")


def enable_mpls_input(pid: int, ifname: str) -> None:
    """Enable MPLS input on an interface inside the namespace.

    Uses nsenter to enter the network namespace because K3s mounts
    /proc/sys read-only inside containers.
    """
    result = subprocess.run(
        ["nsenter", "--target", str(pid), "--net", "--",
         "sysctl", "-w", f"net.mpls.conf.{ifname}.input=1"],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        log.warning(f"Failed to enable MPLS input for {ifname} in ns({pid}): {result.stderr}")


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
        # Remove existing qdiscs (idempotent)
        try:
            ns.tc("del", index=idx, root=True)
        except Exception:
            pass
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


def set_isis_metric(pod_name: str, ifname: str, metric: int, namespace: str | None = None) -> None:
    """Set IS-IS metric on an interface via vtysh."""
    if namespace is None:
        from nodalarc.platform import get_platform_config
        namespace = get_platform_config().kubernetes_namespace
    env = {**os.environ, "KUBECONFIG": os.environ.get("KUBECONFIG", "/etc/rancher/k3s/k3s.yaml")}
    subprocess.run(
        ["kubectl", "exec", "-n", namespace, pod_name, "-c", "frr", "--",
         "vtysh", "-c", "configure terminal",
         "-c", f"interface {ifname}",
         "-c", f"isis metric {metric}"],
        capture_output=True, text=True, timeout=10, env=env,
    )


def remove_link_shaping(pid: int, ifname: str) -> None:
    """Remove all tc qdiscs from an interface."""
    ns = NetNS(f"/proc/{pid}/ns/net")
    try:
        idx = ns.link_lookup(ifname=ifname)[0]
        ns.tc("del", index=idx, root=True)
    except Exception:
        pass  # Interface may already be gone (dynamic veth)
    finally:
        ns.close()
