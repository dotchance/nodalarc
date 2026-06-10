# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""Typed Node Agent wiring manifest contract."""

from __future__ import annotations

import hashlib
import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from nodalarc.substrate.measurement_contract import RequiredSubstratePair

REQUIRED_WIRING_PHASES: tuple[str, ...] = (
    "phase0_cleanup",
    "sysctls",
    "isl_interfaces",
    "mpls",
    "ground_infrastructure",
    "terrestrial_interfaces",
    "pod_route_finalization",
    "pod_security",
)


def canonical_manifest_json(data: dict[str, Any]) -> str:
    return json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def derive_wiring_generation(data: dict[str, Any]) -> str:
    material = dict(data)
    material.pop("wiring_generation", None)
    return "sha256:" + hashlib.sha256(canonical_manifest_json(material).encode()).hexdigest()


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class InterfaceName(_StrictModel):
    name: str

    @field_validator("name")
    @classmethod
    def _nonempty(cls, value: str) -> str:
        if not value:
            raise ValueError("interface name must be non-empty")
        return value


class IslInterface(_StrictModel):
    name: str
    peer_node: str
    peer_iface: str

    @field_validator("name", "peer_node", "peer_iface")
    @classmethod
    def _nonempty(cls, value: str) -> str:
        if not value:
            raise ValueError("ISL interface fields must be non-empty")
        return value


class TerrestrialSpec(_StrictModel):
    addresses: list[str] = Field(default_factory=list)
    # The site LAN this node's terr0 attaches to. Required whenever addresses
    # are present — terr0 is a port on the site's L2 segment, never an
    # isolated interface.
    site_id: str | None = None

    @model_validator(mode="after")
    def _addressed_terr0_belongs_to_a_site(self) -> TerrestrialSpec:
        if self.addresses and not self.site_id:
            raise ValueError("terrestrial addresses require site_id (site LAN membership)")
        return self


class SiteLanMember(_StrictModel):
    """One pod attached to a site LAN, with its operator-assigned placement."""

    node_id: str
    k3s_node: str
    host_ip: str

    @field_validator("node_id", "k3s_node", "host_ip")
    @classmethod
    def _member_fields(cls, value: str) -> str:
        if not value:
            raise ValueError("site LAN member fields must be non-empty")
        return value


class SiteLanUplink(_StrictModel):
    """Future capability slot: attach the site LAN to a real physical
    interface on a compute node so the emulation joins the real world.

    Schema-present from day one so adding the capability is a value, not a
    contract break. The Node Agent fails loudly if it encounters one before
    the wiring exists — an uplink must never be silently ignored.
    """

    host: str
    interface: str


class SiteLanSpec(_StrictModel):
    """One physical site's LAN segment: per-host bridge, members as bridge
    ports, VXLAN head-end replication between hosts that carry members."""

    vni: int = Field(ge=1, le=16777214)
    members: list[SiteLanMember] = Field(min_length=1)
    uplink: SiteLanUplink | None = None

    @model_validator(mode="after")
    def _unique_members(self) -> SiteLanSpec:
        node_ids = [member.node_id for member in self.members]
        if len(set(node_ids)) != len(node_ids):
            raise ValueError("site LAN members must be unique")
        return self


class GroundBridgeSpec(_StrictModel):
    """Ground station bridge declaration marker.

    The manifest does not carry mutable bridge configuration here. The key is
    the ground-station node_id, and concrete host/pod interface names are
    derived deterministically from that node_id plus the station's
    ``NodeSpec.gnd_interfaces``. Keeping this model fieldless and strict makes
    accidental bridge payloads fail validation while still requiring every
    ground station to be declared in ``ground_bridges``.
    """


class NodeSpec(_StrictModel):
    node_type: Literal["satellite", "ground_station"]
    sysctls: dict[str, str]
    isl_interfaces: list[IslInterface]
    gnd_interfaces: list[InterfaceName]
    mpls_enable: bool
    segment_routing: bool
    mtu: int
    remove_default_route: bool
    plane: int | None = None
    slot: int | None = None
    gs_name: str | None = None
    gs_index: int | None = None
    terrestrial: TerrestrialSpec | None = None

    @field_validator("sysctls")
    @classmethod
    def _sysctls_required(cls, value: dict[str, str]) -> dict[str, str]:
        if not value:
            raise ValueError("sysctls must be explicit")
        return value

    @field_validator("gnd_interfaces")
    @classmethod
    def _ground_interfaces_explicit(cls, value: list[InterfaceName]) -> list[InterfaceName]:
        names = [iface.name for iface in value]
        if len(set(names)) != len(names):
            raise ValueError("gnd_interfaces must not contain duplicate names")
        return value

    @field_validator("isl_interfaces")
    @classmethod
    def _isl_interfaces_unique(cls, value: list[IslInterface]) -> list[IslInterface]:
        names = [iface.name for iface in value]
        if len(set(names)) != len(names):
            raise ValueError("isl_interfaces must not contain duplicate names")
        return value

    @model_validator(mode="after")
    def _node_type_fields(self) -> NodeSpec:
        if self.node_type == "satellite":
            if self.plane is None or self.slot is None:
                raise ValueError("satellite nodes require plane and slot")
            if self.gs_name is not None or self.gs_index is not None:
                raise ValueError("satellite nodes must not set gs_name or gs_index")
        if self.node_type == "ground_station":
            if not self.gs_name or self.gs_index is None:
                raise ValueError("ground_station nodes require gs_name and gs_index")
            if self.plane is not None or self.slot is not None:
                raise ValueError("ground_station nodes must not set plane or slot")
            if not self.gnd_interfaces:
                raise ValueError("ground_station nodes require at least one gnd_interface")
        return self


class WiringManifest(_StrictModel):
    session_id: str
    wiring_generation: str
    required_phases: list[str]
    nodes: dict[str, NodeSpec]
    ground_bridges: dict[str, GroundBridgeSpec]
    required_substrate_pairs: list[RequiredSubstratePair]
    site_lans: dict[str, SiteLanSpec]
    isl_link_count: int

    @field_validator("session_id", "wiring_generation")
    @classmethod
    def _nonempty(cls, value: str) -> str:
        if not value:
            raise ValueError("manifest identity fields must be non-empty")
        return value

    @field_validator("wiring_generation")
    @classmethod
    def _generation_format(cls, value: str) -> str:
        if not value.startswith("sha256:") or len(value) != len("sha256:") + 64:
            raise ValueError("wiring_generation must be sha256:<64 hex chars>")
        return value

    @field_validator("required_phases")
    @classmethod
    def _required_phases(cls, value: list[str]) -> list[str]:
        missing = set(REQUIRED_WIRING_PHASES) - set(value)
        if missing:
            raise ValueError(f"required_phases missing: {', '.join(sorted(missing))}")
        unknown = set(value) - set(REQUIRED_WIRING_PHASES)
        if unknown:
            raise ValueError(f"required_phases unknown: {', '.join(sorted(unknown))}")
        return value

    @field_validator("nodes")
    @classmethod
    def _nodes_required(cls, value: dict[str, NodeSpec]) -> dict[str, NodeSpec]:
        if not value:
            raise ValueError("manifest nodes must be non-empty")
        return value

    @model_validator(mode="after")
    def _ground_bridges_match_ground_stations(self) -> WiringManifest:
        ground_station_nodes = {
            node_id for node_id, node in self.nodes.items() if node.node_type == "ground_station"
        }
        bridge_ids = set(self.ground_bridges)
        if bridge_ids != ground_station_nodes:
            raise ValueError(
                "ground_bridges must exactly match ground_station nodes: "
                f"missing={sorted(ground_station_nodes - bridge_ids)} "
                f"extra={sorted(bridge_ids - ground_station_nodes)}"
            )
        return self

    @model_validator(mode="after")
    def _site_lans_cover_addressed_terr0(self) -> WiringManifest:
        """Site LANs and addressed terr0 interfaces must agree exactly.

        Every ground node carrying terrestrial addresses is a member of the
        site LAN it names, every declared member is a manifest ground node,
        and VNIs are pairwise distinct — the agent wires precisely what is
        declared, with no orphan ports and no phantom members.
        """
        members_by_site: dict[str, set[str]] = {
            site_id: {member.node_id for member in spec.members}
            for site_id, spec in self.site_lans.items()
        }
        for node_id, node in self.nodes.items():
            terrestrial = node.terrestrial
            if terrestrial is None or not terrestrial.addresses:
                continue
            site_id = terrestrial.site_id
            if site_id not in members_by_site:
                raise ValueError(
                    f"node {node_id!r} references site LAN {site_id!r}, "
                    "which is not declared in site_lans"
                )
            if node_id not in members_by_site[site_id]:
                raise ValueError(
                    f"node {node_id!r} is not a declared member of site LAN {site_id!r}"
                )
        ground_nodes = {
            node_id for node_id, node in self.nodes.items() if node.node_type == "ground_station"
        }
        for site_id, member_ids in members_by_site.items():
            unknown = sorted(member_ids - ground_nodes)
            if unknown:
                raise ValueError(f"site LAN {site_id!r} declares non-ground member(s): {unknown}")
        vnis = [spec.vni for spec in self.site_lans.values()]
        if len(set(vnis)) != len(vnis):
            raise ValueError("site LAN VNIs must be pairwise distinct")
        return self

    @field_validator("required_substrate_pairs")
    @classmethod
    def _substrate_pairs_unique(
        cls, value: list[RequiredSubstratePair]
    ) -> list[RequiredSubstratePair]:
        keys = [pair.directional_key for pair in value]
        if len(set(keys)) != len(keys):
            raise ValueError("required_substrate_pairs must not contain duplicate directions")
        return value
