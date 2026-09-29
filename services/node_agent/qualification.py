# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""Node qualification: whether this host can run NodalArc session pods.

Every check reads the host from the Node Agent's own process: the kernel's
module tables, /proc, and netlink. The same checks run in two ways:

- ``python -m node_agent.qualification`` prints each check and exits 0 only
  when every hard requirement holds. It changes nothing, so an installer can
  run it on a node before NodalArc is installed (the node check).
- The running Node Agent repeats them and publishes the verdict to its
  readiness probe (``READINESS_FILE``), so the DaemonSet shows desired
  against ready nodes, the probe's failure message names the failed checks,
  and the Operator places session pods only on nodes whose Node Agent is
  ready.

Checks between hosts (the path MTU) depend on both ends and are not part of
a node's qualification: each session proves its own host paths before it is
wired.
"""

from __future__ import annotations

import argparse
import ipaddress
import json
import logging
import os
import sys
import threading
from dataclasses import asdict, dataclass
from pathlib import Path

from nodalarc.runtime_naming import MANAGED_HOST_DEVICE_GROUP
from nodalarc.vxlan import host_path_mtu_for
from pyroute2 import IPRoute

log = logging.getLogger(__name__)

# Kernel modules every session needs (links, shaping, the pod firewall).
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
    "nf_tables",
    "nf_conntrack",
)
FEATURE_KERNEL_MODULES = {"MPLS": ("mpls_router", "mpls_iptunnel")}
MIN_INOTIFY_INSTANCES = 8192
MIN_INOTIFY_WATCHES = 65536
READINESS_FILE = Path("/tmp/nodalarc-qualification")
RECHECK_INTERVAL_S = 30.0


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


def _node_interface_check(ipr: IPRoute, host_ip: str, link_mtu: int) -> Check:
    needed = host_path_mtu_for(link_mtu, host_ip)
    family = 2 if ipaddress.ip_address(host_ip).version == 4 else 10
    for address in ipr.get_addr(family=family):
        if address.get_attr("IFA_ADDRESS") == host_ip:
            link = ipr.get_links(address["index"])[0]
            name, mtu = link.get_attr("IFLA_IFNAME"), link.get_attr("IFLA_MTU")
            return Check("node network MTU", mtu >= needed, f"{mtu} on {name}", f">= {needed}")
    return Check("node network MTU", False, f"no interface holds {host_ip}", f">= {needed}")


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
        ]
    checks.extend(_inotify_checks(proc_root))
    return checks


def qualified(checks: list[Check]) -> bool:
    return all(check.passed for check in checks if check.hard)


def publish(checks: list[Check], path: Path = READINESS_FILE) -> None:
    """Write the verdict the readiness probe reads: the failed checks, or nothing."""
    failed = [check.line() for check in checks if check.hard and not check.passed]
    staging = path.with_suffix(".staging")
    staging.write_text("\n".join(failed) + ("\n" if failed else ""))
    os.replace(staging, path)


def qualify_forever(*, host_ip: str, link_mtu: int, vxlan_port: int, stop: threading.Event) -> None:
    """Re-run the checks every ``RECHECK_INTERVAL_S`` and publish each verdict.

    A change of verdict is logged with every failed check named.
    """
    previous: list[str] | None = None
    while not stop.is_set():
        try:
            checks = run_checks(host_ip=host_ip, link_mtu=link_mtu, vxlan_port=vxlan_port)
        except Exception as exc:
            checks = [Check("qualification", False, f"could not run: {exc}", "a readable host")]
        publish(checks)
        failed = [check.line() for check in checks if check.hard and not check.passed]
        if failed != previous:
            if failed:
                log.error("Node qualification failed: %s", "; ".join(failed))
            else:
                log.info("Node qualified: %s", "; ".join(check.line() for check in checks))
            previous = failed
        stop.wait(RECHECK_INTERVAL_S)


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
    parser.add_argument("--json", action="store_true", help="print the checks as JSON")
    args = parser.parse_args(argv)
    checks = run_checks(host_ip=args.host_ip, link_mtu=args.link_mtu, vxlan_port=args.vxlan_port)
    if args.json:
        sys.stdout.write(json.dumps([asdict(check) for check in checks]) + "\n")
    else:
        for check in checks:
            sys.stdout.write(check.line() + "\n")
    return 0 if qualified(checks) else 1


if __name__ == "__main__":
    sys.exit(_main())
