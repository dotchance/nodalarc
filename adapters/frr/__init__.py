# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""The FRR reference adapter.

FRR is the reference implementation of the workload adapter contract: it renders
one routed node's resolved facts into the exact ``frr.conf``/``daemons`` files
the FRR image loads from its plan-artifact mount. The image's ENTRYPOINT reads
``/etc/frr-config`` on its own; this adapter only produces the bytes. It sets no
environment and appends no arguments — FRR needs neither.

This is the single FRR translator. Every FRR node, whether selected explicitly
or through the built-in default, renders through here.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import TYPE_CHECKING

from jinja2 import Environment, FileSystemLoader
from nodalarc.stack_resolver import ResolvedStack, resolve_domain_stack, validate_sid_indices
from nodalarc.template_vars import build_template_vars_from_resolved
from nodalarc.workloads.adapter import AdapterNodeConfig, SessionContext

if TYPE_CHECKING:
    from nodalarc.models.resolved_session import (
        ResolvedNode,
        ResolvedRoutingDomain,
        ResolvedSession,
    )

# The adapter's name: the value a profile's `adapter:` field carries, and the
# key the explicit registry and the runtime-support renderability declaration
# use.
FRR_ADAPTER_NAME = "frr"

# FRR templates live beside the platform's other runtime templates, resolved
# relative to the process working directory (the operator's /app, or the repo
# root under tests) — the same path the built-in renderer used.
_TEMPLATE_DIR = "configs/templates/frr"

# All known FRR daemons — used to generate the daemons file. Every daemon is
# listed yes/no so the file is complete regardless of which the stack enables.
_ALL_FRR_DAEMONS = [
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
]

# frr.conf is assembled from these daemon configs in this order; FRR's parser
# treats blank lines inside blocks as implicit "exit", so blanks are stripped.
_FRR_CONF_ORDER = ("zebra.conf", "isisd.conf", "ospfd.conf", "pathd.conf", "staticd.conf")


def _routing_domain_for_node(resolved: ResolvedSession, node_id: str) -> ResolvedRoutingDomain:
    """The one routing domain a node belongs to, or a loud failure."""
    domains = [domain for domain in resolved.routing_domains if node_id in domain.node_ids]
    if len(domains) != 1:
        raise ValueError(
            f"node {node_id!r} must resolve to exactly one routing domain for FRR rendering; "
            f"got {[domain.domain_id for domain in domains]}"
        )
    return domains[0]


class FrrAdapter:
    """Render one routed node's FRR configuration from resolved truth."""

    name = FRR_ADAPTER_NAME

    def __init__(self) -> None:
        self._env: Environment | None = None

    def _environment(self) -> Environment:
        if self._env is None:
            template_dir = str(Path(_TEMPLATE_DIR).resolve())
            # nosec B701 — FRR router config templates, not HTML; autoescape
            # would corrupt config syntax.
            self._env = Environment(
                loader=FileSystemLoader(template_dir), keep_trailing_newline=True
            )
        return self._env

    def render_node(
        self,
        resolved_node: ResolvedNode,
        session_context: SessionContext,
    ) -> AdapterNodeConfig:
        resolved = session_context.resolved
        node_id = resolved_node.node_id
        domain = _routing_domain_for_node(resolved, node_id)
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
        node_sid_index = sid_by_node.get(node_id) if stack.segment_routing else None
        template_vars = build_template_vars_from_resolved(
            resolved,
            node_id,
            stack_variables=stack.template_variables,
            node_sid_index=node_sid_index,
        )
        configs = self._render(stack, template_vars)
        return AdapterNodeConfig(files={name: text.encode() for name, text in configs.items()})

    def _render(self, stack: ResolvedStack, template_vars: dict) -> dict[str, str]:
        env = self._environment()
        configs: dict[str, str] = {}
        for template_file in stack.template_files:
            rendered = env.get_template(template_file.src).render(**template_vars)
            configs[Path(template_file.dst).name] = rendered
        if stack.daemons:
            # mgmtd is always required in FRR 10.x — it manages config loading.
            enabled = set(stack.daemons) | {"mgmtd"}
            configs["daemons"] = (
                "\n".join(f"{d}={'yes' if d in enabled else 'no'}" for d in _ALL_FRR_DAEMONS) + "\n"
            )
        frr_conf_parts: list[str] = []
        for name_key in _FRR_CONF_ORDER:
            if name_key in configs:
                frr_conf_parts.append(f"! === {name_key} ===")
                frr_conf_parts.append(configs[name_key])
        if frr_conf_parts:
            raw = "\n".join(frr_conf_parts)
            # Blank lines inside interface/router blocks are read as implicit
            # "exit"; Jinja {% if %} blocks emit blanks that break parsing.
            cleaned_lines = [line for line in raw.splitlines() if line.strip() != ""]
            configs["frr.conf"] = "\n".join(cleaned_lines) + "\n"
        if "frr.conf" in configs:
            # Config version hash — the entrypoint writes it after loading, and
            # the readiness probe diffs it to prove the intended config is live.
            configs["_config_version"] = hashlib.sha256(configs["frr.conf"].encode()).hexdigest()[
                :16
            ]
        return configs
