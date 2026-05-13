"""Lifecycle contract tests for Make-as-facade behavior."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _makefile() -> str:
    return (ROOT / "Makefile").read_text()


def _target_body(name: str) -> str:
    text = _makefile()
    match = re.search(rf"^{re.escape(name)}:.*?(?=^[A-Za-z0-9_-]+:|\Z)", text, re.M | re.S)
    assert match, f"target not found: {name}"
    return match.group(0)


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

    assert "tools/na-install-platform.sh" in install
    assert "ACTION=install" in install
    assert "helm uninstall" not in install
    assert "kubectl delete namespace" not in install

    assert "tools/na-install-platform.sh" in upgrade
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

    assert "tools/clean-registry.sh" in clean_registry
    assert "na-purge-containerd" not in clean_registry
    assert "tools/na-purge-containerd.sh" in purge_containerd
    assert "tools/na-nuke.sh" in nuke


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
    assert "build-fwd" in _target_body("build-base-images")


def test_helm_templates_do_not_have_duplicate_env_blocks_or_nats_box_latest() -> None:
    for rel in (
        "deploy/helm/templates/operator-deployment.yaml",
        "deploy/helm/templates/nodalpath-deployment.yaml",
    ):
        text = (ROOT / rel).read_text()
        assert text.count("          env:\n") == 1, rel

    rendered_templates = "\n".join(
        p.read_text() for p in (ROOT / "deploy/helm/templates").glob("*.yaml")
    )
    assert "natsio/nats-box:latest" not in rendered_templates


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


def test_required_nats_streams_are_persistent() -> None:
    ome = (ROOT / "deploy/helm/templates/ome-deployment.yaml").read_text()

    assert "nats stream add NODALARC_OPS" in ome
    assert "nats stream add NODALARC_DEBUG" in ome
    assert "nats stream add NODALARC_SESSION \\" in ome
    assert ome.count("nats stream add NODALARC_SESSION") == 1
    assert "--storage=memory" not in ome
