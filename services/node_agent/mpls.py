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

from node_agent import kernel_verifier, ops_events
from node_agent.kernel_constants import MPLS_INPUT_ENABLED, mpls_input_sysctl
from node_agent.kernel_verifier import KernelStateConflict, Proof
from node_agent.namespace_ops import _write_sysctl_in_netns

log = logging.getLogger(__name__)

MODULE_ROUTING = "mpls_router"
MODULE_ENCAPSULATION = "mpls_iptunnel"
MPLS_KERNEL_MODULES = (MODULE_ROUTING, MODULE_ENCAPSULATION)


def running_in_k8s() -> bool:
    return bool(os.environ.get("KUBERNETES_SERVICE_HOST") or os.environ.get("NODE_NAME"))


class MplsInputError(RuntimeError):
    """A created interface's MPLS input could not be written, or did not read back as written."""

    def __init__(
        self, subject: str, ifname: str, detail: str, evidence: tuple[str, ...] = ()
    ) -> None:
        rendered = f" [{', '.join(evidence)}]" if evidence else ""
        super().__init__(f"{subject}: MPLS input on {ifname}: {detail}{rendered}")
        self.subject = subject
        self.ifname = ifname
        self.detail = detail
        self.evidence = evidence


def enable_mpls_input(pid: int, ifname: str, *, subject: str) -> None:
    """Write the interface's MPLS input switch; a failed write is an error, never a log line."""
    err = _write_sysctl_in_netns(pid, mpls_input_sysctl(ifname), MPLS_INPUT_ENABLED)
    if err:
        raise MplsInputError(subject, ifname, f"write failed: {err}")


def configure_mpls_input(pid: int, ifname: str, *, created: bool, subject: str) -> Proof:
    """The creating operation's MPLS input step for one interface that requires it.

    ``created`` is the creator's own decision. A created or recreated
    interface is written and then read back; a value that does not read
    back as written is ``MplsInputError``. A reused interface is only read;
    a mismatch is a ``KernelStateConflict`` under the one policy for kernel
    state found under a link's names: refused, never repaired. The returned
    proof is the read-back and travels with the operation's result.
    """
    if created:
        enable_mpls_input(pid, ifname, subject=subject)
        proof = kernel_verifier.verify_mpls_input(pid, ifname)
        if not proof.verified:
            raise MplsInputError(
                subject, ifname, f"written but not read back: {proof.summary}", proof.evidence
            )
        return proof
    proof = kernel_verifier.verify_mpls_input(pid, ifname)
    if not proof.verified:
        raise KernelStateConflict(subject, (f"{ifname}@pod",), (proof.summary,), (proof.evidence,))
    return proof


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
