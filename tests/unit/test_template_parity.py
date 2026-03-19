"""Template parity tests — unified templates must produce identical output to legacy.

For each of the 5 FRR stacks, we render both the legacy per-stack templates and
the unified templates with matching resolved variables, and assert functionally
identical output (normalized blank lines — FRR is whitespace-insensitive).
"""

from pathlib import Path

import pytest
from jinja2 import Environment, FileSystemLoader
from nodalarc.stack_resolver import resolve_stack


def _normalize(text: str) -> str:
    """Remove all blank lines — FRR config is whitespace-insensitive."""
    lines = [line for line in text.splitlines() if line.strip()]
    return "\n".join(lines) + "\n"


# Map legacy stack dirs to (protocol, extensions) pairs
LEGACY_STACKS = {
    "frr-ospf-plain": ("ospf", []),
    "frr-ospf-te": ("ospf", ["te"]),
    "frr-ospf-te-mpls": ("ospf", ["te", "mpls"]),
    "frr-isis-sr": ("isis", ["sr"]),
}

# Sample template variables that exercise all template branches
_BASE_VARS = {
    "hostname": "test-node",
    "node_id": "sat-P00S00",
    "node_type": "satellite",
    "ipv4_loopback": "10.0.0.1",
    "ipv6_loopback": "fd00::0:0:1",
    "area_id": "49.0001",
    "plane": 0,
    "slot": 0,
    "gs_index": 0,
    "isl_interfaces": ["isl0", "isl1"],
    "gnd_interfaces": ["gnd0"],
    "interface_info": {
        "isl0": {"bandwidth_mbps": 1000, "cross_area": False, "peer_loopback_ipv4": "10.0.1.1"},
        "isl1": {"bandwidth_mbps": 1000, "cross_area": True, "peer_loopback_ipv4": "10.1.0.1"},
    },
    "reference_bandwidth": 10000,
    "compression_factor": 1,
}


def _load_legacy_stack_vars(stack_name: str) -> dict:
    """Load template_variables from a legacy stack.yaml and merge with base vars."""
    import yaml

    stack_dir = Path(f"configs/routing-stacks/{stack_name}")
    raw = yaml.safe_load((stack_dir / "stack.yaml").read_text())
    stack_vars = raw["stack"].get("template_variables", {})
    merged = dict(_BASE_VARS)
    merged.update(stack_vars)
    return merged


def _render_legacy(stack_name: str, template_src: str, vars: dict) -> str:
    """Render a template from the legacy stack directory."""
    stack_dir = Path(f"configs/routing-stacks/{stack_name}")
    env = Environment(loader=FileSystemLoader(str(stack_dir)), keep_trailing_newline=True)
    return env.get_template(template_src).render(**vars)


def _render_unified(template_src: str, vars: dict) -> str:
    """Render a template from the unified templates directory."""
    unified_dir = Path("configs/templates/frr")
    env = Environment(loader=FileSystemLoader(str(unified_dir)), keep_trailing_newline=True)
    return env.get_template(template_src).render(**vars)


@pytest.mark.parametrize("stack_name", list(LEGACY_STACKS.keys()))
class TestTemplateParity:
    """For each legacy stack, verify that unified templates produce identical output."""

    def test_parity(self, stack_name: str):
        protocol, extensions = LEGACY_STACKS[stack_name]
        resolved = resolve_stack(protocol, extensions)

        # Build unified vars: base + resolved.template_variables
        unified_vars = dict(_BASE_VARS)
        unified_vars.update(resolved.template_variables)

        # Build legacy vars: base + stack.yaml template_variables
        legacy_vars = _load_legacy_stack_vars(stack_name)

        # Load the template file list from the legacy stack
        import yaml

        stack_dir = Path(f"configs/routing-stacks/{stack_name}")
        raw = yaml.safe_load((stack_dir / "stack.yaml").read_text())
        legacy_templates = raw["stack"].get("config_templates", [])

        for tpl_info in legacy_templates:
            src = tpl_info["src"]
            legacy_output = _normalize(_render_legacy(stack_name, src, legacy_vars))
            unified_output = _normalize(_render_unified(src, unified_vars))
            assert legacy_output == unified_output, (
                f"Parity mismatch for {stack_name}/{src}:\n"
                f"--- LEGACY ---\n{legacy_output}\n"
                f"--- UNIFIED ---\n{unified_output}"
            )

    def test_daemon_list_matches(self, stack_name: str):
        """Verify resolved daemons match the legacy stack.yaml daemons."""
        import yaml

        protocol, extensions = LEGACY_STACKS[stack_name]
        resolved = resolve_stack(protocol, extensions)

        stack_dir = Path(f"configs/routing-stacks/{stack_name}")
        raw = yaml.safe_load((stack_dir / "stack.yaml").read_text())
        legacy_daemons = raw["stack"].get("daemons", [])

        assert sorted(resolved.daemons) == sorted(legacy_daemons), (
            f"Daemon mismatch for {stack_name}: "
            f"resolved={resolved.daemons}, legacy={legacy_daemons}"
        )


class TestGroundStationParity:
    """Test that GS-specific template branches also match."""

    @pytest.mark.parametrize("stack_name", ["frr-ospf-plain", "frr-isis-sr"])
    def test_gs_zebra_parity(self, stack_name: str):
        protocol, extensions = LEGACY_STACKS[stack_name]
        resolved = resolve_stack(protocol, extensions)

        gs_vars = {
            **_BASE_VARS,
            "node_type": "ground_station",
            "gs_name": "ashburn",
            "gs_index": 0,
            "isl_interfaces": [],
            "gnd_interfaces": ["gnd0"],
            "interface_info": {},
            "terrestrial_prefixes": [
                {"prefix": "172.16.0.0/24", "host_address": "172.16.0.1/24", "metric": 10},
            ],
        }

        legacy_gs_vars = dict(gs_vars)
        import yaml

        stack_dir = Path(f"configs/routing-stacks/{stack_name}")
        raw = yaml.safe_load((stack_dir / "stack.yaml").read_text())
        legacy_gs_vars.update(raw["stack"].get("template_variables", {}))

        unified_gs_vars = dict(gs_vars)
        unified_gs_vars.update(resolved.template_variables)

        legacy_out = _normalize(_render_legacy(stack_name, "zebra.conf.j2", legacy_gs_vars))
        unified_out = _normalize(_render_unified("zebra.conf.j2", unified_gs_vars))
        assert legacy_out == unified_out
