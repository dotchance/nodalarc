"""Unit-level lifecycle script tests with stubbed commands."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

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
    scripts = sorted(str(path.relative_to(ROOT)) for path in (ROOT / "scripts").glob("*.sh"))
    assert scripts
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
    result = _run(["bash", "scripts/na-mode.sh"], path_dir=tmp_path)
    assert result.returncode != 0
    assert "multi-node cluster detected" in result.stderr


def test_mode_resolver_refuses_a_registry_prefix_even_with_a_host(tmp_path: Path) -> None:
    """The prefix is derived from the host; a supplied prefix is refused, never ignored."""
    result = _run(
        ["bash", "scripts/na-mode.sh", "--no-cluster"],
        env={"REGISTRY_HOST": "registry.local:5000", "REGISTRY_PREFIX": "registry.local:5000/"},
        path_dir=tmp_path,
    )
    assert result.returncode == 2, result.stderr
    assert "REGISTRY_PREFIX is not a setting" in result.stderr


def test_image_inventory_generates_runtime_helm_args_without_cluster() -> None:
    result = _run(
        ["bash", "scripts/na-images.sh", "helm-image-args"],
        env={"NA_IMAGES_NO_CLUSTER": "1", "REGISTRY_HOST": "registry.local:5000"},
    )
    assert result.returncode == 0, result.stderr
    output = result.stdout
    assert "--set-string=images.frr=registry.local:5000/nodalarc/frr:abc123" in output
    assert "--set-string=images.probe=registry.local:5000/nodalarc/probe:abc123" in output
    assert "--set-string=images.natsBox=natsio/nats-box:0.19.3" in output
    assert "--set-string=imagePullPolicy=Always" in output


def test_registry_catalog_failure_is_not_treated_as_empty(tmp_path: Path) -> None:
    _stub(tmp_path, "crane", 'if [ "$1" = "catalog" ]; then echo boom >&2; exit 7; fi')
    result = _run(
        ["bash", "scripts/clean-registry.sh"],
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
        ["bash", "scripts/clean-registry.sh"],
        env={"REGISTRY_HOST": "registry.local:5000", "REGISTRY_INSECURE": "1"},
        path_dir=tmp_path,
    )
    assert result.returncode == 0, result.stderr
    assert "SKIPPED registry.local:5000/nodalarc/ome:latest" in result.stderr


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
        ["bash", "scripts/na-purge-containerd.sh"],
        env={"PURGE_SCOPE": "remote", "REMOTE_REQUIRED": "1", "TRACE_FILE": str(trace_file)},
        path_dir=tmp_path,
    )
    assert result.returncode == 0, result.stderr
    assert "remote: node01 purged (2)" in result.stdout
    remote_script = trace_file.read_text()
    assert "print $3" not in remote_script
    assert 'print $1 ":" $2' in remote_script
    assert 'crictl --runtime-endpoint "$runtime" rmi "$image"' in remote_script
    assert "k3s crictl" not in remote_script


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
        ["bash", "scripts/na-image-preflight.sh"],
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
    status_script = (ROOT / "scripts/na-status.sh").read_text()
    assert "application/vnd.oci.image.index.v1+json" in status_script
    assert "registry_manifest_exists" in status_script


def test_lifecycle_scripts_print_next_steps() -> None:
    expected = {
        "scripts/na-install-platform.sh": "[install] Next: make session",
        "scripts/na-session.sh": "[session] Next: make status",
        "scripts/na-nuke.sh": "[nuke] Next: make all",
        "scripts/na-teardown.sh": "[teardown] Next: make install && make session",
        "scripts/na-deploy-service.sh": "Next: make status",
    }
    for rel, marker in expected.items():
        assert marker in (ROOT / rel).read_text()


def test_session_readiness_requires_reviewed_transition_and_live_pod_counts() -> None:
    script = (ROOT / "scripts/na-session.sh").read_text()
    assert 'PLATFORM_CONFIG="${PLATFORM_CONFIG:-configs/platform.yaml}"' in script
    assert "init_platform_config(Path(sys.argv[3]))" in script
    assert ".runtime_session" not in script
    assert "CatalogClosureCollector.collect" in script
    assert '"$api_base/api/v1/sessions"' in script
    assert '"$api_base/api/v1/sessions/switch"' in script
    assert '"$api_base/api/v1/session-transitions/$operation_id"' in script
    assert '"expected_document_digest"' in script
    assert '"expected_dependency_digest"' in script
    assert "kubectl apply" not in script
    assert "kubectl delete constellationspec current-session" not in script
    assert '{.metadata.generation}{"|"}{.status.phase}{"|"}{.status.observedGeneration}' in script
    assert '[ "$current_generation" != "$target_generation" ]' in script
    assert 'expected_pods="$pod_count"' in script
    assert '[ "$ready_pods" != "$expected_pods" ]' in script
    assert '[ "$wired_pods" != "$expected_pods" ]' in script
    assert "live pod count is stale" in script
    assert "Waiting for platform rollout to settle" in script
    assert 'platform_converged "$NAMESPACE"' in script
    assert "availableReplicas" not in script
    assert "Computing placement policy" in script
    assert "verify_session_placement" in script
    assert "expected session pods on" in script
    assert "Placement verified" in script
    assert "from nodalarc.platform_config import compute_pod_placement" in script
    assert "nodalarc_operator.session_deployer" not in script


def test_load_next_step_is_state_aware() -> None:
    script = (ROOT / "scripts/na-load-images.sh").read_text()
    assert "helm status" in script
    assert "[load] Next: make install" in script
    assert "[load] Next: make upgrade" in script
    assert "make reinstall && make session" in script
    assert "make install will refuse the existing namespace" in script


def test_install_passes_node_agent_host_network_cidrs_to_helm() -> None:
    script = (ROOT / "scripts/na-install-platform.sh").read_text()
    assert "nodalarc.io/node-agent=true" in script
    assert "nats.networkPolicy.hostNetworkCIDRs[$idx]" in script
    assert "nats.hostNetworkHost=$nats_host" in script
    assert "/32" in script
    assert "/128" in script


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


def _kubectl_stub(path: Path, rows: dict[str, str]) -> None:
    """A kubectl that answers `get deployment NAME` and `get daemonset NAME` from ROWS
    (resource -> custom-columns row); a resource absent from ROWS fails like kubectl does."""
    cases = "\n".join(
        f'  "{kind} {name}") printf "%s\\n" "{row}"; exit 0 ;;'
        for (kind, name), row in rows.items()
    )
    _stub(
        path,
        "kubectl",
        f"""
case "$2 $3" in
{cases}
  *) exit 1 ;;
esac
""",
    )


_CONVERGED_DEPLOYMENT = "1 1 1 1 1 1 1 <none>"
_ROLLING_DEPLOYMENT = "2 1 1 2 1 1 1 <none>"
_CONVERGED_DAEMONSET = "1 1 3 3 3 3 3 0"
_REQUIRED = {
    ("deployment", "ome"),
    ("deployment", "nodalarc-scheduler"),
    ("daemonset", "nodalarc-node-agent"),
    ("deployment", "nodalarc-vs-api"),
    ("deployment", "nodalarc-operator"),
    ("deployment", "nodalarc-vf"),
    ("deployment", "nodalarc-nats"),
}


def _all_converged() -> dict[tuple[str, str], str]:
    return {
        key: (_CONVERGED_DAEMONSET if key[0] == "daemonset" else _CONVERGED_DEPLOYMENT)
        for key in _REQUIRED
    }


def test_platform_converged_when_every_required_workload_is_converged(tmp_path: Path) -> None:
    _kubectl_stub(tmp_path, _all_converged())
    result = _lib_call(
        'platform_converged nodalarc; rc=$?; echo "rc=$rc $PLATFORM_CONVERGED_SUMMARY"; exit 0',
        env={"NA_IMAGES_NO_CLUSTER": "1"},
        path_dir=tmp_path,
    )
    assert result.stdout.strip() == "rc=0 6/6 deployments converged; 3/3 Node Agents ready", (
        result.stderr
    )


def test_platform_not_converged_when_a_required_workload_is_missing(tmp_path: Path) -> None:
    """A missing required deployment must not disappear from the count."""
    rows = _all_converged()
    del rows[("deployment", "nodalarc-nats")]
    _kubectl_stub(tmp_path, rows)
    result = _lib_call(
        'platform_converged nodalarc; rc=$?; echo "rc=$rc $PLATFORM_CONVERGED_SUMMARY"; printf "%s" "$PLATFORM_PROBLEMS"; exit 0',
        env={"NA_IMAGES_NO_CLUSTER": "1"},
        path_dir=tmp_path,
    )
    assert result.stdout.startswith("rc=1 5/6 deployments converged"), result.stdout
    assert "nats: deployment/nodalarc-nats is missing" in result.stdout


def test_platform_not_converged_while_a_workload_rolls(tmp_path: Path) -> None:
    rows = _all_converged()
    rows[("deployment", "nodalarc-vs-api")] = _ROLLING_DEPLOYMENT
    _kubectl_stub(tmp_path, rows)
    result = _lib_call(
        'platform_converged nodalarc; rc=$?; echo "rc=$rc"; printf "%s" "$PLATFORM_PROBLEMS"; exit 0',
        env={"NA_IMAGES_NO_CLUSTER": "1"},
        path_dir=tmp_path,
    )
    assert result.stdout.startswith("rc=1"), result.stdout
    assert "vs-api: deployment/nodalarc-vs-api is not converged" in result.stdout


def test_deploy_service_refuses_a_resource_that_disagrees_with_the_inventory(
    tmp_path: Path,
) -> None:
    """The refusal precedes every docker, helm and kubectl call."""
    for tool in ("docker", "helm", "kubectl"):
        _stub(tmp_path, tool, 'echo "must not be called: $0 $*" >&2; exit 99')
    result = _run(
        ["bash", "scripts/na-deploy-service.sh", "ome", "deployment/nodalarc-ome"],
        env={"NA_IMAGES_NO_CLUSTER": "1", "PROJECT_VERSION": "0+test"},
        path_dir=tmp_path,
    )
    assert result.returncode == 2, result.stderr
    assert "disagrees with the inventory's 'deployment/ome'" in result.stderr
    assert "must not be called" not in result.stderr


def _scripts_copy_with_failing_inventory(tmp_path: Path) -> Path:
    """A copy of scripts/ whose na-images.sh fails before emitting any record."""
    import shutil

    root = tmp_path / "tree"
    shutil.copytree(ROOT / "scripts", root / "scripts")
    (root / "scripts" / "na-images.sh").write_text(
        "#!/usr/bin/env bash\necho 'na-images: broken' >&2\nexit 2\n"
    )
    return root


def _run_in_tree(args: list[str], *, path_dir: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        cwd=ROOT,
        env={**os.environ, "PATH": f"{path_dir}:{os.environ['PATH']}", "TAG": "abc123"},
        text=True,
        capture_output=True,
        check=False,
    )


def test_drift_gate_fails_when_the_inventory_does_not_answer(tmp_path: Path) -> None:
    """Nothing checked is drift: the --check gate must not pass on a failed inventory."""
    root = _scripts_copy_with_failing_inventory(tmp_path)
    _stub(tmp_path, "kubectl", "exit 0")
    result = _run_in_tree(
        ["bash", str(root / "scripts" / "na-drift.sh"), "--check"], path_dir=tmp_path
    )
    assert result.returncode != 0
    assert "inventory did not answer" in result.stderr


def test_platform_converged_fails_when_the_inventory_does_not_answer(tmp_path: Path) -> None:
    root = _scripts_copy_with_failing_inventory(tmp_path)
    _kubectl_stub(tmp_path, _all_converged())
    result = _run_in_tree(
        [
            "bash",
            "-c",
            f'. {root}/scripts/na-lib.sh; platform_converged nodalarc; echo "rc=$?"; printf "%s" "$PLATFORM_PROBLEMS"',
        ],
        path_dir=tmp_path,
    )
    assert result.stdout.startswith("rc=1"), result.stdout
    assert "no workload was checked" in result.stdout


def test_static_inventory_lookups_touch_neither_registry_nor_cluster(tmp_path: Path) -> None:
    """The required population and the Helm key are table facts; resolving them must not
    need a registry or a cluster, or session readiness would inherit that dependency."""
    _stub(tmp_path, "kubectl", 'echo "kubectl must not be called: $*" >&2; exit 1')
    for command in (
        ["list-platform-resources"],
        ["resource-for", "vs-api"],
        ["helm-key-for", "vs-api"],
    ):
        result = _run(
            ["bash", "scripts/na-images.sh", *command],
            env={"MODE": "multi-node", "REGISTRY_HOST": ""},
            path_dir=tmp_path,
        )
        assert result.returncode == 0, (command, result.stderr)
        assert "must not be called" not in result.stderr
    result = _run(
        ["bash", "scripts/na-images.sh", "image-for", "vs-api"],
        env={"MODE": "multi-node", "REGISTRY_HOST": ""},
        path_dir=tmp_path,
    )
    assert result.returncode != 0, "an image reference still needs the transport mode"


def test_drift_service_list_comes_from_the_inventory() -> None:
    script = (ROOT / "scripts/na-drift.sh").read_text()
    assert "SERVICES=(" not in script
    assert "list-platform-resources" in script


def _chart_identity():
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "na_chart_identity", ROOT / "scripts" / "na-chart-identity.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _release_payload(chart_dir: Path, *, digest: str | None, values: dict | None = None) -> str:
    """One Helm release payload whose stored chart is CHART_DIR.

    Helm stores templates and files as exact bytes, chart metadata (with its
    annotations) as given, and values as a map: the chart defaults after an
    install, the defaults merged with the release configuration after a
    --reuse-values upgrade. DIGEST None models a chart that recorded none.
    """
    import base64
    import gzip
    import json

    import yaml

    entries = []
    for sub in ("templates", "files", "crds"):
        for path in sorted((chart_dir / sub).rglob("*")):
            if path.is_file():
                entries.append(
                    {
                        "name": str(path.relative_to(chart_dir)),
                        "data": base64.b64encode(path.read_bytes()).decode(),
                    }
                )
    metadata: dict = {"name": "nodalarc", "version": "0+test"}
    if digest is not None:
        metadata["annotations"] = {"nodalarc.io/chart-digest": digest}
    release = {
        "chart": {
            "metadata": metadata,
            "templates": [e for e in entries if e["name"].startswith("templates/")],
            "files": [e for e in entries if not e["name"].startswith("templates/")],
            "values": values
            if values is not None
            else yaml.safe_load((chart_dir / "values.yaml").read_text()),
        }
    }
    return base64.b64encode(base64.b64encode(gzip.compress(json.dumps(release).encode()))).decode()


def _release_secrets_stub(path: Path, revisions: list[tuple[int, str, str]]) -> None:
    """A kubectl serving the release's Helm secrets, one per (version, status, payload)."""
    import json

    items = [
        {
            "metadata": {
                "name": f"sh.helm.release.v1.nodalarc.v{version}",
                "labels": {
                    "owner": "helm",
                    "name": "nodalarc",
                    "version": str(version),
                    "status": status,
                },
            },
            "data": {"release": payload},
        }
        for version, status, payload in revisions
    ]
    secrets = path / "release-secrets.json"
    secrets.write_text(json.dumps({"items": items}))
    _stub(
        path,
        "kubectl",
        f"""
case "$1 $2" in
  "get secrets") cat "{secrets}"; exit 0 ;;
  "get deployment/ome") exit 0 ;;
esac
exit 1
""",
    )


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


def _changed_chart(tmp_path: Path, relative: str, text: str) -> Path:
    """A second assembly with one content file rewritten, as a later tree would produce."""
    import shutil

    changed = tmp_path / "chart-changed"
    shutil.copytree(_assembled_chart(tmp_path, "chart-source"), changed)
    (changed / relative).write_text(text)
    return changed


def _guard(tmp_path: Path, chart: Path) -> subprocess.CompletedProcess[str]:
    return _lib_call(
        f'release_chart_matches nodalarc nodalarc "{chart}"; echo "rc=$?"; printf "%s" "$RELEASE_CHART_DIFF"',
        path_dir=tmp_path,
    )


def test_assembled_chart_records_its_content_digest(tmp_path: Path) -> None:
    import yaml

    chart = _assembled_chart(tmp_path)
    metadata = yaml.safe_load((chart / "Chart.yaml").read_text())
    assert metadata["annotations"]["nodalarc.io/chart-digest"] == _chart_identity().chart_digest(
        chart
    )
    assert metadata["version"] == "0+test"


def test_release_chart_matches_the_deployed_revision(tmp_path: Path) -> None:
    chart = _assembled_chart(tmp_path)
    digest = _chart_identity().chart_digest(chart)
    _release_secrets_stub(tmp_path, [(1, "deployed", _release_payload(chart, digest=digest))])
    result = _guard(tmp_path, chart)
    assert result.stdout.strip() == "rc=0", result.stderr


def test_release_chart_matches_after_a_reuse_values_deploy(tmp_path: Path) -> None:
    """After a single-service deploy Helm stores the defaults merged with the release
    configuration (images, build identity, namespace); the chart is unchanged."""
    import yaml

    chart = _assembled_chart(tmp_path)
    digest = _chart_identity().chart_digest(chart)
    merged = yaml.safe_load((chart / "values.yaml").read_text())
    merged.update(
        {
            "namespace": "nodalarc",
            "buildTag": "abc123",
            "runtimeRelease": "0.7.1+31.gabc123",
            "images": {**(merged.get("images") or {}), "ome": "registry:5000/nodalarc/ome:abc123"},
        }
    )
    _release_secrets_stub(
        tmp_path,
        [
            (1, "superseded", _release_payload(chart, digest=digest)),
            (2, "deployed", _release_payload(chart, digest=digest, values=merged)),
        ],
    )
    result = _guard(tmp_path, chart)
    assert result.stdout.strip() == "rc=0", result.stdout + result.stderr


def test_release_chart_selects_the_newest_revision_numerically(tmp_path: Path) -> None:
    """Revision 10 follows revision 9; a lexical order would compare against 9."""
    chart = _assembled_chart(tmp_path)
    digest = _chart_identity().chart_digest(chart)
    _release_secrets_stub(
        tmp_path,
        [
            (9, "superseded", _release_payload(chart, digest="sha256:stale")),
            (10, "deployed", _release_payload(chart, digest=digest)),
        ],
    )
    result = _guard(tmp_path, chart)
    assert result.stdout.strip() == "rc=0", result.stdout + result.stderr


def test_release_chart_refuses_when_the_newest_revision_is_not_deployed(tmp_path: Path) -> None:
    """A failed or pending newest revision does not establish which chart runs."""
    chart = _assembled_chart(tmp_path)
    digest = _chart_identity().chart_digest(chart)
    for status in ("failed", "pending-upgrade"):
        _release_secrets_stub(
            tmp_path,
            [
                (9, "deployed", _release_payload(chart, digest=digest)),
                (10, status, _release_payload(chart, digest=digest)),
            ],
        )
        result = _guard(tmp_path, chart)
        assert result.stdout.startswith("rc=1"), result.stdout + result.stderr
        assert f"revision 10 is '{status}', not deployed" in result.stdout


def test_release_chart_differs_when_the_shared_configuration_changed(tmp_path: Path) -> None:
    """A trimmed platform.yaml in the assembled chart must be refused, naming the file."""
    deployed = _changed_chart(tmp_path, "files/platform.yaml", "platform:\n  old: true\n")
    chart = _assembled_chart(tmp_path)
    _release_secrets_stub(
        tmp_path,
        [
            (
                1,
                "deployed",
                _release_payload(deployed, digest=_chart_identity().chart_digest(deployed)),
            )
        ],
    )
    result = _guard(tmp_path, chart)
    assert result.stdout.startswith("rc=1"), result.stdout + result.stderr
    assert "files/platform.yaml" in result.stdout
    assert "values.yaml" not in result.stdout


def test_release_chart_differs_when_default_values_changed(tmp_path: Path) -> None:
    deployed = _changed_chart(tmp_path, "values.yaml", "ome:\n  replicas: 2\n")
    chart = _assembled_chart(tmp_path)
    _release_secrets_stub(
        tmp_path,
        [
            (
                1,
                "deployed",
                _release_payload(deployed, digest=_chart_identity().chart_digest(deployed)),
            )
        ],
    )
    result = _guard(tmp_path, chart)
    assert result.stdout.startswith("rc=1"), result.stdout + result.stderr
    assert result.stdout.rstrip().endswith("values.yaml")
    assert "files/" not in result.stdout and "templates/" not in result.stdout


def test_release_chart_refuses_a_release_without_a_recorded_digest(tmp_path: Path) -> None:
    chart = _assembled_chart(tmp_path)
    _release_secrets_stub(tmp_path, [(1, "deployed", _release_payload(chart, digest=None))])
    result = _guard(tmp_path, chart)
    assert result.stdout.startswith("rc=1"), result.stdout + result.stderr
    assert "records no nodalarc.io/chart-digest" in result.stdout


def test_release_chart_refuses_when_no_release_exists(tmp_path: Path) -> None:
    chart = _assembled_chart(tmp_path)
    _release_secrets_stub(tmp_path, [])
    result = _guard(tmp_path, chart)
    assert result.stdout.startswith("rc=1"), result.stdout + result.stderr
    assert "no Helm release secrets" in result.stdout


def test_release_chart_comparison_failure_is_a_refusal(tmp_path: Path) -> None:
    """An unreadable release payload must not report a matching chart."""
    chart = _assembled_chart(tmp_path)
    _release_secrets_stub(tmp_path, [(1, "deployed", "not-a-release")])
    result = _guard(tmp_path, chart)
    assert result.stdout.startswith("rc=1"), result.stdout + result.stderr
    assert "comparison failed" in result.stdout


def test_deploy_service_refuses_to_carry_chart_changes(tmp_path: Path) -> None:
    for tool in ("docker", "helm"):
        _stub(tmp_path, tool, 'echo "must not be called: $0 $*" >&2; exit 99')
    deployed = _changed_chart(tmp_path, "files/platform.yaml", "platform:\n  old: true\n")
    _release_secrets_stub(
        tmp_path,
        [
            (
                1,
                "deployed",
                _release_payload(deployed, digest=_chart_identity().chart_digest(deployed)),
            )
        ],
    )
    result = _run(
        ["bash", "scripts/na-deploy-service.sh", "ome", "deployment/ome"],
        env={"NA_IMAGES_NO_CLUSTER": "1", "MODE": "single-node", "PROJECT_VERSION": "0+test"},
        path_dir=tmp_path,
    )
    assert result.returncode == 1, result.stderr
    assert "cannot carry chart changes" in result.stderr
    assert "files/platform.yaml" in result.stderr
    assert "must not be called" not in result.stderr


def test_vs_api_discovery_counts_request_time_toward_its_deadline(tmp_path: Path) -> None:
    """Slow requests must consume the deadline; a loop that counts only its sleeps would not stop."""
    import time

    _stub(tmp_path, "kubectl", "sleep 3\nexit 1")
    started = time.monotonic()
    result = _lib_call(
        'discover_vs_api nodalarc 4; echo "rc=$?"',
        path_dir=tmp_path,
    )
    wall = time.monotonic() - started
    assert result.stdout.rstrip().endswith("rc=1"), result.stdout + result.stderr
    assert "not reachable after" in result.stderr
    assert wall < 8, f"discovery ran {wall:.1f}s against a 4s deadline"


# --- teardown: the Node Agent cleaner on every labelled host, judged per host ---

_KUBECTL_TEARDOWN = """
case "$1" in
  get)
    case "$2" in
      namespace)
        if [ -f "$STATE_DIR/ns-deleted" ] || [ "${NS_PRESENT:-1}" != "1" ]; then exit 1; fi
        printf '%s Active 1d\\n' "$3"; exit 0 ;;
      constellationspec) exit 0 ;;
      pods)
        if printf '%s\\n' "$@" | grep -q "app=nodalarc-node-agent"; then
          printf '%s\\n' "$AGENT_ROWS"; exit 0
        fi
        exit 0 ;;
      nodes)
        if [ "${INVENTORY_OK:-1}" != "1" ]; then echo "inventory unavailable" >&2; exit 1; fi
        printf '%s\\n' "$NODE_ROWS"; exit 0 ;;
      crd) exit 1 ;;
    esac
    exit 0 ;;
  exec)
    pod="$2"
    if printf '%s\\n' "$@" | grep -q "node_agent.reconcile"; then
      cat "$STATE_DIR/$pod.out" 2>/dev/null || true
      exit "$(cat "$STATE_DIR/$pod.rc" 2>/dev/null || echo 0)"
    fi
    exit 0 ;;
  delete)
    if [ "$2" = "namespace" ]; then touch "$STATE_DIR/ns-deleted"; fi
    exit 0 ;;
esac
exit 0
"""


def _report(host: str, **overrides) -> str:
    import json

    report = {
        "host": host,
        "removed": [],
        "failed": [],
        "remaining": [],
        "verification_completed": True,
        "enumeration_error": None,
        "verification_error": None,
    }
    report.update(overrides)
    return json.dumps(report) + "\n"


def _teardown_run(
    tmp_path: Path,
    *,
    nodes: list[str],
    agents: dict[str, str],
    reports: dict[str, tuple[str, int]],
    local: tuple[str, int] | None = None,
    namespace_present: bool = True,
    inventory_ok: bool = True,
) -> tuple[subprocess.CompletedProcess[str], Path]:
    """Run scripts/na-teardown.sh with stubbed kubectl, helm, uv and ip.

    ``nodes`` are the hosts carrying the placement label; ``agents`` maps a host
    to its Node Agent pod; ``reports`` maps a pod to the cleaner's stdout and
    exit code; ``local`` is the workstation cleaner's stdout and exit code.
    """
    state = tmp_path / "state"
    state.mkdir()
    for pod, (out, rc) in reports.items():
        (state / f"{pod}.out").write_text(out)
        (state / f"{pod}.rc").write_text(str(rc))
    local_out, local_rc = local if local is not None else (_report("nodal-dev"), 0)
    (state / "local.out").write_text(local_out)
    (state / "local.rc").write_text(str(local_rc))
    _stub(tmp_path, "kubectl", _KUBECTL_TEARDOWN)
    _stub(tmp_path, "helm", 'printf "%s\\n" "$*" >> "$STATE_DIR/helm.calls"; exit 0')
    _stub(tmp_path, "uv", 'cat "$STATE_DIR/local.out"; exit "$(cat "$STATE_DIR/local.rc")"')
    _stub(tmp_path, "ip", "exit 0")
    result = _run(
        ["bash", "scripts/na-teardown.sh"],
        env={
            "STATE_DIR": str(state),
            "NS_PRESENT": "1" if namespace_present else "0",
            "INVENTORY_OK": "1" if inventory_ok else "0",
            "NODE_ROWS": "\n".join(nodes),
            "AGENT_ROWS": "\n".join(f"{host} {pod}" for host, pod in agents.items()),
            "KUBECONFIG": str(tmp_path / "kubeconfig"),
        },
        path_dir=tmp_path,
    )
    return result, state / "helm.calls"


_THREE_HOSTS = ["node01", "node02", "node03"]
_THREE_AGENTS = {"node01": "na-1", "node02": "na-2", "node03": "na-3"}
_THREE_CLEAN = {
    "na-1": (_report("node01", removed=["vx00abcd"]), 0),
    "na-2": (_report("node02"), 0),
    "na-3": (_report("node03"), 0),
}


def test_teardown_verifies_every_labelled_host_then_uninstalls(tmp_path: Path) -> None:
    result, helm_calls = _teardown_run(
        tmp_path, nodes=_THREE_HOSTS, agents=_THREE_AGENTS, reports=_THREE_CLEAN
    )

    assert result.returncode == 0, result.stderr
    for host in _THREE_HOSTS:
        assert f"{host}: verified clean host={host}" in result.stdout
    assert "node01: verified clean host=node01 removed=1" in result.stdout
    assert "local:" in result.stdout and "verified clean host=nodal-dev" in result.stdout
    assert helm_calls.exists() and "uninstall nodalarc" in helm_calls.read_text()
    assert "Teardown complete. Cluster is clean." in result.stdout


def test_teardown_refuses_before_uninstall_when_a_labelled_host_has_no_agent(
    tmp_path: Path,
) -> None:
    """Two Ready hosts report clean; the third host carries the label but is
    NotReady and has no agent pod: its state is unverified, so the teardown
    refuses before uninstalling the agents."""
    result, helm_calls = _teardown_run(
        tmp_path,
        nodes=_THREE_HOSTS,
        agents={"node01": "na-1", "node02": "na-2"},
        reports={"na-1": _THREE_CLEAN["na-1"], "na-2": _THREE_CLEAN["na-2"]},
    )

    assert result.returncode == 1
    assert "node01: verified clean" in result.stdout
    assert "node02: verified clean" in result.stdout
    assert "node03: no Node Agent pod on this host" in result.stderr
    assert "host cleanup unverified on: node03" in result.stderr
    assert "Refusing to uninstall the Node Agents" in result.stderr
    assert not helm_calls.exists()


@pytest.mark.parametrize(
    ("report", "rc", "expected"),
    [
        ("not json at all\n", 0, "node02: unparseable report"),
        ("", 0, "node02: no report"),
        (
            _report("node02", remaining=["vx00abcd"]),
            1,
            "node02: cleaner exited 1 on host=node02: failed=[] remaining=['vx00abcd']",
        ),
        (
            _report("node02", failed=[["vh00abcd", "NetlinkError: (16, 'busy')"]]),
            1,
            "node02: cleaner exited 1 on host=node02: failed=[['vh00abcd'",
        ),
        (
            _report("node02", remaining=["vx00abcd"]),
            0,
            "node02: UNCLEAN host=node02: failed=[] remaining=['vx00abcd']",
        ),
        (_report("node02"), 1, "node02: cleaner exited 1 on host=node02"),
        (_report("node02", verification_completed="yes"), 0, "node02: invalid report"),
        (
            _report("node02", verification_completed=False, verification_error="OSError: boom"),
            1,
            "verification_error='OSError: boom'",
        ),
    ],
)
def test_teardown_refuses_an_invalid_unclean_or_failed_host_report(
    tmp_path: Path, report: str, rc: int, expected: str
) -> None:
    result, helm_calls = _teardown_run(
        tmp_path,
        nodes=_THREE_HOSTS,
        agents=_THREE_AGENTS,
        reports={**_THREE_CLEAN, "na-2": (report, rc)},
    )

    assert result.returncode == 1
    assert expected in result.stderr
    assert "host cleanup unverified on: node02" in result.stderr
    assert not helm_calls.exists()


def test_teardown_refuses_when_the_host_inventory_cannot_be_read(tmp_path: Path) -> None:
    result, helm_calls = _teardown_run(
        tmp_path,
        nodes=_THREE_HOSTS,
        agents=_THREE_AGENTS,
        reports=_THREE_CLEAN,
        inventory_ok=False,
    )

    assert result.returncode == 1
    assert "could not read the Node Agent host inventory" in result.stderr
    assert "host cleanup unverified on:<host inventory unreadable>" in result.stderr
    assert not helm_calls.exists()


def test_teardown_refuses_when_the_local_cleaner_does_not_verify(tmp_path: Path) -> None:
    result, helm_calls = _teardown_run(
        tmp_path,
        nodes=_THREE_HOSTS,
        agents=_THREE_AGENTS,
        reports=_THREE_CLEAN,
        local=(_report("nodal-dev", remaining=["_na_hdeadbe"]), 1),
    )

    assert result.returncode == 1
    assert "cleaner exited 1 on host=nodal-dev: failed=[] remaining=['_na_hdeadbe']" in (
        result.stderr
    )
    assert "host cleanup unverified on: local:" in result.stderr
    assert not helm_calls.exists()


def test_teardown_without_a_namespace_claims_no_remote_verification(tmp_path: Path) -> None:
    result, helm_calls = _teardown_run(
        tmp_path,
        nodes=_THREE_HOSTS,
        agents={},
        reports={},
        namespace_present=False,
    )

    assert result.returncode == 0, result.stderr
    assert "remote host state is NOT verified here" in result.stdout
    assert "verified clean host=nodal-dev" in result.stdout
    assert "Teardown complete (namespace absent; remote host state not verified)." in result.stdout
    assert "Cluster is clean" not in result.stdout
    assert not helm_calls.exists()
