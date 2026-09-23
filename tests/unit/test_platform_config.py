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

ROOT = Path(__file__).resolve().parents[2]


def _valid_config_dict() -> dict:
    return {
        "kubernetes_namespace": "nodalarc",
        "ome_link_state_snapshot_interval_s": 5.0,
        "default_service_host": "127.0.0.1",
        "vs_api_http_port": 8080,
        "nodalpath_console_http_port": 3100,
        "nodalpath_fwd_grpc_port": 50051,
        "probe_daemon_http_api_port": 9100,
        "probe_daemon_udp_data_port": 19100,
        "session_data_root": "/var/nodalarc/sessions",
        "veth_interface_mtu_bytes": 9000,
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
        "trace_interval_seconds": 3.0,
    }


class TestPlatformConfig:
    def test_validates_from_dict(self):
        cfg = PlatformConfig(**_valid_config_dict())
        assert cfg.kubernetes_namespace == "nodalarc"

    def test_frozen(self):
        cfg = PlatformConfig(**_valid_config_dict())
        with pytest.raises(ValidationError):
            cfg.kubernetes_namespace = "other"

    def test_missing_field_raises(self):
        d = _valid_config_dict()
        del d["kubernetes_namespace"]
        with pytest.raises(ValidationError):
            PlatformConfig(**d)

    def test_service_host_default(self):
        cfg = PlatformConfig(**_valid_config_dict())
        assert cfg.service_host("anything") == "127.0.0.1"

    def test_service_host_override(self):
        d = _valid_config_dict()
        d["service_hosts"] = {"vs-api": "nodalarc-vs-api", "nodalpath": "nodalpath"}
        cfg = PlatformConfig(**d)
        assert cfg.service_host("vs-api") == "nodalarc-vs-api"
        assert cfg.service_host("nodalpath") == "nodalpath"
        assert cfg.service_host("unknown") == "127.0.0.1"

    def test_shipped_platform_yaml_declares_exactly_the_model(self):
        """The file is the single source: every model field is in it and nothing else is."""
        raw = yaml.safe_load((ROOT / "configs" / "platform.yaml").read_text(encoding="utf-8"))
        cfg = PlatformConfig.model_validate(raw["platform"])
        assert set(raw["platform"]) == set(cfg.model_dump())

    def test_declared_setting_missing_from_the_file_is_refused(self):
        d = _valid_config_dict()
        del d["ome_link_state_snapshot_interval_s"]
        with pytest.raises(ValidationError):
            PlatformConfig(**d)

    def test_unknown_key_is_refused(self):
        with pytest.raises(ValidationError):
            PlatformConfig(**{**_valid_config_dict(), "ome_full_state_snapshot_interval_s": 10})

    def test_chart_copy_templates_the_namespace_field_by_meaning(self):
        from nodalarc.platform_config import CHART_NAMESPACE_VALUE, render_chart_copy

        shipped = (ROOT / "configs" / "platform.yaml").read_text(encoding="utf-8")
        rendered = render_chart_copy(shipped)
        assert rendered.count(f"kubernetes_namespace: {CHART_NAMESPACE_VALUE}") == 1
        assert rendered.replace(CHART_NAMESPACE_VALUE, "nodalarc") == shipped
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
            resolved = yaml.safe_load(out.replace(CHART_NAMESPACE_VALUE, '"rendered-ns"'))
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

    def test_assembled_chart_copy_is_the_shipped_file_with_the_namespace_templated(self, tmp_path):
        import os
        import subprocess

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
        assert rendered.replace('"{{ .Values.namespace }}"', "nodalarc") == shipped


class TestSingleton:
    def setup_method(self):
        reset_platform_config()

    def teardown_method(self):
        # Re-initialize with standard values so other tests still work
        init_platform_config(PlatformConfig(**_valid_config_dict()))

    def test_get_before_init_raises(self):
        with pytest.raises(RuntimeError, match="not initialized"):
            get_platform_config()

    def test_init_from_object(self):
        cfg = PlatformConfig(**_valid_config_dict())
        result = init_platform_config(cfg)
        assert result is cfg
        assert get_platform_config() is cfg

    def test_init_from_yaml(self, tmp_path):
        import yaml

        yaml_path = tmp_path / "platform.yaml"
        yaml_path.write_text(yaml.dump({"platform": _valid_config_dict()}))
        result = init_platform_config(yaml_path)
        assert result.kubernetes_namespace == "nodalarc"
        assert get_platform_config() is result
