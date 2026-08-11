# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""Catalog profile composition: containers, volumes, artifacts, terminal."""

from __future__ import annotations

import base64

import pytest
from nodalarc.models.catalog import Profile
from nodalarc.workloads.plan import WorkloadPlan

from nodalarc_operator.workloads.compose import (
    PLAN_ARTIFACT_VOLUME,
    TERMINAL_KEYS_VOLUME,
    compose_workload,
)

_DIGEST = "0" * 64
_OWNER = {"uid": "11111111-2222-3333-4444-555555555555"}
PROFILE_REF = "nodalarc:profiles/router.yaml"


def _router_profile() -> Profile:
    return Profile.model_validate(
        {
            "id": "router",
            "adapter": "frr",
            "registry": "registry.example",
            "image": f"nodalarc/frr@sha256:{_DIGEST}",
            "capabilities": ["NET_ADMIN", "NET_RAW"],
            "config_mount": "/etc/frr-config",
            "volumes": [
                {"name": "frr-etc", "kind": "ephemeral", "medium": "node", "size_mi": 8},
            ],
            "mounts": [{"volume": "frr-etc", "path": "/etc/frr"}],
            "resources": {
                "requests": {"cpu_m": 10, "memory_mi": 32},
                "limits": {"cpu_m": 200, "memory_mi": 128},
            },
            "readiness": {
                "argv": ["/bin/sh", "-c", "true"],
                "timeout_seconds": 5,
                "period_seconds": 5,
            },
            "terminal": {"surface": "ssh", "authorized_keys_path": "/etc/ssh-keys"},
            "sidecars": [
                {
                    "name": "observer",
                    "image": f"nodalarc/base@sha256:{_DIGEST}",
                    "command": ["/bin/bash", "-c", "sleep 30"],
                    "resources": {
                        "requests": {"cpu_m": 10, "memory_mi": 16},
                        "limits": {"cpu_m": 100, "memory_mi": 32},
                    },
                }
            ],
        }
    )


def _host_profile() -> Profile:
    return Profile.model_validate(
        {
            "id": "linux-host",
            "registry": "node01:5000",
            "image": f"nodalarc/base@sha256:{_DIGEST}",
            "command": ["/bin/bash", "-c", "sleep infinity"],
            "resources": {
                "requests": {"cpu_m": 10, "memory_mi": 16},
                "limits": {"cpu_m": 100, "memory_mi": 64},
            },
            "terminal": {"surface": "exec", "command": ["/bin/bash"]},
        }
    )


def _plan(rendered: dict[str, bytes] | None = None) -> WorkloadPlan:
    return WorkloadPlan(
        node_id="leo-sat-p00s00",
        profile_ref=PROFILE_REF,
        rendered_files=rendered or {},
    )


def test_router_profile_composes_primary_sidecar_and_artifacts() -> None:
    plan = _plan({"frr.conf": b"!", "daemons": b"zebra=yes"})

    composed = compose_workload(
        plan, _router_profile(), namespace="nodalarc", owner_ref=_OWNER
    )

    names = [container.name for container in composed.composition.containers]
    assert names == ["router", "observer"]
    primary = composed.composition.containers[0]
    assert primary.image == f"registry.example/nodalarc/frr@sha256:{_DIGEST}"
    assert primary.security_context.capabilities.add == ["NET_ADMIN", "NET_RAW"]
    assert primary.security_context.capabilities.drop == ["ALL"]
    assert primary.readiness_probe is not None
    mount_paths = {mount.mount_path for mount in primary.volume_mounts}
    assert {"/etc/frr", "/etc/frr-config", "/etc/ssh-keys"} <= mount_paths

    sidecar = composed.composition.containers[1]
    assert sidecar.image == f"registry.example/nodalarc/base@sha256:{_DIGEST}"
    assert sidecar.security_context.capabilities.add is None

    assert composed.artifact_config_map is not None
    decoded = {
        key: base64.b64decode(value)
        for key, value in composed.artifact_config_map.binary_data.items()
    }
    assert set(decoded.values()) == {b"!", b"zebra=yes"}
    volume_names = {volume.name for volume in composed.composition.volumes}
    assert {PLAN_ARTIFACT_VOLUME, TERMINAL_KEYS_VOLUME, "frr-etc"} <= volume_names
    assert composed.terminal_access == '{"surface":"ssh"}'


def test_host_profile_composes_one_plain_container() -> None:
    composed = compose_workload(
        WorkloadPlan(node_id="host-1", profile_ref=PROFILE_REF),
        _host_profile(),
        namespace="nodalarc",
        owner_ref=_OWNER,
    )

    assert len(composed.composition.containers) == 1
    container = composed.composition.containers[0]
    assert container.image == f"node01:5000/nodalarc/base@sha256:{_DIGEST}"
    assert container.security_context.read_only_root_filesystem is True
    assert composed.artifact_config_map is None
    assert composed.composition.init_containers == []
    assert '"surface":"exec"' in composed.terminal_access
    assert '"container":"linux-host"' in composed.terminal_access


def test_rendered_files_require_a_config_mount() -> None:
    with pytest.raises(ValueError, match="config_mount"):
        compose_workload(
            _plan({"frr.conf": b"!"}),
            _host_profile(),
            namespace="nodalarc",
            owner_ref=_OWNER,
        )


def test_profile_volumes_may_not_use_platform_names() -> None:
    profile = Profile.model_validate(
        {
            "id": "router",
            "registry": "registry.example",
            "image": f"nodalarc/frr@sha256:{_DIGEST}",
            "volumes": [
                {
                    "name": PLAN_ARTIFACT_VOLUME,
                    "kind": "ephemeral",
                    "medium": "memory",
                    "size_mi": 1,
                }
            ],
            "resources": {
                "requests": {"cpu_m": 10, "memory_mi": 16},
                "limits": {"cpu_m": 100, "memory_mi": 32},
            },
        }
    )

    with pytest.raises(ValueError, match="platform names"):
        compose_workload(_plan(), profile, namespace="nodalarc", owner_ref=_OWNER)


def test_artifact_config_map_identity_is_content_addressed_per_owner() -> None:
    plan = _plan({"frr.conf": b"router isis"})
    first = compose_workload(plan, _router_profile(), namespace="nodalarc", owner_ref=_OWNER)
    second = compose_workload(plan, _router_profile(), namespace="nodalarc", owner_ref=_OWNER)
    other_owner = compose_workload(
        plan,
        _router_profile(),
        namespace="nodalarc",
        owner_ref={"uid": "99999999-8888-7777-6666-555555555555"},
    )

    assert first.artifact_config_map.metadata.name == second.artifact_config_map.metadata.name
    assert first.artifact_config_map.metadata.name != other_owner.artifact_config_map.metadata.name

    with pytest.raises(ValueError, match="owner UID"):
        compose_workload(plan, _router_profile(), namespace="nodalarc", owner_ref={})
