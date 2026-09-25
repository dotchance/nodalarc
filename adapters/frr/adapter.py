# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""The FRR reference adapter's renderer.

FRR is the reference implementation of the workload adapter contract. It
renders one routed node's resolved facts into the files the FRR image loads
from its configuration mount: ``frr.conf`` (the integrated configuration),
``daemons`` (exactly the daemons its stack selects for the address families
the node carries). The configuration fragments behind ``frr.conf`` are rendering inputs and are never
delivered. The image's ENTRYPOINT reads the mount on its own; this adapter sets
no environment and appends no arguments.

Only the registry's renderer lookup imports this module; session resolution
reads the declaration in ``adapters.frr.support``. The template engine is
imported on the first render.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from nodalarc.workloads.adapter import AdapterNodeConfig, AdapterRenderRefusal, SessionContext

from adapters.frr.stack import resolve_router_stack, validate_sid_indices
from adapters.frr.support import FRR_ADAPTER_NAME, FRR_SUPPORT
from adapters.frr.template_vars import build_template_vars_from_resolved

if TYPE_CHECKING:
    from typing import Any

    from jinja2 import Environment
    from nodalarc.models.resolved_session import ResolvedNode

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
        raise AdapterRenderRefusal("an FRR stack must select at least one daemon")
    unknown = sorted(set(selected) - set(FRR_DAEMONS))
    if unknown:
        raise AdapterRenderRefusal(f"FRR stack selected unknown daemon(s) {unknown}")
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
        domains = resolved.routing_domains_for(node_id)
        if not domains:
            raise AdapterRenderRefusal(
                f"node {node_id!r} participates in no routing domain; the FRR adapter renders "
                "routing participation"
            )
        stack = resolve_router_stack(domains, resolved_node.address_families)
        sid_by_domain = resolved.sid_index_by_domain()
        validate_sid_indices(domains, sid_by_domain)
        template_vars = build_template_vars_from_resolved(
            resolved, resolved_node, domains=domains, sid_by_domain=sid_by_domain
        )
        frr_conf = self._frr_conf(stack.fragments, template_vars)
        return AdapterNodeConfig(
            files={
                "frr.conf": frr_conf.encode(),
                "daemons": _daemons_file(stack.daemons).encode(),
            }
        )

    def _frr_conf(self, fragments: tuple[str, ...], template_vars: dict[str, Any]) -> str:
        """Assemble the integrated configuration from the member's fragments."""
        env = self._environment()
        parts: list[str] = []
        for fragment in fragments:
            parts.append(f"! === {fragment} ===")
            parts.append(env.get_template(f"{fragment}.conf.j2").render(**template_vars))
        # FRR reads a blank line inside an interface or router block as an
        # implicit exit, and template conditionals emit blank lines.
        lines = [line for line in "\n".join(parts).splitlines() if line.strip()]
        return "\n".join(lines) + "\n"
