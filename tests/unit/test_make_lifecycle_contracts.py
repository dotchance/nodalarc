"""Lifecycle contract tests for Make-as-facade behavior."""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]

DOCKERFILES = (
    "images/base/Dockerfile",
    "images/frr/Dockerfile",
    "images/probe/Dockerfile",
    "services/ome/Dockerfile",
    "services/scheduler/Dockerfile",
    "services/node_agent/Dockerfile",
    "services/vs_api/Dockerfile",
    "services/nodalarc_operator/Dockerfile",
    "services/measurement/Dockerfile",
    "frontend/Dockerfile",
)

DRY_RUN_TARGETS = (
    "help",
    "all",
    "deps",
    "check-deps",
    "check-registry",
    "build",
    "build-frontends",
    "build-images",
    "_clear-build-cache",
    "ensure-base-images",
    "build-base-images",
    "build-base",
    "build-frr",
    "build-probe",
    "build-ome",
    "build-scheduler",
    "build-node-agent",
    "build-vs-api",
    "build-operator",
    "build-vf",
    "build-measurement",
    "load",
    "install",
    "reinstall",
    "session",
    "restart",
    "upgrade",
    "deploy-all",
    "deploy-ome",
    "deploy-scheduler",
    "deploy-node-agent",
    "deploy-vs-api",
    "deploy-operator",
    "deploy-vf",
    "deploy-measurement",
    "status",
    "lint",
    "lint-policy",
    "dead-code",
    "test",
    "test-backend",
    "test-frontend",
    "test-integration",
    "test-root",
    "teardown",
    "force-teardown",
    "reset-platform",
    "clean",
    "clean-deps",
    "clean-images",
    "clean-registry",
    "purge-containerd",
    "nuke",
)


def _makefile() -> str:
    return (ROOT / "Makefile").read_text()


def _target_body(name: str) -> str:
    text = _makefile()
    match = re.search(rf"^{re.escape(name)}:.*?(?=^[A-Za-z0-9_-]+:|\Z)", text, re.M | re.S)
    assert match, f"target not found: {name}"
    return match.group(0)


def test_make_targets_dry_run_cleanly() -> None:
    env = os.environ.copy()
    env.update(
        {
            "KUBECONFIG": str(ROOT / ".does-not-exist"),
            "MODE": "single-node",
            "REGISTRY_HOST": "",
        }
    )

    stale_tool_script = re.compile(r"tools/(?:na-|clean-|detect-|check_lint_policy)|tools/.*\.sh")
    for target in DRY_RUN_TARGETS:
        result = subprocess.run(
            ["make", "-n", "--no-print-directory", target],
            cwd=ROOT,
            env=env,
            text=True,
            capture_output=True,
            check=False,
        )
        output = result.stdout + result.stderr
        assert result.returncode == 0, f"{target} dry-run failed:\n{output}"
        assert not stale_tool_script.search(output), (
            f"{target} uses stale tools/ script path:\n{output}"
        )


def test_make_configuration_uses_canonical_script_paths() -> None:
    stale_tool_script = re.compile(r"tools/(?:na-|clean-|detect-|check_lint_policy)|tools/.*\.sh")
    for rel_path in ("Makefile", "config.mk.example"):
        text = (ROOT / rel_path).read_text()
        assert not stale_tool_script.search(text), f"{rel_path} uses stale tools/ script path"

    local_config = ROOT / "config.mk"
    if local_config.exists():
        config = local_config.read_text()
        assert not stale_tool_script.search(config), "config.mk uses stale tools/ script path"
        assert "bash scripts/detect-registry.sh" in config


def test_all_preserves_user_environment_and_loads_before_install() -> None:
    body = _target_body("all")
    assert "sudo make" not in body
    assert "$(MAKE) load install session" in body
    assert "make build && make load && make upgrade" in body


def test_help_documents_valid_lifecycle_transitions() -> None:
    help_body = _target_body("help")
    assert "make nuke && make all" in help_body
    assert "make build && make load && make upgrade" in help_body
    assert "make build && make load && make reinstall && make session" in help_body
    assert "install refuses existing platform state" in help_body


def test_install_and_upgrade_delegate_to_platform_script() -> None:
    install = _target_body("install")
    upgrade = _target_body("upgrade")

    assert "scripts/na-install-platform.sh" in install
    assert "ACTION=install" in install
    assert "helm uninstall" not in install
    assert "kubectl delete namespace" not in install

    assert "scripts/na-install-platform.sh" in upgrade
    assert "ACTION=upgrade" in upgrade
    assert "helm upgrade --install" not in upgrade


def test_force_teardown_is_the_only_raw_namespace_delete_target() -> None:
    makefile = _makefile()
    raw_delete_targets = [
        name
        for name in re.findall(
            r"^([A-Za-z0-9_-]+):.*?(?=^[A-Za-z0-9_-]+:|\Z)", makefile, re.M | re.S
        )
        if "kubectl delete namespace" in _target_body(name)
    ]
    assert raw_delete_targets == ["force-teardown"]


def test_cleanup_scopes_are_separate() -> None:
    clean_registry = _target_body("clean-registry")
    purge_containerd = _target_body("purge-containerd")
    nuke = _target_body("nuke")

    assert "scripts/clean-registry.sh" in clean_registry
    assert "na-purge-containerd" not in clean_registry
    assert "scripts/na-purge-containerd.sh" in purge_containerd
    assert "scripts/na-nuke.sh" in nuke


def test_lifecycle_targets_print_next_steps() -> None:
    makefile = _makefile()
    for expected in (
        "[build] Next: make load",
        "[all] Next:",
        "[force-teardown] Next: make nuke",
        "[reset-platform] Next: make build && make load && make install && make session",
    ):
        assert expected in makefile


def test_build_images_builds_every_image_load_requires_for_current_tag() -> None:
    body = _target_body("build-images")
    assert "build-base-images" in body
    assert "build-frr" in _target_body("build-base-images")
    assert "build-probe" in _target_body("build-base-images")


def test_notice_file_carries_project_attribution() -> None:
    notice = (ROOT / "NOTICE").read_text()

    assert "NodalArc" in notice
    assert ".chance (dotchance)" in notice
    assert "https://github.com/dotchance/nodalarc" in notice
    assert "THIRD_PARTY_NOTICES.md" in notice


def test_cli_surfaces_project_attribution() -> None:
    surfaces = {
        "Makefile": _makefile(),
        "scripts/na-status.sh": (ROOT / "scripts/na-status.sh").read_text(),
        "scripts/bootstrap-host.sh": (ROOT / "scripts/bootstrap-host.sh").read_text(),
        "scripts/na-teardown.sh": (ROOT / "scripts/na-teardown.sh").read_text(),
        "scripts/na-nuke.sh": (ROOT / "scripts/na-nuke.sh").read_text(),
    }

    for rel, text in surfaces.items():
        assert ".chance (dotchance)" in text, rel
        assert "https://github.com/dotchance/nodalarc" in text, rel


def test_docker_builds_pass_oci_metadata_args() -> None:
    makefile = _makefile()

    assert "BUILD_DATE ?=" in makefile
    assert "PROJECT_VERSION ?=" in makefile
    assert "DOCKER_BUILD_METADATA_ARGS" in makefile
    assert "--build-arg PROJECT_VERSION=$(PROJECT_VERSION)" in makefile
    assert "--build-arg VCS_REF=$(GIT_SHA)" in makefile
    assert "--build-arg BUILD_DATE=$(BUILD_DATE)" in makefile
    for target in (
        "build-base",
        "build-frr",
        "build-probe",
        "build-ome",
        "build-scheduler",
        "build-node-agent",
        "build-vs-api",
        "build-operator",
        "build-measurement",
        "build-vf",
    ):
        assert "$(DOCKER_BUILD_METADATA_ARGS)" in _target_body(target), target


def test_dockerfiles_have_oci_attribution_labels() -> None:
    required = (
        "org.opencontainers.image.title",
        "org.opencontainers.image.description",
        'org.opencontainers.image.vendor=".chance (dotchance)"',
        'org.opencontainers.image.authors=".chance (dotchance)"',
        'org.opencontainers.image.source="https://github.com/dotchance/nodalarc"',
        'org.opencontainers.image.url="https://nodal.asmolab.net"',
        "org.opencontainers.image.documentation",
        'org.opencontainers.image.licenses="NodalArc Source Available License 1.0"',
        'org.opencontainers.image.version="${PROJECT_VERSION}"',
        'org.opencontainers.image.revision="${VCS_REF}"',
        'org.opencontainers.image.created="${BUILD_DATE}"',
        "ENV NODALARC_VERSION=${PROJECT_VERSION}",
        "NODALARC_BUILD_REVISION=${VCS_REF}",
        "NODALARC_BUILD_DATE=${BUILD_DATE}",
    )

    for rel in DOCKERFILES:
        text = (ROOT / rel).read_text()
        assert "ARG PROJECT_VERSION=0+unknown" in text, rel
        assert "ARG VCS_REF=unknown" in text, rel
        assert "ARG BUILD_DATE=unknown" in text, rel
        for expected in required:
            assert expected in text, f"{rel} missing {expected}"


def test_helm_templates_do_not_have_duplicate_env_blocks_or_nats_box_latest() -> None:
    for rel in ("deploy/helm/templates/operator-deployment.yaml",):
        text = (ROOT / rel).read_text()
        assert text.count("          env:\n") == 1, rel

    rendered_templates = "\n".join(
        p.read_text() for p in (ROOT / "deploy/helm/templates").glob("*.yaml")
    )
    assert "natsio/nats-box:latest" not in rendered_templates


def test_orchestrator_rbac_can_list_substrate_status_configmaps() -> None:
    template = (ROOT / "deploy/helm/templates/management-network.yaml").read_text()
    match = re.search(
        r'resources:\s+\["configmaps"\]\s+'
        r"# Scheduler gates startup on substrate status ConfigMaps selected by label\.\s+"
        r"verbs:\s+\[([^\]]+)\]",
        template,
    )
    assert match, "orchestrator ConfigMap RBAC rule not found"

    verbs = {verb.strip().strip('"') for verb in match.group(1).split(",")}
    assert {"get", "list", "create", "update", "patch"}.issubset(verbs)


def test_nats_networkpolicy_allows_host_network_node_cidrs() -> None:
    template = (ROOT / "deploy/helm/templates/nats-networkpolicy.yaml").read_text()
    values = (ROOT / "deploy/helm/values.yaml").read_text()

    assert "hostNetworkCIDRs" in values
    assert ".Values.nats.networkPolicy.hostNetworkCIDRs" in template
    assert "ipBlock:" in template


def test_host_network_node_agents_use_host_reachable_nats_endpoint() -> None:
    values = (ROOT / "deploy/helm/values.yaml").read_text()
    nats = (ROOT / "deploy/helm/templates/nats-deployment.yaml").read_text()
    node_agent = (ROOT / "deploy/helm/templates/node-agent-daemonset.yaml").read_text()
    nats_init = (ROOT / "deploy/helm/templates/_nats-init.yaml").read_text()

    assert "hostNetworkHost" in values
    assert "hostPort: {{ .Values.nats.clientPort }}" in nats
    assert '"hostNetwork" true' in node_agent
    assert "NODALARC_NATS_URL" in node_agent
    assert "$natsHost" in nats_init


def test_node_agent_can_load_host_mpls_kernel_modules() -> None:
    node_agent = (ROOT / "deploy/helm/templates/node-agent-daemonset.yaml").read_text()
    dockerfile = (ROOT / "services/node_agent/Dockerfile").read_text()

    assert "kmod" in dockerfile
    assert "util-linux" in dockerfile
    assert "name: host-modules" in node_agent
    assert "mountPath: /lib/modules" in node_agent
    assert "path: /lib/modules" in node_agent


def test_remote_containerd_purge_uses_node_agent_runtime_socket_contract() -> None:
    script = (ROOT / "scripts/na-purge-containerd.sh").read_text()
    node_agent = (ROOT / "deploy/helm/templates/node-agent-daemonset.yaml").read_text()
    dockerfile = (ROOT / "services/node_agent/Dockerfile").read_text()

    assert "CONTAINER_RUNTIME_ENDPOINT" in node_agent
    assert "/run/k3s/containerd/containerd.sock" in node_agent
    assert 'crictl --runtime-endpoint "$runtime" images' in script
    assert 'crictl --runtime-endpoint "$runtime" rmi' in script
    assert "k3s crictl" not in script
    assert "nsenter --target 1" not in script
    assert "crictl-${CRICTL_VERSION}" in dockerfile


def test_required_nats_streams_are_persistent() -> None:
    ome = (ROOT / "deploy/helm/templates/ome-deployment.yaml").read_text()

    assert "nats stream add NODALARC_OPS" in ome
    assert "nats stream add NODALARC_DEBUG" in ome
    assert "nats stream add NODALARC_SESSION \\" in ome
    assert ome.count("nats stream add NODALARC_SESSION") == 1
    assert "--storage=memory" not in ome


def test_constellationspec_status_schema_preserves_runtime_identity_fields() -> None:
    crd = yaml.safe_load((ROOT / "deploy/helm/crds/constellationspec.yaml").read_text())
    status_props = crd["spec"]["versions"][0]["schema"]["openAPIV3Schema"]["properties"]["status"][
        "properties"
    ]

    for field in ("sessionName", "sessionRunId", "platformHash", "runtimeHash"):
        assert status_props[field]["type"] == "string"
