# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""MPLS kernel capability for Node Agent wiring.

Linux defines MPLS routing (``mpls_router``) and IP-over-MPLS encapsulation
(``mpls_iptunnel``) separately, so each capability is probed on its own.
The Node Agent asks for the modules only when the installation allows it
(``node_agent_loads_kernel_modules``); otherwise the host's own
configuration loads them. The Node Agent loads a module in its own process:
it reads the module and its dependencies from the host's module tree,
decompresses each file with the standard library and hands the image to
``init_module(2)``. A failed load decides nothing by itself: the capability
may be built in or already loaded. The verdict is the kernel's current state,
read on every MPLS wiring attempt; nothing is cached across attempts.
"""

from __future__ import annotations

import ctypes
import errno
import gzip
import logging
import lzma
import os
from collections.abc import Callable
from compression import zstd
from dataclasses import dataclass
from pathlib import Path

from nodalarc.platform_config import get_platform_config

from node_agent import kernel_verifier, ops_events
from node_agent.kernel_constants import MPLS_INPUT_ENABLED, mpls_input_sysctl
from node_agent.kernel_verifier import KernelStateConflict, Proof
from node_agent.namespace_ops import _libc, _write_sysctl_in_netns

log = logging.getLogger(__name__)

MODULE_ROUTING = "mpls_router"
MODULE_ENCAPSULATION = "mpls_iptunnel"
MPLS_KERNEL_MODULES = (MODULE_ROUTING, MODULE_ENCAPSULATION)


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
    """One module load request: whether it ran and the error it met, if any."""

    name: str
    attempted: bool
    error: str = ""

    @property
    def failed(self) -> bool:
        return self.attempted and bool(self.error)

    def describe(self) -> str:
        if not self.attempted:
            return (
                f"{self.name}: not loaded by the Node Agent (nodeAgent.loadKernelModules is off; "
                "the host's own configuration loads it)"
            )
        if not self.error:
            return f"{self.name}: load ok"
        return f"{self.name}: load failed: {self.error}"


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


# init_module(2) by machine: the kernel takes the module image from memory.
_INIT_MODULE_SYSCALL = {"x86_64": 175, "aarch64": 105}
_DECOMPRESS: dict[str, Callable[[bytes], bytes]] = {
    ".zst": zstd.decompress,
    ".xz": lzma.decompress,
    ".gz": gzip.decompress,
}


def _module_name(path: str) -> str:
    """The kernel's name for a module file: its stem, with dashes as underscores."""
    stem = Path(path).name
    for suffix in _DECOMPRESS:
        stem = stem.removesuffix(suffix)
    return stem.removesuffix(".ko").replace("-", "_")


def module_load_order(name: str, modules_dir: Path) -> list[Path]:
    """The files that load ``name``: its dependencies first, then the module.

    ``modules.dep`` lists a module's dependencies with each one depending
    only on those after it, so they load from the end of the list.
    """
    dep_file = modules_dir / "modules.dep"
    for line in dep_file.read_text().splitlines():
        path, sep, deps = line.partition(":")
        if sep and _module_name(path) == name:
            return [modules_dir / dep for dep in reversed(deps.split())] + [modules_dir / path]
    raise LookupError(f"{name} is not listed in {dep_file}")


def _init_module(image: bytes) -> None:
    machine = os.uname().machine
    number = _INIT_MODULE_SYSCALL.get(machine)
    if number is None:
        raise OSError(errno.ENOSYS, f"no init_module system call number known for {machine}")
    buffer = ctypes.create_string_buffer(image, len(image))
    if _libc.syscall(ctypes.c_long(number), buffer, ctypes.c_ulong(len(image)), b"") != 0:
        code = ctypes.get_errno()
        raise OSError(code, os.strerror(code))


def _load_file(path: Path, sys_root: Path) -> None:
    """Load one module file unless the kernel already has that module."""
    if (sys_root / "module" / _module_name(path.name)).is_dir():
        return
    data = path.read_bytes()
    decompress = _DECOMPRESS.get(path.suffix)
    try:
        _init_module(decompress(data) if decompress else data)
    except OSError as exc:
        if exc.errno != errno.EEXIST:  # EEXIST: loaded meanwhile
            raise


def _load_module(name: str, *, sys_root: Path, modules_dir: Path) -> ModuleLoad:
    if not get_platform_config().node_agent_loads_kernel_modules:
        return ModuleLoad(name, attempted=False)
    try:
        for path in module_load_order(name, modules_dir):
            _load_file(path, sys_root)
    except (OSError, LookupError, zstd.ZstdError, lzma.LZMAError, gzip.BadGzipFile) as exc:
        return ModuleLoad(name, attempted=True, error=f"{type(exc).__name__}: {exc}")
    return ModuleLoad(name, attempted=True)


def ensure_mpls_kernel_support(
    *,
    proc_root: Path = Path("/proc"),
    sys_root: Path = Path("/sys"),
    modules_root: Path = Path("/lib/modules"),
    release: str | None = None,
) -> MplsSupport:
    """Ask the kernel for MPLS support when allowed to, then read what it has.

    Called once per host on every wiring attempt that needs MPLS. The modules
    are asked for only when the installation lets the Node Agent load kernel
    modules; a module the kernel already has is not loaded again. Each
    capability is judged by its own probe; a failed load whose capability is
    present is reported as a diagnostic and nothing more, and a failed load
    whose capability is absent is published as the existing kernel-module
    event. The caller refuses the MPLS nodes when ``available`` is False and
    carries ``diagnostic()`` with the refusal.
    """
    modules_dir = modules_root / (release or os.uname().release)
    modules = tuple(
        _load_module(name, sys_root=sys_root, modules_dir=modules_dir)
        for name in MPLS_KERNEL_MODULES
    )
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
                "error": module.error,
                "capability": capability.describe(),
            },
        )
        log.warning("%s error=%s", message, module.error)
    return MplsSupport(routing=routing, encapsulation=encapsulation, modules=modules)
