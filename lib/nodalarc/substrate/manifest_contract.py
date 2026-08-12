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
    "managed_interface_cleanup",
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


# Session pod labels carrying the deployment-run identity. The Operator
# stamps them at pod creation; Node Agent discovery filters on them.
POD_SESSION_RUN_LABEL = "nodalarc.io/session-run-id"
POD_OWNER_UID_LABEL = "nodalarc.io/owner-uid"


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


class SiteLanMember(_StrictModel):
    """One environment attached to an Ethernet segment: its in-pod interface
    name, its allocated addresses, an optional host default-route gateway
    (substrate configuration, never present for a routed member), and its
    operator-assigned placement."""

    node_id: str
    interface: str
    addresses: list[str] = Field(min_length=1)
    gateway: str | None = None
    k3s_node: str
    host_ip: str

    @field_validator("node_id", "interface", "k3s_node", "host_ip")
    @classmethod
    def _member_fields(cls, value: str) -> str:
        if not value:
            raise ValueError("segment member fields must be non-empty")
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
    node_type: Literal["satellite", "ground_station", "host"]
    # The K3s node hosting this pod, from observed scheduling. Each Node
    # Agent derives its expected-local set from this field; discovery output
    # must never define expectation.
    host: str
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

    @field_validator("sysctls")
    @classmethod
    def _sysctls_required(cls, value: dict[str, str]) -> dict[str, str]:
        if not value:
            raise ValueError("sysctls must be explicit")
        return value

    @model_validator(mode="after")
    def _host_attachment_rules(self) -> NodeSpec:
        if self.node_type == "host" and (self.isl_interfaces or self.gnd_interfaces):
            raise ValueError("host nodes carry no ISL or ground interfaces")
        return self

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
            # plane/slot are optional grid coordinates: grid-born
            # satellites carry both, individually placed satellites (GEO
            # longitude slots, raw state vectors) carry neither. No
            # wiring consumer reads them; a half-set pair is corruption.
            if (self.plane is None) != (self.slot is None):
                raise ValueError("satellite plane/slot must be set together or not at all")
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
    # Deployment-run identity, matching the session pod labels. Node Agent
    # discovery is fenced to pods carrying exactly this run and owner, so a
    # stale pod from a previous deployment can never satisfy discovery.
    session_run_id: str
    owner_uid: str
    wiring_generation: str
    required_phases: list[str]
    nodes: dict[str, NodeSpec]
    ground_bridges: dict[str, GroundBridgeSpec]
    # The cluster's covering pod CIDR. The Node Agent installs a management
    # route to it via the CNI gateway on every session pod, so the browser
    # terminal and control-plane traffic reach pods on other nodes while the
    # routing engine owns the default route. Optional for older manifests.
    cluster_pod_cidr: str | None = None
    required_substrate_pairs: list[RequiredSubstratePair]
    site_lans: dict[str, SiteLanSpec]
    isl_link_count: int

    @field_validator("session_id", "session_run_id", "owner_uid", "wiring_generation")
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
    def _segments_declare_known_members(self) -> WiringManifest:
        """Ethernet segments and manifest nodes must agree exactly.

        Every declared segment member is a manifest node, host nodes carry
        exactly one membership with a gateway, and VNIs are pairwise
        distinct — the agent wires precisely what is declared, with no
        orphan ports and no phantom members.
        """
        memberships: dict[str, list[SiteLanMember]] = {}
        for segment_id, spec in self.site_lans.items():
            for member in spec.members:
                if member.node_id not in self.nodes:
                    raise ValueError(
                        f"segment {segment_id!r} declares unknown member {member.node_id!r}"
                    )
                memberships.setdefault(member.node_id, []).append(member)
        for node_id, node in self.nodes.items():
            members = memberships.get(node_id, [])
            gateways = [member for member in members if member.gateway is not None]
            if node.node_type == "host":
                if len(members) != 1:
                    raise ValueError(
                        f"host node {node_id!r} requires exactly one segment "
                        f"membership; got {len(members)}"
                    )
                if not gateways:
                    raise ValueError(f"host node {node_id!r} requires a segment gateway")
            elif gateways:
                raise ValueError(
                    f"only host nodes carry a segment gateway; a routed node's "
                    f"forwarding system owns its routing decisions ({node_id!r})"
                )
        vnis = [spec.vni for spec in self.site_lans.values()]
        if len(set(vnis)) != len(vnis):
            raise ValueError("segment VNIs must be pairwise distinct")
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
