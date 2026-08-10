# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""Structural grammar checks for the profile catalog family."""

from __future__ import annotations

import pytest
from nodalarc.catalog_refs import CatalogReferenceError, ProfileRef
from nodalarc.models.catalog import (
    Node,
    Profile,
    ProfileDocument,
    SiteNode,
    SpaceNode,
)
from nodalarc.models.configuration import CONFIGURATION_DOCUMENT_MODELS
from nodalarc.models.segments import GroundSegment, LagrangeSegment, SpaceSegment
from pydantic import ValidationError

_DIGEST = "0" * 64
_IMAGE = f"nodalarc/frr@sha256:{_DIGEST}"
_BASE_IMAGE = f"nodalarc/base@sha256:{_DIGEST}"


def _resources() -> dict:
    return {
        "requests": {"cpu_m": 10, "memory_mi": 32},
        "limits": {"cpu_m": 200, "memory_mi": 128},
    }


def _router_profile() -> dict:
    return {
        "id": "frr-router",
        "display_name": "FRR reference router",
        "adapter": "frr",
        "registry": "node01:5000",
        "image": _IMAGE,
        "capabilities": ["CHOWN", "NET_ADMIN", "NET_RAW", "SYS_ADMIN", "SYS_CHROOT"],
        "root_filesystem": "read_only",
        "config_mount": "/etc/frr-config",
        "volumes": [
            {"name": "frr-etc", "kind": "ephemeral", "medium": "node", "size_mi": 8},
            {"name": "tmp", "kind": "ephemeral", "medium": "memory", "size_mi": 16},
        ],
        "mounts": [
            {"volume": "frr-etc", "path": "/etc/frr"},
            {"volume": "tmp", "path": "/tmp"},
        ],
        "resources": _resources(),
        "readiness": {
            "argv": ["/bin/sh", "-c", "test -f /etc/frr/.config_version"],
            "timeout_seconds": 5,
            "period_seconds": 5,
        },
        "terminal": {"surface": "ssh", "authorized_keys_path": "/etc/ssh-keys"},
        "sidecars": [
            {
                "name": "observer",
                "image": _BASE_IMAGE,
                "command": ["/bin/bash", "-c", "sleep 30"],
                "resources": _resources(),
            }
        ],
    }


def _app_profile() -> dict:
    return {
        "id": "linux-host",
        "registry": "node01:5000",
        "image": _BASE_IMAGE,
        "command": ["/bin/bash", "-c", "sleep infinity"],
        "resources": _resources(),
        "terminal": {"surface": "exec", "command": ["/bin/bash"]},
    }


def test_routing_shaped_profile_parses_completely() -> None:
    profile = Profile.model_validate(_router_profile())

    assert profile.adapter == "frr"
    assert profile.config_mount == "/etc/frr-config"
    assert profile.sidecars[0].registry is None
    assert profile.sidecars[0].root_filesystem == "read_only"
    assert profile.terminal is not None and profile.terminal.surface == "ssh"


def test_app_profile_defaults_are_the_documented_defaults() -> None:
    profile = Profile.model_validate(_app_profile())

    assert profile.adapter is None
    assert profile.capabilities == ()
    assert profile.root_filesystem == "read_only"
    assert profile.volumes == () and profile.mounts == () and profile.sidecars == ()
    assert profile.config_mount is None


def test_config_mount_requires_an_adapter() -> None:
    document = _app_profile()
    document["config_mount"] = "/etc/app-config"

    with pytest.raises(ValidationError, match="config_mount requires an adapter"):
        Profile.model_validate(document)


def test_capability_outside_the_closed_vocabulary_is_rejected() -> None:
    document = _app_profile()
    document["capabilities"] = ["SYS_PTRACE"]

    with pytest.raises(ValidationError):
        Profile.model_validate(document)


def test_capabilities_must_be_unique_and_ascending() -> None:
    document = _app_profile()
    document["capabilities"] = ["NET_RAW", "NET_ADMIN"]

    with pytest.raises(ValidationError, match="ascending"):
        Profile.model_validate(document)


def test_duplicate_volume_names_are_rejected() -> None:
    document = _router_profile()
    document["volumes"].append(
        {"name": "tmp", "kind": "ephemeral", "medium": "memory", "size_mi": 1}
    )

    with pytest.raises(ValidationError, match="volume names must be unique"):
        Profile.model_validate(document)


def test_mount_of_an_undeclared_volume_is_rejected() -> None:
    document = _router_profile()
    document["mounts"].append({"volume": "missing", "path": "/data"})

    with pytest.raises(ValidationError, match="undeclared volume"):
        Profile.model_validate(document)


def test_sidecar_mount_of_an_undeclared_volume_is_rejected() -> None:
    document = _router_profile()
    document["sidecars"][0]["mounts"] = [{"volume": "missing", "path": "/data"}]

    with pytest.raises(ValidationError, match="undeclared volume"):
        Profile.model_validate(document)


def test_config_mount_nested_under_an_authored_mount_is_rejected() -> None:
    document = _router_profile()
    document["config_mount"] = "/etc/frr/rendered"

    with pytest.raises(ValidationError, match="mount destinations conflict"):
        Profile.model_validate(document)


def test_limits_below_requests_are_rejected() -> None:
    document = _app_profile()
    document["resources"]["limits"]["memory_mi"] = 1

    with pytest.raises(ValidationError, match="memory limit"):
        Profile.model_validate(document)


def test_image_without_a_digest_is_rejected() -> None:
    document = _app_profile()
    document["image"] = "nodalarc/base:latest"

    with pytest.raises(ValidationError):
        Profile.model_validate(document)


def test_argv_element_limit_is_enforced() -> None:
    document = _app_profile()
    document["command"] = ["x"] * 65

    with pytest.raises(ValidationError, match="exceeds 64 elements"):
        Profile.model_validate(document)


def test_sidecar_must_not_reuse_the_profile_id() -> None:
    document = _router_profile()
    document["sidecars"][0]["name"] = "frr-router"

    with pytest.raises(ValidationError, match="profile id as its name"):
        Profile.model_validate(document)


def test_terminal_surfaces_are_closed_shapes() -> None:
    document = _app_profile()
    document["terminal"] = {"surface": "ssh", "command": ["/bin/bash"]}

    with pytest.raises(ValidationError):
        Profile.model_validate(document)


def test_reserved_mount_trees_are_rejected() -> None:
    document = _router_profile()
    document["mounts"].append({"volume": "tmp", "path": "/var/run/secrets/steal"})

    with pytest.raises(ValidationError):
        Profile.model_validate(document)


def test_profile_document_wrapper_is_closed() -> None:
    document = ProfileDocument.model_validate({"profile": _app_profile()})
    assert document.profile.id == "linux-host"

    with pytest.raises(ValidationError):
        ProfileDocument.model_validate({"profile": _app_profile(), "extra": {}})


def test_profiles_family_is_registered() -> None:
    assert CONFIGURATION_DOCUMENT_MODELS["profiles"] is ProfileDocument


def test_profile_reference_accepts_both_namespaces_and_only_its_family() -> None:
    assert ProfileRef("nodalarc:profiles/frr-router.yaml")
    assert ProfileRef("user:profiles/my-client.yaml")

    with pytest.raises(CatalogReferenceError):
        ProfileRef("nodalarc:nodes/space/leo-sat.yaml")


def test_profile_is_readable_at_every_assignment_level() -> None:
    reference = "nodalarc:profiles/frr-router.yaml"

    node = Node.model_validate(
        {
            "id": "leo-sat",
            "forwarding": "routed",
            "profile": reference,
            "ethernet": [],
            "terminals": [],
            "payloads": [],
        }
    )
    assert node.profile == reference

    space_node = SpaceNode.model_validate(
        {"id": "probe-01", "node": "nodalarc:nodes/space/leo-sat.yaml", "profile": reference,
         "orbit": "nodalarc:orbits/earth/leo-550.yaml"}
    )
    assert space_node.profile == reference

    site_node = SiteNode.model_validate(
        {
            "id": "host1",
            "model": "nodalarc:nodes/ground/lab-host.yaml",
            "profile": reference,
            "terminals": {},
            "payloads": {},
            "interfaces": {"lo0": {"ipv4": "10.255.0.2/32"}, "terr0": {"ipv4": "172.16.150.9/24"}},
        }
    )
    assert site_node.profile == reference

    space_segment = SpaceSegment.model_validate(
        {"id": "leo", "profile": reference,
         "source": "nodalarc:constellations/earth/leo/earth-leo-ring-36.yaml"}
    )
    assert space_segment.profile == reference

    ground_segment = GroundSegment.model_validate(
        {"id": "ground", "profile": reference,
         "placement": {"from_site_set": "nodalarc:site-sets/earth/earth-quic-lab-sites.yaml"}}
    )
    assert ground_segment.profile == reference

    lagrange_segment = LagrangeSegment.model_validate(
        {
            "id": "l1-relay",
            "profile": reference,
            "node": "nodalarc:nodes/space/leo-sat.yaml",
            "frame": {
                "lagrange": {
                    "primary_body": "nodalarc:bodies/earth.yaml",
                    "secondary_body": "nodalarc:bodies/luna.yaml",
                    "point": "l1",
                    "ephemeris": {"lagrange_approximation": {}},
                }
            },
        }
    )
    assert lagrange_segment.profile == reference
