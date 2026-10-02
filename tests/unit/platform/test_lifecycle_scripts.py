"""Unit-level lifecycle script tests with stubbed commands."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]


def _run(
    args: list[str],
    *,
    env: dict[str, str] | None = None,
    path_dir: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    merged = os.environ.copy()
    merged.update(
        {
            "MODE": "auto",
            "REGISTRY_HOST": "",
            "REGISTRY_PREFIX": "",
            "REGISTRIES_YAML": str(ROOT / ".does-not-exist"),
            "TAG": "abc123",
        }
    )
    if env:
        merged.update(env)
    if path_dir:
        merged["PATH"] = f"{path_dir}:{merged['PATH']}"
    return subprocess.run(
        args,
        cwd=ROOT,
        env=merged,
        text=True,
        capture_output=True,
        check=False,
    )


@pytest.mark.parametrize("script", sorted((ROOT / "scripts").glob("*.sh")), ids=lambda p: p.name)
def test_shell_scripts_are_parseable(script: Path) -> None:
    result = _run(["bash", "-n", str(script)])
    assert result.returncode == 0, result.stderr


def test_mode_resolver_refuses_a_registry_prefix_even_with_a_host(tmp_path: Path) -> None:
    """The prefix is derived from the host; a supplied prefix is refused, never ignored."""
    result = _run(
        ["bash", "scripts/na-mode.sh", "--no-cluster"],
        env={"REGISTRY_HOST": "registry.local:5000", "REGISTRY_PREFIX": "registry.local:5000/"},
        path_dir=tmp_path,
    )
    assert result.returncode == 2, result.stderr
    assert "REGISTRY_PREFIX is not a setting" in result.stderr


def test_purge_containerd_local_k3s_unavailable_is_explicit(tmp_path: Path) -> None:
    missing_k3s = tmp_path / "missing-k3s"
    result = _run(
        ["bash", "scripts/na-purge-containerd.sh"],
        env={
            "PURGE_SCOPE": "local",
            "LOCAL_REQUIRED": "1",
            "K3S_BIN": str(missing_k3s),
            "SUDO_CTR": "",
        },
    )
    assert result.returncode != 0
    assert "local: failed (k3s command unavailable)" in result.stderr


def _lib_call(body: str, *, path_dir: Path, env: dict[str, str] | None = None):
    """Run a snippet with scripts/na-lib.sh sourced, the way the lifecycle scripts do."""
    return _run(["bash", "-c", f". scripts/na-lib.sh; {body}"], env=env, path_dir=path_dir)


def test_mode_record_keeps_empty_registry_fields(tmp_path: Path) -> None:
    """A single-node record has an empty host and prefix; the parser must not collapse them."""
    result = _lib_call(
        'mode_record_load "$(bash scripts/na-mode.sh --no-cluster)"; '
        'printf "%s|%s|%s|%s\n" "$MODE_RESOLVED" "$REGISTRY_HOST_RESOLVED" "$REGISTRY_PREFIX_RESOLVED" "$NODE_COUNT"',
        env={"MODE": "single-node"},
        path_dir=tmp_path,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "single-node|||0"


def test_mode_record_multi_node_without_cluster(tmp_path: Path) -> None:
    result = _lib_call(
        'mode_record_load "$(bash scripts/na-mode.sh --no-cluster)"; '
        'printf "%s|%s|%s\n" "$MODE_RESOLVED" "$REGISTRY_HOST_RESOLVED" "$REGISTRY_PREFIX_RESOLVED"',
        env={"MODE": "auto", "REGISTRY_HOST": "registry.local:5000"},
        path_dir=tmp_path,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "multi-node|registry.local:5000|registry.local:5000/"


def test_unknown_mode_is_refused_on_the_no_cluster_path() -> None:
    result = _run(
        ["bash", "scripts/na-images.sh", "list-build-images"],
        env={"NA_IMAGES_NO_CLUSTER": "1", "MODE": "bogus"},
    )
    assert result.returncode != 0
    assert "MODE must be auto, single-node, or multi-node" in result.stderr


def test_mode_record_parser_refuses_a_foreign_key(tmp_path: Path) -> None:
    result = _lib_call(
        'mode_record_load "mode=single-node\nnode_count=0\nregistry=x"', path_dir=tmp_path
    )
    assert result.returncode != 0
    assert "unknown mode record key" in result.stderr


def _chart_identity():
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "na_chart_identity", ROOT / "scripts" / "na-chart-identity.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _assembled_chart(tmp_path: Path, name: str = "chart") -> Path:
    out = tmp_path / name
    subprocess.run(
        ["bash", str(ROOT / "scripts/na-render-helm-chart.sh"), "deploy/helm", str(out)],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
        env={**os.environ, "PROJECT_VERSION": "0+test"},
    )
    return out


def test_assembled_chart_carries_the_rendered_messaging_inventory(tmp_path: Path) -> None:
    """The assembler writes the registry's messaging inventory into the output chart;
    the source chart carries no copy, so the chart consumes what the tree authored."""
    from nodalarc.nats_channels import render_messaging_inventory

    chart = _assembled_chart(tmp_path)

    assert (chart / "files/nats-messaging.yaml").read_text() == render_messaging_inventory()
    assert not (ROOT / "deploy/helm/files/nats-messaging.yaml").exists()


def test_assembled_chart_records_its_content_digest(tmp_path: Path) -> None:
    import yaml

    chart = _assembled_chart(tmp_path)
    metadata = yaml.safe_load((chart / "Chart.yaml").read_text())
    assert metadata["annotations"]["nodalarc.io/chart-digest"] == _chart_identity().chart_digest(
        chart
    )
    assert metadata["version"] == "0+test"
