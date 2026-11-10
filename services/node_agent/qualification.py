# Copyright 2024-2026 .chance (dotchance)
# SPDX-License-Identifier: AGPL-3.0-only
"""Node qualification: whether this server can run NodalArc session pods.

Every check reads the server from the calling process: the kernel's module
tables, /proc, and netlink. ``python -m node_agent.qualification`` prints
each check and exits 0 only when every hard requirement holds. It changes
nothing. The Node Agent DaemonSet's readiness probe runs it in the Node
Agent container on every probe: each probe reports what the server has at
that moment, so a server becomes ready as soon as its checks pass and
unready when one fails. The DaemonSet shows desired against ready Node
Agents, the probe's failure message names the failed checks, and the
Operator places session pods only on servers whose Node Agent is ready. To
see every check, passed and failed, run it by hand in a Node Agent pod::

    kubectl exec <node agent pod> -c node-agent -- python -m node_agent.qualification \
        --host-ip <node address> --link-mtu 8800 --vxlan-port 14789 \
        --queue-bytes-per-buffer 73728

Checks between hosts (the path MTU) depend on both ends and are not part of
a node's qualification: each session proves its own host paths before it is
wired.

One check changes kernel state and is not part of the probe:
``check_purge_counts`` runs once when the Node Agent starts, before it serves
any command, inside a scratch network namespace that ends with the check.
"""

from __future__ import annotations

import argparse
import ipaddress
import json
import os
import socket
import sys
import threading
from dataclasses import asdict, dataclass
from pathlib import Path

from nodalarc.runtime_naming import MANAGED_HOST_DEVICE_GROUP
from nodalarc.vxlan import host_path_mtu_for
from pyroute2 import IPRoute

# Kernel modules every session needs: links, shaping, the cni0 filters (sch_ingress provides
# the clsact qdisc the filters hang on), and the admission filter of long-delay links
# (cls_matchall with act_gact).
REQUIRED_KERNEL_MODULES = (
    "vxlan",
    "veth",
    "bridge",
    "vrf",
    "sch_htb",
    "sch_netem",
    "sch_ingress",
    "cls_u32",
    "act_mirred",
    "cls_matchall",
    "act_gact",
)
# The largest buffer a link queue can receive from an interface NodalArc creates: the kernel's
# default GSO and GRO limit, which its veths and VXLAN devices keep.
DEFAULT_GSO_GRO_MAX_BYTES = 65_536
# Per-buffer overhead the queue memory estimate allows besides the data and page rounding: the
# buffer's descriptor and shared info, about 920 bytes measured on kernel 6.8.
BUFFER_OVERHEAD_BYTES = 1_024
FEATURE_KERNEL_MODULES = {"MPLS": ("mpls_router", "mpls_iptunnel")}
MIN_INOTIFY_INSTANCES = 8192
MIN_INOTIFY_WATCHES = 65536


@dataclass(frozen=True, slots=True)
class Check:
    """One qualification check: what the host has against what NodalArc needs."""

    name: str
    passed: bool
    value: str
    needed: str
    hard: bool = True

    def line(self) -> str:
        verdict = "pass" if self.passed else ("FAIL" if self.hard else "absent")
        return f"{verdict:6} {self.name}: {self.value} (needs {self.needed})"


def _module_available(name: str, *, sys_root: Path, modules_dir: Path) -> str | None:
    """How the kernel offers a module: loaded, built in, loadable; None when it cannot."""
    if (sys_root / "module" / name).is_dir():
        return "loaded"
    for listing, how in (("modules.builtin", "built in"), ("modules.dep", "loadable")):
        try:
            text = (modules_dir / listing).read_text()
        except OSError:
            continue
        for line in text.splitlines():
            stem = Path(line.split(":", 1)[0].strip()).name
            for suffix in (".zst", ".xz", ".gz"):
                stem = stem.removesuffix(suffix)
            if stem.removesuffix(".ko").replace("-", "_") == name:
                return how
    return None


def _kernel_checks(*, sys_root: Path, modules_dir: Path) -> list[Check]:
    missing = [
        name
        for name in REQUIRED_KERNEL_MODULES
        if _module_available(name, sys_root=sys_root, modules_dir=modules_dir) is None
    ]
    checks = [
        Check(
            "kernel features",
            not missing,
            f"missing {', '.join(missing)}" if missing else "all present",
            ", ".join(REQUIRED_KERNEL_MODULES),
        )
    ]
    for feature, modules in FEATURE_KERNEL_MODULES.items():
        absent = [
            name
            for name in modules
            if _module_available(name, sys_root=sys_root, modules_dir=modules_dir) is None
        ]
        checks.append(
            Check(
                f"{feature} (feature)",
                not absent,
                f"missing {', '.join(absent)}" if absent else "present",
                ", ".join(modules),
                hard=False,
            )
        )
    return checks


def _host_link(ipr: IPRoute, host_ip: str):
    """The interface that holds ``host_ip``, the one other servers reach this one on."""
    family = 2 if ipaddress.ip_address(host_ip).version == 4 else 10
    for address in ipr.get_addr(family=family):
        if address.get_attr("IFA_ADDRESS") == host_ip:
            return ipr.get_links(address["index"])[0]
    return None


def _node_interface_check(ipr: IPRoute, host_ip: str, link_mtu: int) -> Check:
    needed = host_path_mtu_for(link_mtu, host_ip)
    link = _host_link(ipr, host_ip)
    if link is None:
        return Check("node network MTU", False, f"no interface holds {host_ip}", f">= {needed}")
    name, mtu = link.get_attr("IFLA_IFNAME"), link.get_attr("IFLA_MTU")
    return Check("node network MTU", mtu >= needed, f"{mtu} on {name}", f">= {needed}")


def queue_buffer_bytes_needed(*, largest_buffer: int, page_size: int) -> int:
    """The memory the queue estimate must count per buffer on a server: the largest buffer
    rounded up to whole pages, one more page, and the buffer overhead."""
    pages = -(-largest_buffer // page_size)
    return pages * page_size + page_size + BUFFER_OVERHEAD_BYTES


def _queue_buffer_check(ipr: IPRoute, host_ip: str, bytes_per_buffer: int, page_size: int) -> Check:
    """Whether the installation's per-buffer queue memory estimate covers this server.

    The estimate assumes buffers no larger than the kernel's default GSO and GRO limit and the
    page size it was set for. A server whose page size, or whose node interface's GSO or GRO
    limit (raised for BIG TCP), needs more fails, with the value it needs. Not read: the IPv4
    BIG TCP limits (gso_ipv4_max_size, gro_ipv4_max_size), which pyroute2 0.9.6 does not decode;
    the installation documentation states that the estimate assumes them at their default."""
    name = "queue memory estimate"
    link = _host_link(ipr, host_ip)
    if link is None:
        return Check(name, False, f"no interface holds {host_ip}", "a node interface")
    limits = [
        value
        for value in (link.get_attr("IFLA_GSO_MAX_SIZE"), link.get_attr("IFLA_GRO_MAX_SIZE"))
        if value is not None
    ]
    largest = max([DEFAULT_GSO_GRO_MAX_BYTES, *limits])
    needed = queue_buffer_bytes_needed(largest_buffer=largest, page_size=page_size)
    return Check(
        name,
        bytes_per_buffer >= needed,
        f"{bytes_per_buffer} bytes per buffer; page size {page_size}, buffers up to {largest} "
        f"bytes on {link.get_attr('IFLA_IFNAME')}",
        f"network.queueMemory.bytesPerBuffer >= {needed}",
    )


def _vxlan_port_check(ipr: IPRoute, port: int) -> Check:
    others = []
    for link in ipr.get_links():
        info = link.get_attr("IFLA_LINKINFO")
        if info is None or info.get_attr("IFLA_INFO_KIND") != "vxlan":
            continue
        if link.get_attr("IFLA_GROUP") == MANAGED_HOST_DEVICE_GROUP:
            continue
        data = info.get_attr("IFLA_INFO_DATA")
        if data is not None and data.get_attr("IFLA_VXLAN_PORT") == port:
            others.append(link.get_attr("IFLA_IFNAME"))
    return Check(
        "VXLAN UDP port",
        not others,
        f"used by {', '.join(others)}" if others else f"{port} free of other VXLAN devices",
        f"UDP {port} for NodalArc only",
    )


def _read_int(path: Path) -> int | None:
    try:
        return int(path.read_text().strip())
    except OSError, ValueError:
        return None


def _inotify_checks(proc_root: Path) -> list[Check]:
    checks = []
    for name, minimum in (
        ("max_user_instances", MIN_INOTIFY_INSTANCES),
        ("max_user_watches", MIN_INOTIFY_WATCHES),
    ):
        value = _read_int(proc_root / "sys" / "fs" / "inotify" / name)
        checks.append(
            Check(
                f"inotify {name}",
                value is not None and value >= minimum,
                "unreadable" if value is None else str(value),
                f">= {minimum}",
            )
        )
    return checks


def run_checks(
    *,
    host_ip: str,
    link_mtu: int,
    vxlan_port: int,
    queue_bytes_per_buffer: int,
    proc_root: Path = Path("/proc"),
    sys_root: Path = Path("/sys"),
    modules_root: Path = Path("/lib/modules"),
) -> list[Check]:
    """Every check of this host, in the order the requirements list names them."""
    modules_dir = modules_root / os.uname().release
    with IPRoute() as ipr:
        checks = [
            _node_interface_check(ipr, host_ip, link_mtu),
            *_kernel_checks(sys_root=sys_root, modules_dir=modules_dir),
            _vxlan_port_check(ipr, vxlan_port),
            _queue_buffer_check(ipr, host_ip, queue_bytes_per_buffer, os.sysconf("SC_PAGE_SIZE")),
        ]
    checks.extend(_inotify_checks(proc_root))
    return checks


# The purge check's dummy interface, frames and shaper rate. The rate keeps all but the first
# frames (the shaper's burst of one link MTU) queued for minutes.
_PURGE_CHECK_IFNAME = "purgecheck0"
_PURGE_CHECK_FRAMES = 64
_PURGE_CHECK_FRAME = b"\xff" * 6 + b"\x02" + b"\x00" * 5 + b"\x88\xb5" + b"\x00" * 1500
_PURGE_CHECK_RATE_MBPS = 0.001


def check_purge_counts() -> Check:
    """Whether this kernel counts a transmit purge exactly.

    A long-delay stop deletes the transmitter's leaf and reports the rise of the root qdisc's
    drop counter as untransmitted at contact end (``admission.purge_untransmitted``). That count
    relies on the kernel adding every purged packet to the root's drops. The check runs the
    production steps on a dummy interface in their production order: the stopped hook where the
    interface is created, the open, frames queued behind a very slow shaper, the stop, the purge.
    Every frame must be either sent by the shaper or counted as purged, with at least one purged
    and none dropped before.

    It changes kernel state, so it runs once when the Node Agent starts, never in the readiness
    probe. The state lives in a scratch network namespace held by one thread; the namespace and
    the interface in it end when that thread does."""
    from node_agent import admission, loss, namespace_ops

    name = "transmit purge count"
    needed = "every frame sent or counted as purged, at least one purged"
    entered, release = threading.Event(), threading.Event()
    holder: dict[str, int | OSError] = {}

    def hold() -> None:
        try:
            os.unshare(os.CLONE_NEWNET)
            holder["tid"] = threading.get_native_id()
        except OSError as exc:
            holder["error"] = exc
        entered.set()
        release.wait()

    thread = threading.Thread(target=hold, name="purge-count-check")
    thread.start()
    entered.wait()
    try:
        if "error" in holder:
            return Check(name, False, f"no scratch network namespace: {holder['error']}", needed)
        tid = int(holder["tid"])

        def build(ipr: IPRoute) -> None:
            ipr.link("add", ifname=_PURGE_CHECK_IFNAME, kind="dummy")
            ipr.link("set", index=ipr.link_lookup(ifname=_PURGE_CHECK_IFNAME)[0], state="up")

        def send(_ipr: IPRoute) -> None:
            with socket.socket(socket.AF_PACKET, socket.SOCK_RAW) as raw:
                raw.bind((_PURGE_CHECK_IFNAME, 0))
                for _ in range(_PURGE_CHECK_FRAMES):
                    raw.send(_PURGE_CHECK_FRAME)

        namespace_ops._in_namespace(tid, build)
        admission.install_stopped_hook(tid, _PURGE_CHECK_IFNAME)
        namespace_ops.apply_long_delay_transmit_shaping(
            tid, _PURGE_CHECK_IFNAME, _PURGE_CHECK_RATE_MBPS
        )
        admission.freeze_admission(tid, _PURGE_CHECK_IFNAME)
        admission.open_admission(tid, _PURGE_CHECK_IFNAME)
        namespace_ops._in_namespace(tid, send)
        admission.stop_admission(tid, _PURGE_CHECK_IFNAME)
        purge = admission.purge_untransmitted(tid, _PURGE_CHECK_IFNAME)
        counts = namespace_ops._in_namespace(
            tid, lambda ipr: loss.root_counts(ipr, _PURGE_CHECK_IFNAME)
        )
        exact = (
            purge.dropped_before == 0
            and purge.purged > 0
            and counts.sent + purge.purged == _PURGE_CHECK_FRAMES
        )
        return Check(
            name,
            exact,
            f"{_PURGE_CHECK_FRAMES} frames: {counts.sent} sent, {purge.purged} counted as "
            f"purged, {purge.dropped_before} dropped before the purge",
            needed,
        )
    finally:
        release.set()
        thread.join()


def qualified(checks: list[Check]) -> bool:
    return all(check.passed for check in checks if check.hard)


def _main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m node_agent.qualification",
        description="Check whether this host can run NodalArc session pods. Changes nothing.",
    )
    parser.add_argument(
        "--host-ip", required=True, help="the address other nodes reach this node on"
    )
    parser.add_argument(
        "--link-mtu",
        type=int,
        required=True,
        help="the emulated link MTU (chart value network.linkMtu)",
    )
    parser.add_argument(
        "--vxlan-port",
        type=int,
        required=True,
        help="NodalArc's VXLAN port (chart value network.vxlanPort)",
    )
    parser.add_argument(
        "--queue-bytes-per-buffer",
        type=int,
        required=True,
        help="the queue memory estimate per buffer (chart value network.queueMemory.bytesPerBuffer)",
    )
    parser.add_argument("--json", action="store_true", help="print the checks as JSON")
    args = parser.parse_args(argv)
    checks = run_checks(
        host_ip=args.host_ip,
        link_mtu=args.link_mtu,
        vxlan_port=args.vxlan_port,
        queue_bytes_per_buffer=args.queue_bytes_per_buffer,
    )
    if args.json:
        sys.stdout.write(json.dumps([asdict(check) for check in checks]) + "\n")
    else:
        for check in checks:
            sys.stdout.write(check.line() + "\n")
    return 0 if qualified(checks) else 1


if __name__ == "__main__":
    sys.exit(_main())
