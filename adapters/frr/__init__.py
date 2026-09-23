# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""The FRR reference adapter.

FRR is the reference implementation of the workload adapter contract. It
renders one routed node's resolved facts into the files the FRR image loads
from its configuration mount: ``frr.conf`` (the integrated configuration),
``daemons`` (exactly the daemons its stack selected) and ``_config_version``
(the readiness proof). The configuration fragments behind ``frr.conf`` are
rendering inputs and are never delivered. The image's ENTRYPOINT reads the
mount on its own; this adapter sets no environment and appends no arguments.

Declaration reads (``support``) import nothing beyond the core contract.
The template engine is imported on the first render, so services that only
resolve sessions never load it.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import TYPE_CHECKING

from nodalarc.workloads.adapter import AdapterNodeConfig, SessionContext

from adapters.frr.stack import ResolvedStack, resolve_domain_stack, validate_sid_indices
from adapters.frr.support import FRR_SUPPORT
from adapters.frr.template_vars import build_template_vars_from_resolved

if TYPE_CHECKING:
    from typing import Any

    from jinja2 import Environment
    from nodalarc.models.resolved_session import ResolvedNode

# The adapter's name: the value a profile's `adapter:` field carries, and the
# key the explicit registry uses.
FRR_ADAPTER_NAME = "frr"

_TEMPLATE_DIR = Path(__file__).resolve().parent / "templates"

# Every daemon the FRR image's watchfrr knows, in the order of the image's
# daemons file. The rendered daemons file lists each one yes or no.
FRR_DAEMONS: tuple[str, ...] = (
    "mgmtd",
    "zebra",
    "bgpd",
    "ospfd",
    "ospf6d",
    "ripd",
    "ripngd",
    "isisd",
    "pimd",
    "ldpd",
    "nhrpd",
    "eigrpd",
    "babeld",
    "sharpd",
    "pbrd",
    "bfdd",
    "fabricd",
    "vrrpd",
    "pathd",
    "staticd",
)


def _daemons_file(selected: tuple[str, ...]) -> str:
    """The FRR daemons file enabling exactly the selected daemons."""
    if not selected:
        raise ValueError("an FRR stack must select at least one daemon")
    unknown = sorted(set(selected) - set(FRR_DAEMONS))
    if unknown:
        raise ValueError(f"FRR stack selected unknown daemon(s) {unknown}")
    enabled = set(selected)
    return "".join(f"{daemon}={'yes' if daemon in enabled else 'no'}\n" for daemon in FRR_DAEMONS)


class FrrAdapter:
    """Render one routed node's FRR configuration from resolved truth."""

    name = FRR_ADAPTER_NAME
    support = FRR_SUPPORT

    def __init__(self) -> None:
        self._env: Environment | None = None

    def _environment(self) -> Environment:
        if self._env is None:
            from jinja2 import Environment, FileSystemLoader, StrictUndefined

            # nosec B701: FRR router configuration templates, not HTML;
            # autoescape would corrupt configuration syntax.
            self._env = Environment(
                loader=FileSystemLoader(str(_TEMPLATE_DIR)),
                keep_trailing_newline=True,
                undefined=StrictUndefined,
            )
        return self._env

    def render_node(
        self,
        resolved_node: ResolvedNode,
        session_context: SessionContext,
    ) -> AdapterNodeConfig:
        resolved = session_context.resolved
        node_id = resolved_node.node_id
        domain = resolved.routing_domain_for(node_id)
        stack = resolve_domain_stack(domain)
        sid_by_node = resolved.sid_index_by_node_id()
        if stack.segment_routing:
            validate_sid_indices(
                stack,
                {
                    member_id: sid_by_node[member_id]
                    for member_id in domain.node_ids
                    if member_id in sid_by_node
                },
            )
        template_vars = build_template_vars_from_resolved(
            resolved,
            resolved_node,
            domain=domain,
            stack=stack,
            node_sid_index=sid_by_node.get(node_id) if stack.segment_routing else None,
        )
        frr_conf = self._frr_conf(stack, template_vars)
        return AdapterNodeConfig(
            files={
                "frr.conf": frr_conf.encode(),
                "daemons": _daemons_file(stack.daemons).encode(),
                # The entrypoint writes this after loading, and the readiness
                # probe diffs it to prove the intended configuration is live.
                "_config_version": hashlib.sha256(frr_conf.encode()).hexdigest()[:16].encode(),
            }
        )

    def _frr_conf(self, stack: ResolvedStack, template_vars: dict[str, Any]) -> str:
        """Assemble the integrated configuration from the stack's fragments."""
        env = self._environment()
        parts: list[str] = []
        for fragment in stack.fragments:
            parts.append(f"! === {fragment} ===")
            parts.append(env.get_template(f"{fragment}.conf.j2").render(**template_vars))
        # FRR reads a blank line inside an interface or router block as an
        # implicit exit, and template conditionals emit blank lines.
        lines = [line for line in "\n".join(parts).splitlines() if line.strip()]
        return "\n".join(lines) + "\n"
