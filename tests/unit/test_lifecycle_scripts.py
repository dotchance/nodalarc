"""Unit-level lifecycle script tests with stubbed commands."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


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


def _stub(path: Path, name: str, body: str) -> None:
    script = path / name
    script.write_text(f"#!/usr/bin/env bash\nset -euo pipefail\n{body}\n")
    script.chmod(0o755)


def test_shell_scripts_are_parseable() -> None:
    scripts = [
        "tools/na-mode.sh",
        "tools/na-images.sh",
        "tools/na-image-preflight.sh",
        "tools/na-load-images.sh",
        "tools/na-install-platform.sh",
        "tools/na-session.sh",
        "tools/na-deploy-service.sh",
        "tools/na-redeploy.sh",
        "tools/na-purge-containerd.sh",
        "tools/na-clean-images.sh",
        "tools/na-nuke.sh",
        "tools/clean-registry.sh",
        "tools/na-status.sh",
        "tools/na-teardown.sh",
    ]
    result = _run(["bash", "-n", *scripts])
    assert result.returncode == 0, result.stderr


def test_mode_resolver_rejects_multi_node_without_registry(tmp_path: Path) -> None:
    _stub(
        tmp_path,
        "kubectl",
        """
if [ "$1 $2" = "get nodes" ]; then
  printf 'node01 Ready\\nnode02 Ready\\n'
  exit 0
fi
exit 1
""",
    )
    result = _run(["bash", "tools/na-mode.sh"], path_dir=tmp_path)
    assert result.returncode != 0
    assert "multi-node cluster detected" in result.stderr


def test_image_inventory_generates_runtime_helm_args_without_cluster() -> None:
    result = _run(
        ["bash", "tools/na-images.sh", "helm-image-args"],
        env={"NA_IMAGES_NO_CLUSTER": "1", "REGISTRY_HOST": "registry.local:5000"},
    )
    assert result.returncode == 0, result.stderr
    output = result.stdout
    assert "--set-string=images.frr=registry.local:5000/nodalarc/frr:abc123" in output
    assert "--set-string=images.probe=registry.local:5000/nodalarc/probe:abc123" in output
    assert (
        "--set-string=images.nodalpathFwd=registry.local:5000/nodalarc/nodalpath-fwd:abc123"
        in output
    )
    assert "--set-string=images.natsBox=natsio/nats-box:0.19.3" in output
    assert "--set-string=imagePullPolicy=IfNotPresent" in output


def test_registry_catalog_failure_is_not_treated_as_empty(tmp_path: Path) -> None:
    _stub(tmp_path, "crane", 'if [ "$1" = "catalog" ]; then echo boom >&2; exit 7; fi')
    result = _run(
        ["bash", "tools/clean-registry.sh"],
        env={"REGISTRY_HOST": "registry.local:5000", "REGISTRY_INSECURE": "1"},
        path_dir=tmp_path,
    )
    assert result.returncode != 0
    assert "registry catalog failed" in result.stderr


def test_unresolvable_registry_tag_is_skipped_not_failed(tmp_path: Path) -> None:
    _stub(
        tmp_path,
        "crane",
        """
case "$1" in
  catalog) printf 'nodalarc/ome\\n' ;;
  ls) printf 'latest\\n' ;;
  digest) exit 1 ;;
  *) exit 2 ;;
esac
""",
    )
    result = _run(
        ["bash", "tools/clean-registry.sh"],
        env={"REGISTRY_HOST": "registry.local:5000", "REGISTRY_INSECURE": "1"},
        path_dir=tmp_path,
    )
    assert result.returncode == 0, result.stderr
    assert "SKIPPED registry.local:5000/nodalarc/ome:latest" in result.stderr


def test_purge_containerd_local_k3s_unavailable_is_explicit(tmp_path: Path) -> None:
    missing_k3s = tmp_path / "missing-k3s"
    result = _run(
        ["bash", "tools/na-purge-containerd.sh"],
        env={
            "PURGE_SCOPE": "local",
            "LOCAL_REQUIRED": "1",
            "K3S_BIN": str(missing_k3s),
            "SUDO_CTR": "",
        },
    )
    assert result.returncode != 0
    assert "local: failed (k3s command unavailable)" in result.stderr


def test_purge_containerd_remote_removes_image_refs_not_ids(tmp_path: Path) -> None:
    trace_file = tmp_path / "remote-script.txt"
    _stub(
        tmp_path,
        "kubectl",
        """
if [ "$1 $2" = "get pods" ]; then
  printf 'agent-a node01\\n'
  exit 0
fi
if [ "$1" = "exec" ]; then
  script="${@: -1}"
  printf '%s\\n' "$script" > "$TRACE_FILE"
  printf 'purged:2\\n'
  exit 0
fi
exit 1
""",
    )
    result = _run(
        ["bash", "tools/na-purge-containerd.sh"],
        env={"PURGE_SCOPE": "remote", "REMOTE_REQUIRED": "1", "TRACE_FILE": str(trace_file)},
        path_dir=tmp_path,
    )
    assert result.returncode == 0, result.stderr
    assert "remote: node01 purged (2)" in result.stdout
    remote_script = trace_file.read_text()
    assert "print $3" not in remote_script
    assert 'print $1 ":" $2' in remote_script
    assert 'k3s crictl rmi "$image"' in remote_script


def test_registry_preflight_accepts_oci_indexes(tmp_path: Path) -> None:
    trace_file = tmp_path / "curl-args.txt"
    _stub(
        tmp_path,
        "curl",
        """
printf '%s\\n' "$*" >> "$TRACE_FILE"
exit 0
""",
    )
    result = _run(
        ["bash", "tools/na-image-preflight.sh"],
        env={
            "MODE": "multi-node",
            "REGISTRY_HOST": "registry.local:5000",
            "TRACE_FILE": str(trace_file),
        },
        path_dir=tmp_path,
    )
    assert result.returncode == 0, result.stderr
    curl_args = trace_file.read_text()
    assert "Accept: application/vnd.oci.image.index.v1+json" in curl_args
    assert "application/vnd.docker.distribution.manifest.v2+json" in curl_args


def test_status_uses_oci_manifest_accept_header() -> None:
    status_script = (ROOT / "tools/na-status.sh").read_text()
    assert "application/vnd.oci.image.index.v1+json" in status_script
    assert "registry_manifest_exists" in status_script


def test_lifecycle_scripts_print_next_steps() -> None:
    expected = {
        "tools/na-install-platform.sh": "[install] Next: make session",
        "tools/na-session.sh": "[session] Next: make status",
        "tools/na-nuke.sh": "[nuke] Next: make all",
        "tools/na-teardown.sh": "[teardown] Next: make install && make session",
        "tools/na-deploy-service.sh": "[deploy] Next: make status",
    }
    for rel, marker in expected.items():
        assert marker in (ROOT / rel).read_text()


def test_session_readiness_requires_expected_generation_and_pod_counts() -> None:
    script = (ROOT / "tools/na-session.sh").read_text()
    assert "compute_expected_pod_count" in script
    assert '{.status.phase}{"|"}{.status.observedGeneration}' in script
    assert '[ "$ready_pods" = "$expected_pods" ]' in script
    assert '[ "$pod_count" = "$expected_pods" ]' in script
    assert "live pod count is stale" in script
    assert "Waiting for platform rollout to settle" in script
    assert "Computing placement policy" in script
    assert "verify_session_placement" in script
    assert "expected session pods on" in script
    assert "Placement verified" in script
    assert 'grep -E "nodalarc-|nodalpath-|ome-"' in script


def test_load_next_step_is_state_aware() -> None:
    script = (ROOT / "tools/na-load-images.sh").read_text()
    assert "helm status" in script
    assert "[load] Next: make install" in script
    assert "[load] Next: make upgrade" in script
    assert "make reinstall && make session" in script
    assert "make install will refuse the existing namespace" in script


def test_install_passes_node_agent_host_network_cidrs_to_helm() -> None:
    script = (ROOT / "tools/na-install-platform.sh").read_text()
    assert "nodalarc.io/node-agent=true" in script
    assert "nats.networkPolicy.hostNetworkCIDRs[$idx]" in script
    assert "nats.hostNetworkHost=$nats_host" in script
    assert "/32" in script
    assert "/128" in script
