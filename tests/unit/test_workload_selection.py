# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""Explicit selection: write-free preparation, terminal failures, no fallback."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import kubernetes.client
import pytest
import yaml
from nodalarc.content_identity import SHA256_DIGEST_PATTERN
from nodalarc.models.resolved_session import ResolvedSession
from nodalarc.workloads.refs import (
    ImplementationBindingRef,
    SelectionPairError,
    selection_ref_from_spec,
)
from nodalarc.workloads.source import DirectoryPackageSource
from nodalarc_operator.session_deployer import _ensure_immutable_configmap
from nodalarc_operator.workloads.selection import (
    WorkloadSelectionError,
    prepare_workload_selection,
)

from tests.catalog_session_fixtures import (
    build_catalog_session_fixture,
    resolve_catalog_session,
)

ROOT = Path(__file__).resolve().parents[2]
PACKAGE_ROOT = ROOT / "configs" / "workloads"
BINDING = "nodalarc:bindings/frr-observer-everywhere.yaml"
OWNER_REF = {"kind": "ConstellationSpec", "name": "s", "uid": "owner-uid-1"}


@pytest.fixture(scope="module")
def resolved() -> ResolvedSession:
    fixture = build_catalog_session_fixture(
        name="workload-selection",
        constellation={},
        ground_stations={"stations": [{}, {}]},
    )
    return resolve_catalog_session(fixture)


def _package_digest() -> str:
    return (
        DirectoryPackageSource(PACKAGE_ROOT).load(ImplementationBindingRef(BINDING)).package_digest
    )


def _selection_ref(digest: str | None = None):
    return selection_ref_from_spec(
        {
            "implementationBindingRef": BINDING,
            "implementationPackageDigest": digest or _package_digest(),
        }
    )


def _rendered(resolved: ResolvedSession) -> dict[str, dict[str, str]]:
    return {
        node.node_id: {"frr.conf": f"hostname {node.node_id}\n", "_config_version": "abc"}
        for node in resolved.nodes
    }


def test_selection_pair_is_both_or_neither() -> None:
    assert selection_ref_from_spec({}) is None
    with pytest.raises(SelectionPairError, match="together"):
        selection_ref_from_spec({"implementationBindingRef": BINDING})
    with pytest.raises(SelectionPairError, match="together"):
        selection_ref_from_spec({"implementationPackageDigest": "sha256:" + "a" * 64})
    with pytest.raises(SelectionPairError, match="invalid"):
        selection_ref_from_spec(
            {
                "implementationBindingRef": "nodalarc:profiles/not-a-binding.yaml",
                "implementationPackageDigest": "sha256:" + "a" * 64,
            }
        )


def test_crd_schema_patterns_match_the_typed_authority() -> None:
    crd = yaml.safe_load((ROOT / "deploy" / "helm" / "crds" / "constellationspec.yaml").read_text())
    properties = crd["spec"]["versions"][0]["schema"]["openAPIV3Schema"]["properties"]["spec"][
        "properties"
    ]
    assert (
        properties["implementationBindingRef"]["pattern"]
        == ImplementationBindingRef.json_schema_pattern()
    )
    assert properties["implementationPackageDigest"]["pattern"] == SHA256_DIGEST_PATTERN


def test_absent_selection_is_builtin_default_path(resolved: ResolvedSession) -> None:
    result = prepare_workload_selection(
        None, resolved, {}, namespace="nodalarc", owner_ref=OWNER_REF, package_root=PACKAGE_ROOT
    )
    assert result is None


def test_prepared_selection_composes_every_node(
    resolved: ResolvedSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("WORKLOAD_DEV_IMAGE_OVERRIDES", raising=False)
    selected = prepare_workload_selection(
        _selection_ref(),
        resolved,
        _rendered(resolved),
        namespace="nodalarc",
        owner_ref=OWNER_REF,
        package_root=PACKAGE_ROOT,
    )
    assert selected is not None
    assert selected.identity == f"{BINDING}@{_package_digest()}"
    assert set(selected.composed) == {node.node_id for node in resolved.nodes}
    sample = next(iter(selected.composed.values()))
    assert [c.name for c in sample.composition.containers] == ["frr", "observer"]
    # The transitional producer delivered rendered config as plan artifacts.
    assert sample.artifact_config_map is not None
    plan_keys = [k for k in sample.artifact_config_map.binary_data if k.startswith("p-")]
    assert plan_keys


def test_digest_mismatch_is_terminal(resolved: ResolvedSession) -> None:
    with pytest.raises(WorkloadSelectionError, match="desired digest"):
        prepare_workload_selection(
            _selection_ref("sha256:" + "d" * 64),
            resolved,
            _rendered(resolved),
            namespace="nodalarc",
            owner_ref=OWNER_REF,
            package_root=PACKAGE_ROOT,
        )


def test_dev_image_override_applies_and_is_explicit(
    resolved: ResolvedSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    zeros = "0" * 64
    monkeypatch.setenv(
        "WORKLOAD_DEV_IMAGE_OVERRIDES",
        f'{{"registry.example/nodalarc/frr@sha256:{zeros}": "node01:5000/nodalarc/frr:dev"}}',
    )
    monkeypatch.setenv("IMAGE_PULL_POLICY", "Always")
    selected = prepare_workload_selection(
        _selection_ref(),
        resolved,
        _rendered(resolved),
        namespace="nodalarc",
        owner_ref=OWNER_REF,
        package_root=PACKAGE_ROOT,
    )
    sample = next(iter(selected.composed.values()))
    frr = next(c for c in sample.composition.containers if c.name == "frr")
    observer = next(c for c in sample.composition.containers if c.name == "observer")
    assert frr.image == "node01:5000/nodalarc/frr:dev"
    # A substituted reference is a mutable tag: the configured pull policy
    # must travel with it, while untouched digest-pinned containers keep
    # the composition's default.
    assert frr.image_pull_policy == "Always"
    assert observer.image.startswith("registry.example/nodalarc/base@sha256:")
    assert observer.image_pull_policy != "Always"


def test_dev_image_override_without_pull_policy_is_terminal(
    resolved: ResolvedSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    zeros = "0" * 64
    monkeypatch.setenv(
        "WORKLOAD_DEV_IMAGE_OVERRIDES",
        f'{{"registry.example/nodalarc/frr@sha256:{zeros}": "node01:5000/nodalarc/frr:dev"}}',
    )
    monkeypatch.delenv("IMAGE_PULL_POLICY", raising=False)
    with pytest.raises(WorkloadSelectionError, match="IMAGE_PULL_POLICY"):
        prepare_workload_selection(
            _selection_ref(),
            resolved,
            _rendered(resolved),
            namespace="nodalarc",
            owner_ref=OWNER_REF,
            package_root=PACKAGE_ROOT,
        )


def test_terminal_surfaces_compose_per_profile(resolved: ResolvedSession) -> None:
    """FRR declares ssh (platform mounts the session key); the QUIC
    endpoints declare exec; the contract rides the composed workload."""
    import json

    selected = prepare_workload_selection(
        _selection_ref(),
        resolved,
        _rendered(resolved),
        namespace="nodalarc",
        owner_ref=OWNER_REF,
        package_root=PACKAGE_ROOT,
    )
    sample = next(iter(selected.composed.values()))
    assert json.loads(sample.terminal_access) == {"surface": "ssh"}
    volumes = {v.name: v for v in sample.composition.volumes}
    assert "na-terminal-keys" in volumes
    assert volumes["na-terminal-keys"].secret.secret_name == "nodalarc-terminal-keys"
    frr = next(c for c in sample.composition.containers if c.name == "frr")
    key_mounts = [m for m in frr.volume_mounts if m.name == "na-terminal-keys"]
    assert [m.mount_path for m in key_mounts] == ["/etc/ssh-keys"]


def test_exec_terminal_contract_composes(resolved_static=None) -> None:

    from nodalarc.workloads.refs import ImplementationBindingRef
    from nodalarc.workloads.source import DirectoryPackageSource

    package = DirectoryPackageSource(PACKAGE_ROOT).load(
        ImplementationBindingRef("nodalarc:bindings/earth-luna-quic-lab.yaml")
    )
    loaded = package.profiles["nodalarc:profiles/quic/picoquic-server.yaml"]
    terminal = loaded.profile.terminal
    assert terminal is not None
    assert terminal.surface == "exec"
    assert list(terminal.command) == ["/bin/bash"]


def _immutable_cm(binary_data: dict[str, str]) -> kubernetes.client.V1ConfigMap:
    return kubernetes.client.V1ConfigMap(
        metadata=kubernetes.client.V1ObjectMeta(
            name="wl-x", namespace="nodalarc", owner_references=[OWNER_REF]
        ),
        immutable=True,
        binary_data=binary_data,
    )


def _existing(
    uid: str = "owner-uid-1",
    *,
    kind: str = "ConstellationSpec",
    immutable=True,
    binary_data=None,
    data=None,
):
    ref = SimpleNamespace(api_version="", kind=kind, name="s", uid=uid, block_owner_deletion=False)
    return SimpleNamespace(
        metadata=SimpleNamespace(owner_references=[ref]),
        immutable=immutable,
        binary_data={"k": "dg=="} if binary_data is None else binary_data,
        data=data,
    )


def _conflict(v1: MagicMock, existing) -> None:
    v1.create_namespaced_config_map.side_effect = kubernetes.client.rest.ApiException(status=409)
    v1.read_namespaced_config_map.return_value = existing


def test_immutable_configmap_verifies_owner_content_and_immutability() -> None:
    cm = _immutable_cm({"k": "dg=="})
    v1 = MagicMock()
    _ensure_immutable_configmap(v1, "nodalarc", cm)
    v1.create_namespaced_config_map.assert_called_once()

    v1 = MagicMock()
    _conflict(v1, _existing())
    _ensure_immutable_configmap(v1, "nodalarc", cm)

    v1 = MagicMock()
    _conflict(v1, _existing("old-cr-uid"))
    with pytest.raises(WorkloadSelectionError, match="is owned by"):
        _ensure_immutable_configmap(v1, "nodalarc", cm)

    # The COMPLETE owner projection is compared — a matching UID with a
    # different kind is still a foreign object.
    v1 = MagicMock()
    _conflict(v1, _existing(kind="Deployment"))
    with pytest.raises(WorkloadSelectionError, match="is owned by"):
        _ensure_immutable_configmap(v1, "nodalarc", cm)

    v1 = MagicMock()
    _conflict(v1, _existing(binary_data={"k": "other=="}))
    with pytest.raises(ValueError, match="different contents"):
        _ensure_immutable_configmap(v1, "nodalarc", cm)

    v1 = MagicMock()
    _conflict(v1, _existing(immutable=None))
    with pytest.raises(ValueError, match="not immutable"):
        _ensure_immutable_configmap(v1, "nodalarc", cm)

    v1 = MagicMock()
    _conflict(v1, _existing(data={"extra": "surprise"}))
    with pytest.raises(ValueError, match="unexpected plain data"):
        _ensure_immutable_configmap(v1, "nodalarc", cm)
