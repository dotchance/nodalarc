"""Tests for PlatformConfig Pydantic model and singleton."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from nodalarc.platform_config import (
    PlatformConfig,
    get_platform_config,
    init_platform_config,
    reset_platform_config,
)
from pydantic import ValidationError

ROOT = Path(__file__).resolve().parents[3]


def _valid_config_dict() -> dict:
    return {
        "kubernetes_namespace": "nodalarc",
        "ome_link_state_snapshot_interval_s": 5.0,
        "vs_api_http_port": 8080,
        "probe_daemon_http_api_port": 9100,
        "probe_daemon_udp_data_port": 19100,
        "session_data_root": "/var/nodalarc/sessions",
        "veth_interface_mtu_bytes": 9000,
        "vxlan_udp_port": 14789,
        "node_agent_loads_kernel_modules": False,
        "kubelet_root_dir": "/var/lib/kubelet",
        "vs_api_visual_beam_falloff_exponent": 2.0,
        "vs_api_actuation_expected_latency_ms": 250.0,
        "vs_api_actuation_fault_after_ms": 1200.0,
        "scheduler_clean_kernel_audit_interval_s": 60.0,
        "default_session_pod_placement_policy": "planePerNode",
        "default_session_pod_planes_per_group": 1,
        "vs_api_introspect_max_requests_per_minute": 10,
        "vs_api_playback_max_requests_per_minute": 30,
        "vs_api_session_switch_max_requests_per_minute": 5,
        "vs_api_introspect_max_response_bytes": 65536,
        "vs_api_history_max_bytes": 262144000,
        "vs_api_history_queue_max_writes": 10000,
        "session_pods_per_node": 200,
        "trace_interval_seconds": 3.0,
        "trace_unreached_retrace_seconds": 1.0,
        "trace_max_seconds": 180.0,
    }


def _with_shipped_values(rendered: str, shipped: str) -> str:
    """The chart copy with every templated setting put back to the shipped literal."""
    from nodalarc.platform_config import CHART_TEMPLATED_SETTINGS

    platform = yaml.safe_load(shipped)["platform"]
    for field, template in CHART_TEMPLATED_SETTINGS.items():
        literal = platform[field]
        text = str(literal).lower() if isinstance(literal, bool) else str(literal)
        rendered = rendered.replace(f"{field}: {template}", f"{field}: {text}")
    return rendered


class TestPlatformConfig:
    def test_shipped_platform_yaml_declares_exactly_the_model(self):
        """The file is the single source: every model field is in it and nothing else is."""
        raw = yaml.safe_load((ROOT / "configs" / "platform.yaml").read_text(encoding="utf-8"))
        cfg = PlatformConfig.model_validate(raw["platform"])
        assert set(raw["platform"]) == set(cfg.model_dump())

    def test_chart_copy_templates_every_installer_setting(self):
        """Each installer-chosen setting becomes its chart value in the chart copy,
        and the file's literal equals the chart's default, so the platform file
        and a default install describe the same system."""
        from nodalarc.platform_config import CHART_TEMPLATED_SETTINGS, render_chart_copy

        shipped = (ROOT / "configs" / "platform.yaml").read_text(encoding="utf-8")
        rendered = render_chart_copy(shipped)
        for field, template in CHART_TEMPLATED_SETTINGS.items():
            assert rendered.count(f"{field}: {template}") == 1, field

        platform = yaml.safe_load(shipped)["platform"]
        values = yaml.safe_load(
            (ROOT / "deploy" / "helm" / "values.yaml").read_text(encoding="utf-8")
        )
        # The namespace is the Helm release's, so it has no chart value; the
        # shipped literal equals the lifecycle scripts' default release namespace.
        installer = (ROOT / "scripts" / "na-install-platform.sh").read_text(encoding="utf-8")
        assert 'NAMESPACE="${NAMESPACE:-' + platform["kubernetes_namespace"] + '}"' in installer
        assert "namespace" not in values
        assert platform["veth_interface_mtu_bytes"] == values["network"]["linkMtu"]
        assert platform["vxlan_udp_port"] == values["network"]["vxlanPort"]
        assert (
            platform["node_agent_loads_kernel_modules"] == values["nodeAgent"]["loadKernelModules"]
        )
        assert platform["kubelet_root_dir"] == values["nodeAgent"]["kubeletRootDir"]

    def test_chart_copy_templates_the_namespace_field_by_meaning(self):
        from nodalarc.platform_config import CHART_NAMESPACE_VALUE, render_chart_copy

        shipped = (ROOT / "configs" / "platform.yaml").read_text(encoding="utf-8")
        rendered = render_chart_copy(shipped)
        assert rendered.count(f"kubernetes_namespace: {CHART_NAMESPACE_VALUE}") == 1
        assert _with_shipped_values(rendered, shipped) == shipped
        forms = {
            'kubernetes_namespace: "nodalarc"': f"kubernetes_namespace: {CHART_NAMESPACE_VALUE}",
            "kubernetes_namespace:   'nodalarc'  # the release namespace": (
                f"kubernetes_namespace:   {CHART_NAMESPACE_VALUE}  # the release namespace"
            ),
            '"kubernetes_namespace": nodalarc': f'"kubernetes_namespace": {CHART_NAMESPACE_VALUE}',
            "kubernetes_namespace : nodalarc": f"kubernetes_namespace : {CHART_NAMESPACE_VALUE}",
            "kubernetes_namespace:\n    nodalarc": f"kubernetes_namespace:\n    {CHART_NAMESPACE_VALUE}",
        }
        for source_form, expected in forms.items():
            out = render_chart_copy(shipped.replace("kubernetes_namespace: nodalarc", source_form))
            assert expected in out, source_form
            resolved = yaml.safe_load(
                _with_shipped_values(out.replace(CHART_NAMESPACE_VALUE, '"rendered-ns"'), shipped)
            )
            assert resolved["platform"]["kubernetes_namespace"] == "rendered-ns", source_form
        reindented = shipped.replace("\n  ", "\n    ")
        assert f"    kubernetes_namespace: {CHART_NAMESPACE_VALUE}" in render_chart_copy(reindented)

    def test_chart_copy_refuses_an_invalid_file(self):
        from nodalarc.platform_config import render_chart_copy

        shipped = (ROOT / "configs" / "platform.yaml").read_text(encoding="utf-8")
        with pytest.raises(ValidationError):
            render_chart_copy(shipped + "  not_a_setting: 1\n")
        with pytest.raises(ValidationError):
            render_chart_copy(
                shipped.replace("kubernetes_namespace: nodalarc", "namespace: nodalarc")
            )
        with pytest.raises(ValueError, match="exactly once"):
            render_chart_copy(
                shipped.replace(
                    "kubernetes_namespace: nodalarc",
                    "kubernetes_namespace: nodalarc\n  kubernetes_namespace: nodalarc",
                )
            )

    def test_chart_copy_refuses_value_forms_it_cannot_template(self):
        """Valid YAML whose value is not one scalar on one line is refused before output."""
        from nodalarc.platform_config import render_chart_copy

        shipped = (ROOT / "configs" / "platform.yaml").read_text(encoding="utf-8")
        for form in (
            "kubernetes_namespace: >-\n    nodalarc",
            "kubernetes_namespace: |-\n    nodalarc",
            "kubernetes_namespace: nodalarc\n    continued",
            "kubernetes_namespace: &ns nodalarc",
            "kubernetes_namespace: !!str nodalarc",
        ):
            text = shipped.replace("kubernetes_namespace: nodalarc", form)
            assert yaml.safe_load(text)["platform"]["kubernetes_namespace"]
            with pytest.raises(ValueError, match="not templated"):
                render_chart_copy(text)
        aliased = "anchored: &ns nodalarc\n" + shipped.replace(
            "kubernetes_namespace: nodalarc", "kubernetes_namespace: *ns"
        )
        assert yaml.safe_load(aliased)["platform"]["kubernetes_namespace"] == "nodalarc"
        with pytest.raises(ValueError, match="not templated"):
            render_chart_copy(aliased)

    def test_assembled_chart_copy_is_the_shipped_file_with_the_settings_templated(self, tmp_path):
        import os
        import subprocess

        from nodalarc.platform_config import render_chart_copy

        out = tmp_path / "chart"
        subprocess.run(
            ["bash", str(ROOT / "scripts/na-render-helm-chart.sh"), "deploy/helm", str(out)],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
            env={**os.environ, "PROJECT_VERSION": "0+test"},
        )
        rendered = (out / "files" / "platform.yaml").read_text(encoding="utf-8")
        shipped = (ROOT / "configs" / "platform.yaml").read_text(encoding="utf-8")
        assert rendered == render_chart_copy(shipped)
        assert _with_shipped_values(rendered, shipped) == shipped


class TestSingleton:
    def setup_method(self):
        reset_platform_config()

    def teardown_method(self):
        # Re-initialize with standard values so other tests still work
        init_platform_config(PlatformConfig(**_valid_config_dict()))

    def test_get_before_init_raises(self):
        with pytest.raises(RuntimeError, match="not initialized"):
            get_platform_config()

    def test_init_from_yaml(self, tmp_path):
        import yaml

        yaml_path = tmp_path / "platform.yaml"
        yaml_path.write_text(yaml.dump({"platform": _valid_config_dict()}))
        result = init_platform_config(yaml_path)
        assert result.kubernetes_namespace == "nodalarc"
        assert get_platform_config() is result
