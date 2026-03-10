"""Pure-function translation of ForwardingTable entries to FRR vtysh commands.

No I/O, no subprocess. All functions receive lookup dicts for nexthop resolution.
"""

from __future__ import annotations

import logging

from nodalpath.models.almanac import ForwardingTable, LabelBinding, IngressRule

log = logging.getLogger(__name__)


def lsr_binding_to_command(
    binding: LabelBinding,
    node_id: str,
    sid_to_loopback: dict[int, str],
    iface_to_peer_loopback: dict[tuple[str, str], str],
) -> str:
    """Convert a single LSR label binding to a vtysh command line.

    Swap: mpls lsp <in_label> <nexthop_ip> <out_label>
    Pop:  mpls lsp <in_label> <nexthop_ip> implicit-null
    """
    if binding.action == "swap":
        nexthop = sid_to_loopback.get(binding.out_label)
        if nexthop is None:
            log.error(
                "Unknown SID %s for swap binding in_label=%d on %s",
                binding.out_label, binding.in_label, node_id,
            )
            return ""
        return f" mpls lsp {binding.in_label} {nexthop} {binding.out_label}"
    elif binding.action == "pop":
        nexthop = iface_to_peer_loopback.get((node_id, binding.out_interface))
        if nexthop is None:
            log.error(
                "Unknown interface (%s, %s) for pop binding in_label=%d",
                node_id, binding.out_interface, binding.in_label,
            )
            return ""
        return f" mpls lsp {binding.in_label} {nexthop} implicit-null"
    else:
        log.error("Unknown action %s for binding in_label=%d", binding.action, binding.in_label)
        return ""


def lsr_binding_remove_command(in_label: int) -> str:
    """Generate a removal command for an LSR binding."""
    return f" no mpls lsp {in_label}"


def ingress_rule_to_command(
    rule: IngressRule,
    node_id: str,
    iface_to_peer_loopback: dict[tuple[str, str], str],
) -> str:
    """Convert an ingress rule to a vtysh ip route command."""
    nexthop = iface_to_peer_loopback.get((node_id, rule.out_interface))
    if nexthop is None:
        log.error(
            "Unknown interface (%s, %s) for ingress rule dst_prefix=%s",
            node_id, rule.out_interface, rule.dst_prefix,
        )
        return ""
    return f" ip route {rule.dst_prefix} {nexthop} label {rule.push_label}"


def ingress_rule_remove_command(
    rule: IngressRule,
    node_id: str,
    iface_to_peer_loopback: dict[tuple[str, str], str],
) -> str:
    """Generate a removal command for an ingress rule."""
    nexthop = iface_to_peer_loopback.get((node_id, rule.out_interface))
    if nexthop is None:
        log.error(
            "Unknown interface (%s, %s) for ingress rule removal dst_prefix=%s",
            node_id, rule.out_interface, rule.dst_prefix,
        )
        return ""
    return f" no ip route {rule.dst_prefix} {nexthop}"


def wrap_in_configure_block(inner_lines: list[str]) -> str:
    """Wrap command lines in a configure terminal / end / write memory block.

    Returns empty string if inner_lines is empty or all blank.
    """
    filtered = [line for line in inner_lines if line]
    if not filtered:
        return ""
    body = "\n".join(filtered)
    return f"configure terminal\n{body}\nend\nwrite memory"


def forwarding_table_to_vtysh(
    table: ForwardingTable,
    sid_to_loopback: dict[int, str],
    iface_to_peer_loopback: dict[tuple[str, str], str],
) -> str:
    """Convert a full ForwardingTable to a vtysh command string."""
    lines: list[str] = []
    for binding in table.lsr_bindings:
        cmd = lsr_binding_to_command(binding, table.node_id, sid_to_loopback, iface_to_peer_loopback)
        if cmd:
            lines.append(cmd)
    for rule in table.ler_ingress_rules:
        cmd = ingress_rule_to_command(rule, table.node_id, iface_to_peer_loopback)
        if cmd:
            lines.append(cmd)
    return wrap_in_configure_block(lines)


def diff_forwarding_tables(
    current: ForwardingTable | None,
    next_table: ForwardingTable,
    sid_to_loopback: dict[int, str],
    iface_to_peer_loopback: dict[tuple[str, str], str],
) -> str:
    """Compute an incremental diff between two forwarding tables.

    If current is None, returns the full next_table as commands.
    Removals are emitted before additions in the configure block.
    Returns empty string if nothing changed.
    """
    if current is None:
        return forwarding_table_to_vtysh(next_table, sid_to_loopback, iface_to_peer_loopback)

    node_id = next_table.node_id
    lines: list[str] = []

    # --- LSR bindings diff by in_label ---
    curr_bindings = {b.in_label: b for b in current.lsr_bindings}
    next_bindings = {b.in_label: b for b in next_table.lsr_bindings}

    # Removals: in current but not in next, or changed
    for in_label, curr_b in curr_bindings.items():
        next_b = next_bindings.get(in_label)
        if next_b is None or next_b != curr_b:
            lines.append(lsr_binding_remove_command(in_label))

    # --- Ingress rules diff by dst_prefix ---
    curr_rules = {r.dst_prefix: r for r in current.ler_ingress_rules}
    next_rules = {r.dst_prefix: r for r in next_table.ler_ingress_rules}

    # Removals
    for prefix, curr_r in curr_rules.items():
        next_r = next_rules.get(prefix)
        if next_r is None or next_r != curr_r:
            cmd = ingress_rule_remove_command(curr_r, node_id, iface_to_peer_loopback)
            if cmd:
                lines.append(cmd)

    # --- Additions ---
    for in_label, next_b in next_bindings.items():
        curr_b = curr_bindings.get(in_label)
        if curr_b is None or curr_b != next_b:
            cmd = lsr_binding_to_command(next_b, node_id, sid_to_loopback, iface_to_peer_loopback)
            if cmd:
                lines.append(cmd)

    for prefix, next_r in next_rules.items():
        curr_r = curr_rules.get(prefix)
        if curr_r is None or curr_r != next_r:
            cmd = ingress_rule_to_command(next_r, node_id, iface_to_peer_loopback)
            if cmd:
                lines.append(cmd)

    return wrap_in_configure_block(lines)
