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
    "typecheck",
    "format-diff",
    "generate-contracts",
    "check-contracts",
    "render-helm",
    "lint-helm",
    "check-helm",
    "dead-code",
    "test",
    "test-backend",
    "test-frontend",
    "test-integration",
    "test-runtime-matrix",
    "test-builder-e2e",
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


def _make_env(**overrides: str) -> dict[str, str]:
    env = os.environ.copy()
    env.update(
        {
            "KUBECONFIG": str(ROOT / ".does-not-exist"),
            "MODE": "single-node",
            "REGISTRY_HOST": "",
        }
    )
    env.update(overrides)
    return env


def _dry_run_make(target: str, **env_overrides: str) -> str:
    result = subprocess.run(
        ["make", "-n", "--no-print-directory", target],
        cwd=ROOT,
        env=_make_env(**env_overrides),
        text=True,
        capture_output=True,
        check=False,
    )
    output = result.stdout + result.stderr
    assert result.returncode == 0, f"{target} dry-run failed:\n{output}"
    return output


def _help_output() -> str:
    result = subprocess.run(
        ["make", "--no-print-directory", "help"],
        cwd=ROOT,
        env=_make_env(),
        text=True,
        capture_output=True,
        check=False,
    )
    output = result.stdout + result.stderr
    assert result.returncode == 0, output
    return re.sub(r"\x1b\[[0-9;]*m", "", output)


def test_make_targets_dry_run_cleanly() -> None:
    stale_tool_script = re.compile(r"tools/(?:na-|clean-|detect-|check_lint_policy)|tools/.*\.sh")
    for target in DRY_RUN_TARGETS:
        output = _dry_run_make(target)
        assert not stale_tool_script.search(output), (
            f"{target} uses stale tools/ script path:\n{output}"
        )


def test_make_configuration_uses_canonical_script_paths() -> None:
    stale_tool_script = re.compile(r"tools/(?:na-|clean-|detect-|check_lint_policy)|tools/.*\.sh")
    for rel_path in ("Makefile", "config.mk.example"):
        text = (ROOT / rel_path).read_text()
        assert not stale_tool_script.search(text), f"{rel_path} uses stale tools/ script path"
        assert "configs/sessions" not in text, f"{rel_path} references retired session root"
        assert "catalog/nodalarc/sessions/earth-leo-simple.yaml" in text

    local_config = ROOT / "config.mk"
    if local_config.exists():
        config = local_config.read_text()
        assert not stale_tool_script.search(config), "config.mk uses stale tools/ script path"
        assert "bash scripts/detect-registry.sh" in config


def test_all_preserves_user_environment_and_loads_before_install() -> None:
    output = _dry_run_make("all")

    assert "sudo make" not in output
    assert "make load install session" in output
    assert "ACTION=install" in output
    assert "scripts/na-load-images.sh" in output
    assert output.index("scripts/na-load-images.sh") < output.index("ACTION=install")
    assert "make build && make load && make upgrade" in output


def test_help_documents_valid_lifecycle_transitions() -> None:
    output = _help_output()

    assert "make nuke && make all" in output
    assert "make build && make load && make upgrade" in output
    assert "make build && make load && make reinstall && make session" in output
    assert "install refuses existing platform state" in output


def test_install_and_upgrade_delegate_to_platform_script() -> None:
    install = _dry_run_make("install")
    reinstall = _dry_run_make("reinstall")
    upgrade = _dry_run_make("upgrade")

    assert "ACTION=install" in install
    assert "bash scripts/na-install-platform.sh" in install
    assert "PROJECT_VERSION='" in install
    assert "helm uninstall" not in install
    assert "kubectl delete namespace" not in install

    assert "ACTION=reinstall" in reinstall
    assert "PROJECT_VERSION='" in reinstall

    assert "ACTION=upgrade" in upgrade
    assert "bash scripts/na-install-platform.sh" in upgrade
    assert "PROJECT_VERSION='" in upgrade
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
    clean_registry = _dry_run_make("clean-registry")
    purge_containerd = _dry_run_make("purge-containerd")
    nuke = _dry_run_make("nuke")

    assert "scripts/clean-registry.sh" in clean_registry
    assert "na-purge-containerd" not in clean_registry
    assert "scripts/na-purge-containerd.sh" in purge_containerd
    assert "scripts/na-nuke.sh" in nuke


def test_lifecycle_targets_print_next_steps() -> None:
    expected_by_target = {
        "build": "[build] Next: make load",
        "all": "[all] Next:",
        "force-teardown": "[force-teardown] Next: make nuke",
        "reset-platform": "[reset-platform] Next: make build && make load && make install && make session",
    }

    for target, expected in expected_by_target.items():
        assert expected in _dry_run_make(target)


def test_build_images_builds_every_image_load_requires_for_current_tag() -> None:
    output = _dry_run_make("build-images")

    for image in (
        "base",
        "frr",
        "probe",
        "ome",
        "scheduler",
        "node-agent",
        "vs-api",
        "operator",
        "vf",
    ):
        assert f"image-for {image}" in output, image
    assert "image-for measurement" in _dry_run_make("deploy-measurement")


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
    assert "bash scripts/na-project-version.sh" in makefile
    assert r"sed -n 's/^version = \"\(.*\)\"/\1/p' pyproject.toml" not in makefile
    assert "uv pip install -e lib/" not in makefile
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
        output = _dry_run_make(target)
        assert "--build-arg PROJECT_VERSION=" in output, target
        assert "--build-arg VCS_REF=" in output, target
        assert "--build-arg BUILD_DATE=" in output, target


def test_dockerfiles_have_oci_attribution_labels() -> None:
    required = (
        "org.opencontainers.image.title",
        "org.opencontainers.image.description",
        'org.opencontainers.image.vendor=".chance (dotchance)"',
        'org.opencontainers.image.authors=".chance (dotchance)"',
        'org.opencontainers.image.source="https://github.com/dotchance/nodalarc"',
        'org.opencontainers.image.url="https://nodal.asmolab.net"',
        "org.opencontainers.image.documentation",
        'org.opencontainers.image.licenses="Apache-2.0"',
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


def test_runtime_images_include_exact_catalog_loader_files() -> None:
    dockerfiles = {
        name: (ROOT / f"services/{name}/Dockerfile").read_text()
        for name in ("ome", "scheduler", "vs_api", "nodalarc_operator")
    }

    for name, dockerfile in dockerfiles.items():
        assert "COPY lib/ lib/" in dockerfile, name
        assert "COPY catalog/ catalog/" in dockerfile, name
        assert "kubernetes" in dockerfile, name
    assert "COPY services/ome/ services/ome/" in dockerfiles["ome"]
    assert "COPY services/scheduler/ services/scheduler/" in dockerfiles["scheduler"]
    assert "COPY services/vs_api/ services/vs_api/" in dockerfiles["vs_api"]
    assert (
        "COPY services/nodalarc_operator/ services/nodalarc_operator/"
        in dockerfiles["nodalarc_operator"]
    )
    for contract in ("docs/ops/configuration-grammar.md",):
        assert contract in dockerfiles["vs_api"]


def test_helm_templates_do_not_have_duplicate_env_blocks_or_nats_box_latest() -> None:
    for rel in ("deploy/helm/templates/operator-deployment.yaml",):
        text = (ROOT / rel).read_text()
        assert text.count("          env:\n") == 1, rel

    rendered_templates = "\n".join(
        p.read_text() for p in (ROOT / "deploy/helm/templates").glob("*.yaml")
    )
    assert "natsio/nats-box:latest" not in rendered_templates


def test_platform_config_mounts_force_pod_rollout_on_config_change() -> None:
    checksum = 'checksum/platform-config: {{ tpl (.Files.Get "files/platform.yaml") . | sha256sum | quote }}'
    platform_mount = "mountPath: /etc/nodalarc/platform.yaml"

    checked = []
    for path in sorted((ROOT / "deploy/helm/templates").glob("*.yaml")):
        text = path.read_text()
        if platform_mount not in text:
            continue
        rel = str(path.relative_to(ROOT))
        checked.append(rel)
        assert checksum in text, f"{rel} mounts platform config without rollout checksum"

    assert checked == [
        "deploy/helm/templates/node-agent-daemonset.yaml",
        "deploy/helm/templates/ome-deployment.yaml",
        "deploy/helm/templates/operator-deployment.yaml",
        "deploy/helm/templates/scheduler-deployment.yaml",
        "deploy/helm/templates/vs-api-deployment.yaml",
    ]


def test_helm_namespace_is_one_runtime_authority() -> None:
    installer = (ROOT / "scripts/na-install-platform.sh").read_text()
    platform_file = (ROOT / "deploy/helm/files/platform.yaml").read_text()
    platform_configmap = (ROOT / "deploy/helm/templates/platform-configmaps.yaml").read_text()
    operator_template = (ROOT / "deploy/helm/templates/operator-deployment.yaml").read_text()
    operator_dockerfile = (ROOT / "services/nodalarc_operator/Dockerfile").read_text()

    assert '"--set-string=namespace=$NAMESPACE"' in installer
    assert '"--set-string=runtimeRelease=$PROJECT_VERSION"' in installer
    assert "|buildTag|runtimeRelease|namespace)" in installer
    assert 'kubernetes_namespace: "{{ .Values.namespace }}"' in platform_file
    assert 'tpl (.Files.Get "files/platform.yaml") .' in platform_configmap
    assert "- {{ .Values.namespace | quote }}" in operator_template
    assert 'ENTRYPOINT ["kopf", "run", "-m", "nodalarc_operator"]' in operator_dockerfile


def test_operator_mutation_rbac_is_namespace_bound_and_discovery_is_read_only() -> None:
    template = (ROOT / "deploy/helm/templates/operator-rbac.yaml").read_text()
    role_match = re.search(r"kind: Role\n(?P<body>.*?)(?=\n---\n)", template, re.DOTALL)
    cluster_role_match = re.search(
        r"kind: ClusterRole\n(?P<body>.*?)(?=\n---\n)",
        template,
        re.DOTALL,
    )

    assert role_match is not None
    assert cluster_role_match is not None
    role = role_match.group("body")
    cluster_role = cluster_role_match.group("body")

    assert "namespace: {{ .Values.namespace }}" in role
    for resource in (
        "constellationspecs",
        "constellationspecs/status",
        "events",
        "pods",
        "pods/exec",
        "pods/proxy",
        "configmaps",
        "secrets",
        "deployments",
    ):
        assert f'resources: ["{resource}"]' in role
        assert f'resources: ["{resource}"]' not in cluster_role

    assert set(re.findall(r'resources: \["([^"]+)"\]', cluster_role)) == {
        "customresourcedefinitions",
        "namespaces",
        "nodes",
    }
    for mutation in ("create", "update", "patch", "delete"):
        assert f'"{mutation}"' not in cluster_role


def test_operator_cluster_discovery_names_are_release_and_namespace_qualified() -> None:
    template = (ROOT / "deploy/helm/templates/operator-rbac.yaml").read_text()

    assert 'printf "nodalarc-operator-discovery-%s-%s" .Release.Name .Values.namespace' in template
    assert "name: {{ $operatorDiscoveryName | quote }}" in template
    assert "name: {{ $operatorDiscoveryBindingName | quote }}" in template
    assert "kind: ClusterRole\nmetadata:\n  name: nodalarc-operator\n" not in template
    assert "kind: ClusterRoleBinding\nmetadata:\n  name: nodalarc-operator\n" not in template


def test_operator_configmap_rbac_keeps_runtime_mutation_without_watch() -> None:
    template = (ROOT / "deploy/helm/templates/operator-rbac.yaml").read_text()
    match = re.search(
        r'resources:\s+\["configmaps"\]\s+verbs:\s+\[([^\]]+)\]',
        template,
    )
    assert match, "Operator ConfigMap RBAC rule not found"

    verbs = {verb.strip().strip('"') for verb in match.group(1).split(",")}
    assert verbs == {"create", "update", "delete", "get", "list"}


def test_orchestrator_cluster_discovery_names_are_release_and_namespace_qualified() -> None:
    template = (ROOT / "deploy/helm/templates/management-network.yaml").read_text()

    assert (
        'printf "nodalarc-orchestrator-cluster-%s-%s" .Release.Name .Values.namespace' in template
    )
    assert "name: {{ $orchestratorClusterName | quote }}" in template
    assert "name: {{ $orchestratorClusterBindingName | quote }}" in template
    assert "name: nodalarc-orchestrator-cluster\n" not in template


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
    assert verbs == {"get", "list"}


def test_ome_rbac_can_read_catalog_upload_configmaps() -> None:
    template = (ROOT / "deploy/helm/templates/ome-rbac.yaml").read_text()
    match = re.search(
        r'resources:\s+\["configmaps"\]\s+verbs:\s+\[([^\]]+)\]',
        template,
    )
    assert match, "OME ConfigMap RBAC rule not found"

    verbs = {verb.strip().strip('"') for verb in match.group(1).split(",")}
    assert verbs == {"get", "list"}


def test_vs_api_has_separate_upload_and_lifecycle_rbac() -> None:
    template = (ROOT / "deploy/helm/templates/management-network.yaml").read_text()
    deployment = (ROOT / "deploy/helm/templates/vs-api-deployment.yaml").read_text()
    role = template.split("name: nodalarc-vs-api", 2)[2]
    match = re.search(
        r'resources:\s+\["configmaps"\]\s+verbs:\s+\[([^\]]+)\]',
        role,
    )
    assert match, "VS-API ConfigMap RBAC rule not found"
    verbs = {verb.strip().strip('"') for verb in match.group(1).split(",")}

    assert verbs == {"create", "list", "delete"}
    assert "serviceAccountName: nodalarc-vs-api" in deployment
    assert "name: nodalarc-vs-api" in template


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


def test_platform_install_renders_versioned_helm_chart() -> None:
    script = (ROOT / "scripts/na-install-platform.sh").read_text()
    renderer = (ROOT / "scripts/na-render-helm-chart.sh").read_text()
    chart_template = (ROOT / "deploy/helm/Chart.yaml.in").read_text()

    assert "render_chart_if_needed" in script
    assert "scripts/na-render-helm-chart.sh" in script
    assert "Chart.yaml.in" in renderer
    assert "PROJECT_VERSION" in renderer
    assert "version: @PROJECT_VERSION@" in chart_template


def test_constellationspec_status_schema_preserves_runtime_identity_fields() -> None:
    crd = yaml.safe_load((ROOT / "deploy/helm/crds/constellationspec.yaml").read_text())
    status_props = crd["spec"]["versions"][0]["schema"]["openAPIV3Schema"]["properties"]["status"][
        "properties"
    ]

    for field in (
        "sessionName",
        "sessionRunId",
        "documentDigest",
        "closureDigest",
        "resolvedSemanticDigest",
        "runtimeRelease",
        "runtimeBuild",
        "platformHash",
        "runtimeHash",
    ):
        assert status_props[field]["type"] == "string"
    assert "catalogUploadId" not in status_props
    assert "catalogManifestUid" not in status_props


def test_runtime_proof_services_share_product_release_identity() -> None:
    release_env = (
        '- name: NODALARC_RELEASE\n              value: {{ required "runtimeRelease is required" '
        ".Values.runtimeRelease | quote }}"
    )

    for template in (
        "operator-deployment.yaml",
        "ome-deployment.yaml",
        "scheduler-deployment.yaml",
        "vs-api-deployment.yaml",
    ):
        deployment = (ROOT / "deploy/helm/templates" / template).read_text()
        assert release_env in deployment
        assert ".Chart.AppVersion" not in deployment
        assert "value: {{ .Release.Name | quote }}" not in deployment


def test_runtime_services_mount_one_atomic_session_configmap_directory() -> None:
    for template in ("ome-deployment.yaml", "scheduler-deployment.yaml"):
        deployment = (ROOT / "deploy/helm/templates" / template).read_text()
        assert "mountPath: /etc/nodalarc/session-config" in deployment
        assert "name: nodalarc-session" in deployment
        assert "subPath: session.yaml" not in deployment
        assert "subPath: session_run_id" not in deployment


def test_constellationspec_catalog_upload_schema_is_required_and_exact() -> None:
    crd = yaml.safe_load((ROOT / "deploy/helm/crds/constellationspec.yaml").read_text())
    spec_schema = crd["spec"]["versions"][0]["schema"]["openAPIV3Schema"]["properties"]["spec"]
    upload = spec_schema["properties"]["catalogUpload"]

    assert set(spec_schema["required"]) == {"sessionYaml", "catalogUpload"}
    assert spec_schema["additionalProperties"] is False
    assert upload["additionalProperties"] is False
    assert "x-kubernetes-preserve-unknown-fields" not in spec_schema
    assert "x-kubernetes-preserve-unknown-fields" not in upload
    assert set(upload["required"]) == {"upload_id", "closure_digest", "file_count"}
    assert set(upload["properties"]) == set(upload["required"])
    assert upload["properties"]["upload_id"]["pattern"].startswith("^[a-z0-9]")
    assert upload["properties"]["closure_digest"]["pattern"] == "^sha256:[0-9a-f]{64}$"
    assert upload["properties"]["file_count"]["minimum"] == 0


def test_make_session_uses_the_reviewed_vs_api_catalog_switch_path() -> None:
    import subprocess

    script_path = ROOT / "scripts/na-session.sh"
    script = script_path.read_text()

    subprocess.run(["bash", "-n", str(script_path)], check=True)
    assert "/api/v1/sessions" in script
    assert "/api/v1/sessions/yaml" in script
    assert "/api/v1/sessions/switch" in script
    assert "/api/v1/session-transitions/" in script
    assert "CatalogClosureCollector.collect" in script
    assert "kubectl apply" not in script
    assert "kubectl delete constellationspec" not in script
    assert "sessionYaml: |" not in script


def test_platform_upgrade_applies_and_verifies_crd_before_helm() -> None:
    script = (ROOT / "scripts/na-install-platform.sh").read_text()

    apply_call = 'apply_constellationspec_crd "$HELM_CHART"'
    assert 'kubectl apply -f "$crd_path"' in script
    assert "--for=condition=Established" in script
    assert "spec.properties.catalogUpload.type" in script
    assert "catalogUpload.properties.upload_id.type" in script
    assert "catalogUpload.properties.closure_digest.type" in script
    assert "catalogUpload.properties.file_count.type" in script
    assert "status.properties.runtimeRelease.type" in script
    assert "status.properties.runtimeBuild.type" in script
    assert script.index(apply_call) < script.index('helm install "$HELM_RELEASE"')
    assert script.index(apply_call) < script.index('helm upgrade "$HELM_RELEASE"')


def test_platform_upgrade_has_no_legacy_session_migration_preflight() -> None:
    script = (ROOT / "scripts/na-install-platform.sh").read_text()

    image_preflight = 'bash "$ROOT_DIR/scripts/na-image-preflight.sh"'
    crd_apply = 'apply_constellationspec_crd "$HELM_CHART"'
    assert "upgrade_preflight_cli" not in script
    assert "preflight_active_sessions_for_upgrade" not in script
    assert "switch or migrate" not in script
    assert script.index(image_preflight) < script.index(crd_apply)


def test_platform_wait_requires_complete_deployment_and_daemonset_rollouts() -> None:
    script = (ROOT / "scripts/na-install-platform.sh").read_text()

    assert "custom-columns=GEN:.metadata.generation,OBS:.status.observedGeneration" in script
    assert "TOTAL:.status.replicas" in script
    assert "TERM:.status.terminatingReplicas" in script
    assert "$1 == $2 && $3 == $4 && $3 == $5 && $3 == $6 && $3 == $7" in script
    assert ".status.currentNumberScheduled" in script
    assert ".status.updatedNumberScheduled" in script
    assert ".status.numberAvailable" in script
    assert ".status.numberMisscheduled" in script
    assert '"$ds_generation" -eq "$ds_observed"' in script


def test_status_uses_workload_generation_readiness_not_pod_phase_counts() -> None:
    script = (ROOT / "scripts/na-status.sh").read_text()

    assert "status.get('sessionName')" in script
    assert "yaml.safe_load" not in script
    assert "NAME:.metadata.name,GEN:.metadata.generation,OBS:.status.observedGeneration" in script
    assert "TOTAL:.status.replicas" in script
    assert "TERM:.status.terminatingReplicas" in script
    assert "$2 == $3 && $4 == $5 && $4 == $6 && $4 == $7 && $4 == $8" in script
    assert ".status.currentNumberScheduled" in script
    assert ".status.updatedNumberScheduled" in script
    assert ".status.numberMisscheduled" in script
    assert "deployments converged" in script
    assert "retained rollout replicas" in script


def test_image_tags_are_content_addressed() -> None:
    """Image identity must equal content identity: a dirty tree must never
    share a tag with its HEAD commit, or the registry serves stale code under
    a current name and concurrent builds race (this happened; it cost hours)."""
    import subprocess
    import tempfile
    from pathlib import Path

    script = Path(__file__).resolve().parents[2] / "scripts" / "na-tag.sh"

    with tempfile.TemporaryDirectory() as tmp:
        repo = Path(tmp)
        env = {"PATH": "/usr/bin:/bin", "HOME": tmp, "GIT_CONFIG_GLOBAL": "/dev/null"}

        def git(*args: str) -> None:
            subprocess.run(
                ["git", "-c", "user.name=t", "-c", "user.email=t@t", *args],
                cwd=repo,
                env=env,
                check=True,
                capture_output=True,
            )

        def tag() -> str:
            return subprocess.run(
                ["bash", str(script)],
                cwd=repo,
                env=env,
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()

        git("init", "-q")
        (repo / "a.txt").write_text("one\n")
        git("add", "a.txt")
        git("commit", "-qm", "first")

        clean = tag()
        assert "-" not in clean, f"clean tree must tag as bare sha, got {clean}"

        (repo / "a.txt").write_text("two\n")
        dirty_one = tag()
        assert dirty_one.startswith(clean + "-"), dirty_one
        assert dirty_one != clean, "dirty tree must not share the clean tag"

        (repo / "a.txt").write_text("three\n")
        dirty_two = tag()
        assert dirty_two != dirty_one, "different dirty trees must not share a tag"

        # Untracked files change builds too.
        (repo / "a.txt").write_text("one\n")
        assert tag() == clean
        (repo / "new.txt").write_text("x\n")
        assert tag() != clean, "untracked files must dirty the tag"


def test_deploy_script_moves_helm_reference_and_verifies_digest() -> None:
    """A deploy must MOVE the Helm-owned image reference and PROVE the
    cluster runs what was pushed. `rollout restart` re-pulls whatever tag the
    spec already pins - after any commit that silently redeploys old code."""
    from pathlib import Path

    script = (Path(__file__).resolve().parents[2] / "scripts" / "na-deploy-service.sh").read_text()
    code_lines = [line for line in script.splitlines() if not line.strip().startswith("#")]
    code = "\n".join(code_lines)
    assert "helm upgrade" in code
    assert "--reuse-values" in code
    assert "runtimeRelease" not in code
    assert "rollout restart" not in code, (
        "deploys must move the Helm reference, never bounce pods onto a pinned tag"
    )
    assert "pushed_digest" in code and "imageID" in code, (
        "deploys must verify the running digest against the pushed digest"
    )


def test_session_refuses_drifted_platform_unless_forced() -> None:
    """make session must not deploy a session onto a platform that is not
    running this tree's images - testing against stale services looks like
    testing your change when it is not."""
    from pathlib import Path

    makefile = (Path(__file__).resolve().parents[2] / "Makefile").read_text()
    session_block = makefile.split("session:")[1].split("\n\n")[0]
    assert "na-drift.sh --check" in session_block
    assert "FORCE_SESSION" in session_block
