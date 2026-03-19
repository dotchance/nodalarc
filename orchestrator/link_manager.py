"""Link manager — all pyroute2 netlink operations.

Creates/destroys veth pairs, creates dummy interfaces, sets interface
state, applies tc netem/tbf, manages ground station bridges.
Does NOT compute latency, decide when links change, or know about OME events.

All netlink operations use pyroute2 directly (PRD 13.6).
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import logging
import os
import subprocess

import kubernetes.client
import kubernetes.config
from pyroute2 import IPRoute, NetNS

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Naming helpers for ground link infrastructure (15-char Linux limit)
# ---------------------------------------------------------------------------


def _sat_short_id(sat_id: str) -> str:
    """Stable short identifier from satellite ID.

    "sat-P00S05" → "P00S05"
    """
    if sat_id.startswith("sat-"):
        return sat_id[4:]
    return sat_id[-10:]


def _gs_short_name(gs_id: str) -> str:
    """Extract station name from gs_id, stripping 'gs-' prefix."""
    return gs_id[3:] if gs_id.startswith("gs-") else gs_id


def _gs_bridge_name(gs_id: str) -> str:
    """Bridge device name for a ground station. ≤15 chars."""
    return f"brg-{_gs_short_name(gs_id)}"[:15]


def _gs_bridge_port_name(gs_id: str) -> str:
    """Host-side veth name for GS bridge port. ≤15 chars."""
    return f"_gbr-{_gs_short_name(gs_id)}"[:15]


def _sat_gnd_host_name(sat_id: str) -> str:
    """Host-side veth name for satellite ground link. ≤15 chars."""
    return f"_gnd_{_sat_short_id(sat_id)}"[:15]


def _bridge_sysfs(bridge_name: str, param: str, value: str) -> None:
    """Write a bridge parameter via sysfs."""
    sysfs_path = f"/sys/class/net/{bridge_name}/bridge/{param}"
    try:
        with open(sysfs_path, "w") as f:
            f.write(value)
    except OSError as exc:
        log.warning(f"Failed to set {param}={value} on {bridge_name}: {exc}")


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
            capture_output=True,
            text=True,
            check=True,
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
            ns.addr("add", index=idx, address=addr.split("/")[0], prefixlen=int(addr.split("/")[1]))
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
            ["nsenter", "--target", str(pid), "--net", "--", "sysctl", "-w", f"{sysctl_key}=0"],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            log.warning(
                f"Failed to set {param}=0 for {ifname} in ns({pid}): {result.stderr.strip()}"
            )


def mac_to_link_local(mac_str: str) -> str:
    """Derive canonical IPv6 link-local from MAC via EUI-64.

    '02:ee:d2:0a:a9:36' → 'fe80::ee:d2ff:fe0a:a936'

    Used to compute the peer's link-local address for NDP solicitation
    and for ``via inet6`` in MPLS routes.
    """
    import ipaddress

    parts = [int(x, 16) for x in mac_str.split(":")]
    parts[0] ^= 0x02  # flip U/L bit
    eui64 = parts[:3] + [0xFF, 0xFE] + parts[3:]
    groups = [
        f"{eui64[0]:02x}{eui64[1]:02x}",
        f"{eui64[2]:02x}{eui64[3]:02x}",
        f"{eui64[4]:02x}{eui64[5]:02x}",
        f"{eui64[6]:02x}{eui64[7]:02x}",
    ]
    raw = "fe80::" + ":".join(groups)
    return str(ipaddress.IPv6Address(raw))


def trigger_ndp_and_wait(pid: int, ifname: str, peer_ll: str, timeout_ms: int = 500) -> bool:
    """Trigger NDP solicitation and wait for the peer to become REACHABLE.

    After the TO brings an ISL or ground interface admin UP, call this
    before emitting LinkUp. This ensures the kernel's neighbor table has
    a resolved L2 entry for the peer's link-local, so MPLS routes with
    ``via inet6 <peer_ll>`` can forward immediately.

    Uses a UDP6 connect() to trigger the kernel to send a Neighbor
    Solicitation, then polls the neighbor table until REACHABLE or STALE.

    Returns True if resolved within timeout, False otherwise.
    On timeout, logs a warning — the sidecar's retry mechanism handles it.
    """
    import time as _time

    ns_path = f"/proc/{pid}/ns/net"
    ns = NetNS(ns_path)
    try:
        iface_links = ns.link_lookup(ifname=ifname)
        if not iface_links:
            log.warning("NDP: interface %s not found in ns(%d)", ifname, pid)
            return False
        iface_idx = iface_links[0]
    finally:
        ns.close()

    # Trigger NS by pinging the peer's link-local with %scope format.
    # The %ifname suffix is required for link-local addresses to bind to
    # the correct interface. Without it, the kernel may send the NS from
    # a different interface and resolve the wrong neighbor.
    subprocess.run(
        [
            "nsenter",
            "--target",
            str(pid),
            "--net",
            "--",
            "ping",
            "-6",
            "-c",
            "1",
            "-W",
            "1",
            f"{peer_ll}%{ifname}",
        ],
        capture_output=True,
        text=True,
        timeout=5,
    )

    # Poll neighbor table
    start = _time.monotonic()
    deadline = start + (timeout_ms / 1000)
    NUD_REACHABLE = 0x02
    NUD_STALE = 0x04
    NUD_FAILED = 0x20

    while _time.monotonic() < deadline:
        ns = NetNS(ns_path)
        try:
            neighbours = ns.get_neighbours(family=10, ifindex=iface_idx)  # AF_INET6=10
            for n in neighbours:
                attrs = dict(n["attrs"])
                if attrs.get("NDA_DST") == peer_ll:
                    state = n["state"]
                    elapsed = (_time.monotonic() - start) * 1000
                    if state & (NUD_REACHABLE | NUD_STALE):
                        log.debug(
                            "NDP resolved %s on %s in ns(%d) in %.1fms",
                            peer_ll,
                            ifname,
                            pid,
                            elapsed,
                        )
                        return True
                    if state & NUD_FAILED:
                        log.error(
                            "NDP FAILED for %s on %s in ns(%d) after %.1fms",
                            peer_ll,
                            ifname,
                            pid,
                            elapsed,
                        )
                        return False
        finally:
            ns.close()
        _time.sleep(0.010)  # 10ms poll

    elapsed = (_time.monotonic() - start) * 1000
    log.warning(
        "NDP timeout for %s on %s in ns(%d) after %.1fms — proceeding, sidecar retry will catch it",
        peer_ll,
        ifname,
        pid,
        elapsed,
    )
    return False


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
        [
            "nsenter",
            "--target",
            str(pid),
            "--net",
            "--",
            "sysctl",
            "-w",
            f"net.mpls.conf.{ifname}.input=1",
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        log.warning(f"Failed to enable MPLS input for {ifname} in ns({pid}): {result.stderr}")


def set_interface_up(pid: int, ifname: str) -> None:
    """Bring an interface up inside a namespace."""
    ns = NetNS(f"/proc/{pid}/ns/net")
    try:
        links = ns.link_lookup(ifname=ifname)
        if not links:
            raise FileNotFoundError(f"Interface {ifname} not found in ns({pid})")
        ns.link("set", index=links[0], state="up")
    finally:
        ns.close()


def set_interface_down(pid: int, ifname: str) -> None:
    """Bring an interface down inside a namespace."""
    ns = NetNS(f"/proc/{pid}/ns/net")
    try:
        links = ns.link_lookup(ifname=ifname)
        if not links:
            raise FileNotFoundError(f"Interface {ifname} not found in ns({pid})")
        ns.link("set", index=links[0], state="down")
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
        links = ns.link_lookup(ifname=ifname)
        if not links:
            raise FileNotFoundError(f"Interface {ifname} not found in ns({pid})")
        idx = links[0]
        # Remove existing qdiscs (idempotent)
        with contextlib.suppress(Exception):
            ns.tc("del", index=idx, root=True)
        # Root: tbf for bandwidth shaping (handle 1:0)
        ns.tc(
            "add",
            kind="tbf",
            index=idx,
            handle=0x00010000,
            rate=rate_bps,
            burst=burst,
            latency=latency_us,
        )
        # Child: netem for delay (under class 1:1)
        ns.tc("add", kind="netem", index=idx, handle=0x00100000, parent=0x00010001, delay=delay_us)
    finally:
        ns.close()
    log.info(f"Applied shaping on ns({pid})/{ifname}: {delay_ms}ms, {rate_mbps}Mbps")


def update_delay(pid: int, ifname: str, delay_ms: float) -> None:
    """Update netem delay on an existing qdisc chain."""
    delay_us = int(delay_ms * 1000)
    ns = NetNS(f"/proc/{pid}/ns/net")
    try:
        links = ns.link_lookup(ifname=ifname)
        if not links:
            raise FileNotFoundError(f"Interface {ifname} not found in ns({pid})")
        ns.tc(
            "change",
            kind="netem",
            index=links[0],
            handle=0x00100000,
            parent=0x00010001,
            delay=delay_us,
        )
    finally:
        ns.close()


def set_link_metric(
    pod_name: str,
    ifname: str,
    metric: int,
    routing_protocol: str,
    namespace: str | None = None,
) -> None:
    """Set link metric on an interface via vtysh for the given routing protocol.

    Args:
        pod_name: K8s pod name (used with kubectl exec).
        ifname: Interface name (e.g., gnd0, isl0).
        metric: Metric value to set.
        routing_protocol: One of "isis", "ospf", "bgp". Determines the vtysh command.
        namespace: K8s namespace (defaults to platform config).
    """
    if namespace is None:
        from nodalarc.platform import get_platform_config

        namespace = get_platform_config().kubernetes_namespace

    if routing_protocol == "isis":
        metric_cmd = f"isis metric {metric}"
    elif routing_protocol == "ospf":
        metric_cmd = f"ip ospf cost {metric}"
    else:
        return  # No metric setting for this protocol

    env = {**os.environ, "KUBECONFIG": os.environ.get("KUBECONFIG", "/etc/rancher/k3s/k3s.yaml")}
    subprocess.run(
        [
            "kubectl",
            "exec",
            "-n",
            namespace,
            pod_name,
            "-c",
            "frr",
            "--",
            "vtysh",
            "-c",
            "configure terminal",
            "-c",
            f"interface {ifname}",
            "-c",
            metric_cmd,
        ],
        capture_output=True,
        text=True,
        timeout=10,
        env=env,
    )


def remove_link_shaping(pid: int, ifname: str) -> None:
    """Remove all tc qdiscs from an interface."""
    ns = NetNS(f"/proc/{pid}/ns/net")
    try:
        idx = ns.link_lookup(ifname=ifname)[0]
        ns.tc("del", index=idx, root=True)
    except Exception:
        pass  # Interface may already be gone or no qdisc set
    finally:
        ns.close()


# ---------------------------------------------------------------------------
# Ground station link infrastructure (tc mirred redirect)
#
# Linux bridges don't deliver multicast to the IP stack on bridged-veth
# peers, which prevents OSPF adjacency formation.  Instead, we use
# tc ingress + mirred egress redirect to create a point-to-point L2
# connection between the GS host-side veth and the satellite host-side
# veth.  This is functionally equivalent to directly connecting them
# with a patch cable.
# ---------------------------------------------------------------------------


def create_ground_bridge(
    gs_id: str,
    gs_pid: int,
    mtu: int | None = None,
) -> str:
    """Create GS-side veth pair for ground link. Idempotent.

    Creates a veth pair: host end (_gbr-{gs}) stays in host ns (DOWN),
    GS end moved into GS namespace as gnd0 (DOWN).

    GS gnd0 is left admin DOWN for the caller to configure (MAC, MPLS)
    and then bring UP.

    Returns host-side veth name (used as the GS "port" for tc redirect).
    """
    if mtu is None:
        from nodalarc.platform import get_platform_config

        mtu = get_platform_config().veth_interface_mtu_bytes

    gs_port = _gs_bridge_port_name(gs_id)

    ipr = IPRoute()
    try:
        # Idempotent: skip if host port already exists
        if ipr.link_lookup(ifname=gs_port):
            log.debug(f"GS port {gs_port} already exists")
            return gs_port

        # Check if gnd0 already exists in GS namespace
        ns = NetNS(f"/proc/{gs_pid}/ns/net")
        try:
            if ns.link_lookup(ifname="gnd0"):
                log.debug(f"gnd0 already exists in GS ns({gs_pid})")
                return gs_port
        finally:
            ns.close()

        # Create veth pair with temp names
        rand = os.urandom(3).hex()
        tmp_host = f"_na_h{rand}"[:15]
        tmp_ns = f"_na_n{rand}"[:15]

        for tmp in [tmp_host, tmp_ns]:
            stale = ipr.link_lookup(ifname=tmp)
            if stale:
                ipr.link("del", index=stale[0])

        ipr.link("add", ifname=tmp_host, peer={"ifname": tmp_ns}, kind="veth")

        # Host end: rename, set MTU — leave DOWN
        host_idx = ipr.link_lookup(ifname=tmp_host)[0]
        ipr.link("set", index=host_idx, ifname=gs_port, mtu=mtu)

        # Move NS end into GS namespace
        ns_idx = ipr.link_lookup(ifname=tmp_ns)[0]
        ipr.link("set", index=ns_idx, net_ns_pid=gs_pid)

        # Rename to gnd0 inside GS namespace, leave DOWN
        ns = NetNS(f"/proc/{gs_pid}/ns/net")
        try:
            idx = ns.link_lookup(ifname=tmp_ns)[0]
            ns.link("set", index=idx, ifname="gnd0", mtu=mtu)
        finally:
            ns.close()

        log.info(f"Created GS port {gs_port} → gnd0 in ns({gs_pid})")
    finally:
        ipr.close()

    return gs_port


def create_satellite_ground_veth(
    sat_id: str,
    sat_pid: int,
    mtu: int | None = None,
) -> tuple[str, str]:
    """Pre-create satellite ground veth pair at deploy time. Idempotent.

    Creates veth pair:
    - Host side: stays in host ns, admin DOWN, unattached to any bridge
    - NS side: moved into satellite ns as gnd0, admin DOWN

    Returns (host_side_name, "gnd0").
    """
    if mtu is None:
        from nodalarc.platform import get_platform_config

        mtu = get_platform_config().veth_interface_mtu_bytes

    host_name = _sat_gnd_host_name(sat_id)

    ipr = IPRoute()
    try:
        # Idempotent: skip if host side already exists
        if ipr.link_lookup(ifname=host_name):
            log.debug(f"Satellite ground veth {host_name} already exists")
            return (host_name, "gnd0")

        # Also check if gnd0 already in satellite namespace
        ns = NetNS(f"/proc/{sat_pid}/ns/net")
        try:
            if ns.link_lookup(ifname="gnd0"):
                log.debug(f"gnd0 already exists in sat ns({sat_pid})")
                return (host_name, "gnd0")
        finally:
            ns.close()

        # Create veth pair with temp names
        rand = os.urandom(3).hex()
        tmp_host = f"_na_h{rand}"[:15]
        tmp_ns = f"_na_n{rand}"[:15]

        for tmp in [tmp_host, tmp_ns]:
            stale = ipr.link_lookup(ifname=tmp)
            if stale:
                ipr.link("del", index=stale[0])

        ipr.link("add", ifname=tmp_host, peer={"ifname": tmp_ns}, kind="veth")

        # Host end: rename, set MTU — leave DOWN and unattached
        host_idx = ipr.link_lookup(ifname=tmp_host)[0]
        ipr.link("set", index=host_idx, ifname=host_name, mtu=mtu)

        # Move NS end into satellite namespace
        ns_idx = ipr.link_lookup(ifname=tmp_ns)[0]
        ipr.link("set", index=ns_idx, net_ns_pid=sat_pid)

        # Rename to gnd0 inside satellite namespace, leave DOWN
        ns = NetNS(f"/proc/{sat_pid}/ns/net")
        try:
            idx = ns.link_lookup(ifname=tmp_ns)[0]
            ns.link("set", index=idx, ifname="gnd0", mtu=mtu)
        finally:
            ns.close()
    finally:
        ipr.close()

    log.info(f"Created satellite ground veth {host_name} ↔ gnd0 in ns({sat_pid})")
    return (host_name, "gnd0")


def _tc_mirred_redirect(src: str, dst: str) -> None:
    """Install tc ingress + mirred egress redirect from src to dst."""
    # Remove stale ingress qdisc (idempotent)
    subprocess.run(
        ["tc", "qdisc", "del", "dev", src, "ingress"],
        capture_output=True,
    )
    subprocess.run(
        ["tc", "qdisc", "add", "dev", src, "ingress"],
        capture_output=True,
        check=True,
    )
    subprocess.run(
        [
            "tc",
            "filter",
            "add",
            "dev",
            src,
            "parent",
            "ffff:",
            "protocol",
            "all",
            "u32",
            "match",
            "u32",
            "0",
            "0",
            "action",
            "mirred",
            "egress",
            "redirect",
            "dev",
            dst,
        ],
        capture_output=True,
        check=True,
    )


def _tc_mirred_remove(ifname: str) -> None:
    """Remove tc ingress qdisc (and all its filters) from an interface."""
    subprocess.run(
        ["tc", "qdisc", "del", "dev", ifname, "ingress"],
        capture_output=True,
    )


def attach_to_ground_bridge(
    gs_id: str,
    sat_id: str,
    sat_pid: int,
) -> None:
    """Connect satellite to GS via tc mirred redirect.

    Brings both host-side veths and satellite gnd0 admin UP, then
    installs bidirectional tc mirred redirect between the GS and
    satellite host-side veths.
    """
    gs_port = _gs_bridge_port_name(gs_id)
    host_veth = _sat_gnd_host_name(sat_id)

    ipr = IPRoute()
    try:
        for name in (gs_port, host_veth):
            idx = ipr.link_lookup(ifname=name)
            if not idx:
                raise FileNotFoundError(f"{name} not found")
            ipr.link("set", index=idx[0], state="up")
    finally:
        ipr.close()

    # Bring satellite gnd0 UP
    ns = NetNS(f"/proc/{sat_pid}/ns/net")
    try:
        gnd_idx = ns.link_lookup(ifname="gnd0")
        if not gnd_idx:
            raise FileNotFoundError(f"gnd0 not found in sat ns({sat_pid})")
        ns.link("set", index=gnd_idx[0], state="up")
    finally:
        ns.close()

    # Bidirectional tc mirred redirect between host-side veths
    _tc_mirred_redirect(gs_port, host_veth)
    _tc_mirred_redirect(host_veth, gs_port)

    log.info(f"Attached {sat_id} to {gs_id} (tc redirect)")


def detach_from_ground_bridge(
    gs_id: str,
    sat_id: str,
    sat_pid: int,
) -> None:
    """Disconnect satellite from GS.

    Removes tc mirred redirect, then brings satellite gnd0 and
    host veth admin DOWN.
    """
    gs_port = _gs_bridge_port_name(gs_id)
    host_veth = _sat_gnd_host_name(sat_id)

    # Remove tc redirect first
    _tc_mirred_remove(gs_port)
    _tc_mirred_remove(host_veth)

    # Bring satellite gnd0 DOWN
    ns = NetNS(f"/proc/{sat_pid}/ns/net")
    try:
        gnd_idx = ns.link_lookup(ifname="gnd0")
        if gnd_idx:
            ns.link("set", index=gnd_idx[0], state="down")
    finally:
        ns.close()

    # Bring host veth DOWN
    ipr = IPRoute()
    try:
        host_idx = ipr.link_lookup(ifname=host_veth)
        if host_idx:
            ipr.link("set", index=host_idx[0], state="down")
    finally:
        ipr.close()

    log.info(f"Detached {sat_id} from {gs_id}")


def teardown_ground_bridge(gs_id: str) -> None:
    """Remove GS port veth (and any legacy bridge) for a ground station."""
    gs_port = _gs_bridge_port_name(gs_id)
    bridge_name = _gs_bridge_name(gs_id)

    _tc_mirred_remove(gs_port)

    ipr = IPRoute()
    try:
        port_idx = ipr.link_lookup(ifname=gs_port)
        if port_idx:
            ipr.link("del", index=port_idx[0])
            log.info(f"Deleted GS port veth {gs_port}")

        # Clean up legacy bridge if present
        bridge_idx = ipr.link_lookup(ifname=bridge_name)
        if bridge_idx:
            ipr.link("del", index=bridge_idx[0])
            log.info(f"Deleted legacy bridge {bridge_name}")
    finally:
        ipr.close()


def teardown_satellite_ground_veth(sat_id: str) -> None:
    """Remove satellite ground veth pair."""
    host_veth = _sat_gnd_host_name(sat_id)

    _tc_mirred_remove(host_veth)

    ipr = IPRoute()
    try:
        idx = ipr.link_lookup(ifname=host_veth)
        if idx:
            ipr.link("del", index=idx[0])
            log.info(f"Deleted satellite ground veth {host_veth}")
    finally:
        ipr.close()


def teardown_all_ground_infra() -> None:
    """Remove all ground bridge infrastructure from host namespace.

    Finds and deletes all bridges (brg-*), satellite ground veths (_gnd_*),
    and GS bridge port veths (_gbr-*). Idempotent.
    """
    ipr = IPRoute()
    try:
        to_delete: list[tuple[int, str]] = []
        for link in ipr.get_links():
            ifname = link.get_attr("IFLA_IFNAME")
            if ifname and (
                ifname.startswith("brg-")
                or ifname.startswith("_gnd_")
                or ifname.startswith("_gbr-")
            ):
                to_delete.append((link["index"], ifname))

        for idx, ifname in to_delete:
            try:
                ipr.link("del", index=idx)
                log.info(f"Cleaned up {ifname}")
            except Exception:
                pass  # May already be gone (deleting veth deletes peer)

        if to_delete:
            log.info(f"Cleaned up {len(to_delete)} ground infrastructure devices")
    finally:
        ipr.close()
