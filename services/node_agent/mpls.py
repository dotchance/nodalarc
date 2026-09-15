# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""MPLS kernel capability for Node Agent wiring.

Linux defines MPLS routing (``mpls_router``) and IP-over-MPLS encapsulation
(``mpls_iptunnel``) separately, so each capability is probed on its own
after the modules are asked for. A failed ``modprobe`` decides nothing by
itself: the capability may be built in or already loaded. The verdict is the
kernel's current state, read on every MPLS wiring attempt; nothing is
cached across attempts.
"""

from __future__ import annotations

import logging
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

from node_agent import ops_events

log = logging.getLogger(__name__)

MODULE_ROUTING = "mpls_router"
MODULE_ENCAPSULATION = "mpls_iptunnel"
MPLS_KERNEL_MODULES = (MODULE_ROUTING, MODULE_ENCAPSULATION)


def running_in_k8s() -> bool:
    return bool(os.environ.get("KUBERNETES_SERVICE_HOST") or os.environ.get("NODE_NAME"))


@dataclass(frozen=True)
class ModuleLoad:
    """One ``modprobe`` request: whether it ran and what it answered."""

    name: str
    attempted: bool
    returncode: int | None
    stderr: str

    @property
    def failed(self) -> bool:
        return self.attempted and self.returncode != 0

    def describe(self) -> str:
        if not self.attempted:
            return f"{self.name}: modprobe not attempted outside Kubernetes"
        if self.returncode == 0:
            return f"{self.name}: modprobe ok"
        return f"{self.name}: modprobe rc={self.returncode} stderr={self.stderr.strip()!r}"


@dataclass(frozen=True)
class CapabilityProbe:
    """One kernel capability read from the filesystem: what was read and what it showed."""

    name: str
    probe: str
    present: bool
    detail: str = ""

    def describe(self) -> str:
        state = "present" if self.present else "absent"
        return f"{self.name} {state} ({self.probe}{'; ' + self.detail if self.detail else ''})"


@dataclass(frozen=True)
class MplsSupport:
    """The kernel's MPLS capabilities at the moment of one wiring attempt."""

    routing: CapabilityProbe
    encapsulation: CapabilityProbe
    modules: tuple[ModuleLoad, ...]

    @property
    def available(self) -> bool:
        return self.routing.present and self.encapsulation.present

    def diagnostic(self) -> str:
        parts = [self.routing.describe(), self.encapsulation.describe()]
        parts.extend(module.describe() for module in self.modules)
        return "; ".join(parts)


def module_is_builtin(name: str, inventory: Path) -> tuple[bool, str]:
    """Whether ``modules.builtin`` for the running kernel lists ``name``.

    Returns the verdict and a detail: which inventory was read, or why it
    could not be. An unreadable inventory is not evidence of absence and its
    error travels with the verdict.
    """
    try:
        lines = inventory.read_text().splitlines()
    except OSError as exc:
        return False, f"{inventory}: {exc.strerror or exc}"
    for line in lines:
        stem = Path(line.strip()).name
        for suffix in (".ko.zst", ".ko.xz", ".ko.gz", ".ko"):
            if stem.endswith(suffix):
                stem = stem[: -len(suffix)]
                break
        if stem == name:
            return True, f"{inventory}: listed"
    return False, f"{inventory}: not listed"


def probe_routing(*, proc_root: Path = Path("/proc")) -> CapabilityProbe:
    """MPLS routing is present when the kernel exposes the ``net.mpls`` sysctl tree.

    The tree exists in every network namespace once ``mpls_router`` is loaded
    or built in; the Node Agent runs on the host network, so its own tree is
    the host's.
    """
    tree = proc_root / "sys" / "net" / "mpls"
    return CapabilityProbe("mpls routing", str(tree), tree.is_dir())


def probe_encapsulation(
    *,
    sys_root: Path = Path("/sys"),
    modules_root: Path = Path("/lib/modules"),
    release: str | None = None,
) -> CapabilityProbe:
    """IP-over-MPLS encapsulation is present when ``mpls_iptunnel`` is loaded or built in."""
    loaded_path = sys_root / "module" / MODULE_ENCAPSULATION
    loaded = loaded_path.is_dir()
    inventory = modules_root / (release or os.uname().release) / "modules.builtin"
    builtin, builtin_detail = module_is_builtin(MODULE_ENCAPSULATION, inventory)
    detail = f"loaded={'yes' if loaded else 'no'} per {loaded_path}; builtin={'yes' if builtin else 'no'} per {builtin_detail}"
    return CapabilityProbe("mpls encapsulation", MODULE_ENCAPSULATION, loaded or builtin, detail)


def _load_module(name: str) -> ModuleLoad:
    if not running_in_k8s():
        return ModuleLoad(name, attempted=False, returncode=None, stderr="")
    result = subprocess.run(["modprobe", name], text=True, capture_output=True, check=False)
    return ModuleLoad(name, attempted=True, returncode=result.returncode, stderr=result.stderr)


def ensure_mpls_kernel_support(
    *,
    proc_root: Path = Path("/proc"),
    sys_root: Path = Path("/sys"),
    modules_root: Path = Path("/lib/modules"),
    release: str | None = None,
) -> MplsSupport:
    """Ask the kernel for MPLS support, then read what it has.

    Called once per host on every wiring attempt that needs MPLS. ``modprobe``
    is idempotent, so an already-loaded module costs a lookup and no
    reinsertion. Each capability is judged by its own probe; a failed
    ``modprobe`` whose capability is present is reported as a diagnostic and
    nothing more, and a failed ``modprobe`` whose capability is absent is
    published as the existing kernel-module event. The caller refuses the
    MPLS nodes when ``available`` is False and carries ``diagnostic()`` with
    the refusal.
    """
    modules = tuple(_load_module(name) for name in MPLS_KERNEL_MODULES)
    routing = probe_routing(proc_root=proc_root)
    encapsulation = probe_encapsulation(
        sys_root=sys_root, modules_root=modules_root, release=release
    )
    capability_of = {MODULE_ROUTING: routing, MODULE_ENCAPSULATION: encapsulation}
    for module in modules:
        if not module.failed:
            continue
        capability = capability_of[module.name]
        if capability.present:
            log.info(
                "Kernel module %s did not load but its capability is present; treating as built in: %s",
                module.name,
                module.describe(),
            )
            continue
        message = (
            f"Kernel module {module.name!r} could not be loaded and {capability.name} is absent; "
            "MPLS wiring on this host is refused until the kernel exposes it."
        )
        ops_events.publish(
            level="warning",
            code="STARTUP_KERNEL_MODULE_UNAVAILABLE",
            message=message,
            session_id="",
            details={
                "module": module.name,
                "stderr": module.stderr.strip(),
                "returncode": module.returncode,
                "capability": capability.describe(),
            },
        )
        log.warning("%s stderr=%s", message, module.stderr.strip())
    return MplsSupport(routing=routing, encapsulation=encapsulation, modules=modules)
