# Copyright 2024-2026 .chance (dotchance)
# Licensed under the NodalArc Source Available License 1.0. See LICENSE file.
"""Generation-scoped substrate measurement contract.

Substrate RTT is a physical Kubernetes-node-pair fact. It must exist before
Scheduler dispatches cross-node links, and it must be measured by the Node
Agent running on the source host network.
"""

from __future__ import annotations

import ipaddress
import json
from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

SubstrateReason = Literal["isl", "ground"]
SubstrateMeasurementStatus = Literal["ok", "failed"]

STATUS_CONFIGMAP_PREFIX = "nodalarc-substrate-status-"
STATUS_CONFIGMAP_LABEL_KEY = "nodalarc.io/config-type"
STATUS_CONFIGMAP_LABEL_VALUE = "substrate-status"


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


def _validate_ip(value: str, *, field: str) -> str:
    if not value:
        raise ValueError(f"{field} must be non-empty")
    try:
        ipaddress.ip_address(value)
    except ValueError as exc:
        raise ValueError(f"{field} must be a valid IP address") from exc
    return value


def substrate_pair_key(node_a: str, node_b: str) -> str:
    """Direction-independent key for a Kubernetes node pair."""
    if not node_a or not node_b:
        raise ValueError("substrate pair nodes must be non-empty")
    left, right = sorted((node_a, node_b))
    return f"{left}<->{right}"


def substrate_directional_key(source_node: str, target_node: str) -> str:
    """Direction-specific key for a measured Kubernetes node pair."""
    if not source_node or not target_node:
        raise ValueError("substrate directional nodes must be non-empty")
    return f"{source_node}->{target_node}"


def substrate_status_configmap_name(source_node: str) -> str:
    if not source_node:
        raise ValueError("source_node is required")
    return f"{STATUS_CONFIGMAP_PREFIX}{source_node}"


def substrate_status_labels() -> dict[str, str]:
    return {
        "nodalarc.io/managed-by": "node-agent",
        STATUS_CONFIGMAP_LABEL_KEY: STATUS_CONFIGMAP_LABEL_VALUE,
    }


class RequiredSubstratePair(_StrictModel):
    """One directional Kubernetes-node measurement required before dispatch."""

    source_node: str
    source_ip: str
    target_node: str
    target_ip: str
    reasons: list[SubstrateReason]
    pair_key: str
    directional_key: str

    @field_validator("source_node", "target_node", "pair_key", "directional_key")
    @classmethod
    def _nonempty(cls, value: str) -> str:
        if not value:
            raise ValueError("substrate pair identity fields must be non-empty")
        return value

    @field_validator("source_ip")
    @classmethod
    def _source_ip(cls, value: str) -> str:
        return _validate_ip(value, field="source_ip")

    @field_validator("target_ip")
    @classmethod
    def _target_ip(cls, value: str) -> str:
        return _validate_ip(value, field="target_ip")

    @field_validator("reasons")
    @classmethod
    def _reasons_required(cls, value: list[SubstrateReason]) -> list[SubstrateReason]:
        if not value:
            raise ValueError("substrate pair reasons must be non-empty")
        if len(set(value)) != len(value):
            raise ValueError("substrate pair reasons must be unique")
        return sorted(value)

    @model_validator(mode="after")
    def _keys_match_nodes(self) -> RequiredSubstratePair:
        expected_pair = substrate_pair_key(self.source_node, self.target_node)
        expected_directional = substrate_directional_key(self.source_node, self.target_node)
        if self.source_node == self.target_node:
            raise ValueError("required substrate pair must be cross-node")
        if self.pair_key != expected_pair:
            raise ValueError(
                f"pair_key mismatch: expected {expected_pair!r}, got {self.pair_key!r}"
            )
        if self.directional_key != expected_directional:
            raise ValueError(
                "directional_key mismatch: "
                f"expected {expected_directional!r}, got {self.directional_key!r}"
            )
        return self

    @classmethod
    def build(
        cls,
        *,
        source_node: str,
        source_ip: str,
        target_node: str,
        target_ip: str,
        reasons: list[SubstrateReason],
    ) -> RequiredSubstratePair:
        return cls(
            source_node=source_node,
            source_ip=source_ip,
            target_node=target_node,
            target_ip=target_ip,
            reasons=reasons,
            pair_key=substrate_pair_key(source_node, target_node),
            directional_key=substrate_directional_key(source_node, target_node),
        )


class SubstrateMeasurement(_StrictModel):
    """One measured RTT from a source Kubernetes node to a target node."""

    session_id: str
    wiring_generation: str
    source_node: str
    source_ip: str
    target_node: str
    target_ip: str
    measured_at: datetime
    stale_after: datetime
    status: SubstrateMeasurementStatus
    sample_count: int
    success_count: int
    median_rtt_ms: float | None
    min_rtt_ms: float | None = None
    max_rtt_ms: float | None = None
    error_message: str = ""

    @field_validator("session_id", "wiring_generation", "source_node", "target_node")
    @classmethod
    def _nonempty(cls, value: str) -> str:
        if not value:
            raise ValueError("substrate measurement identity fields must be non-empty")
        return value

    @field_validator("source_ip")
    @classmethod
    def _source_ip(cls, value: str) -> str:
        return _validate_ip(value, field="source_ip")

    @field_validator("target_ip")
    @classmethod
    def _target_ip(cls, value: str) -> str:
        return _validate_ip(value, field="target_ip")

    @field_validator("measured_at", "stale_after")
    @classmethod
    def _timezone_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("substrate measurement timestamps must be timezone-aware")
        return value

    @model_validator(mode="after")
    def _measurement_consistent(self) -> SubstrateMeasurement:
        if not self.wiring_generation.startswith("sha256:"):
            raise ValueError("wiring_generation must be sha256:<hex>")
        if self.sample_count <= 0:
            raise ValueError("sample_count must be > 0")
        if self.success_count < 0 or self.success_count > self.sample_count:
            raise ValueError("success_count must be between 0 and sample_count")
        if self.stale_after <= self.measured_at:
            raise ValueError("stale_after must be after measured_at")
        if self.status == "ok":
            if self.success_count <= 0:
                raise ValueError("successful substrate measurement requires RTT samples")
            if self.median_rtt_ms is None:
                raise ValueError("successful substrate measurement requires median_rtt_ms")
        return self

    def rtt_ms(
        self,
        *,
        now: datetime,
        session_id: str,
        wiring_generation: str,
        source_node: str,
        source_ip: str,
        target_node: str,
        target_ip: str,
    ) -> float:
        """Return RTT when this measurement proves the requested direction."""
        if now.tzinfo is None:
            raise ValueError("now must be timezone-aware")
        expected = {
            "session_id": session_id,
            "wiring_generation": wiring_generation,
            "source_node": source_node,
            "source_ip": source_ip,
            "target_node": target_node,
            "target_ip": target_ip,
        }
        actual = {
            "session_id": self.session_id,
            "wiring_generation": self.wiring_generation,
            "source_node": self.source_node,
            "source_ip": self.source_ip,
            "target_node": self.target_node,
            "target_ip": self.target_ip,
        }
        mismatches = [
            f"{field}: measurement={actual[field]!r} expected={expected[field]!r}"
            for field in expected
            if actual[field] != expected[field]
        ]
        if mismatches:
            raise ValueError(
                "Substrate RTT measurement identity mismatch: " + "; ".join(mismatches)
            )
        if self.status != "ok":
            raise ValueError(
                f"Substrate RTT measurement {self.source_node}->{self.target_node} failed: "
                f"{self.error_message}"
            )
        if self.median_rtt_ms is None:
            raise ValueError(
                f"Substrate RTT measurement {self.source_node}->{self.target_node} has no median RTT"
            )
        if self.success_count <= 0:
            raise ValueError(
                f"Substrate RTT measurement {self.source_node}->{self.target_node} has no samples"
            )
        if self.stale_after <= now:
            raise ValueError(
                f"Substrate RTT measurement {self.source_node}->{self.target_node} is stale: "
                f"stale_after={self.stale_after.isoformat()} now={now.isoformat()}"
            )
        return self.median_rtt_ms


class SubstrateStatusDocument(_StrictModel):
    """Durable per-source-node substrate status document."""

    session_id: str
    wiring_generation: str
    source_node: str
    measurements: dict[str, SubstrateMeasurement]

    @field_validator("session_id", "wiring_generation", "source_node")
    @classmethod
    def _nonempty(cls, value: str) -> str:
        if not value:
            raise ValueError("substrate status identity fields must be non-empty")
        return value

    @model_validator(mode="after")
    def _measurements_match_document(self) -> SubstrateStatusDocument:
        for target_node, measurement in self.measurements.items():
            if target_node != measurement.target_node:
                raise ValueError(
                    f"substrate status key {target_node!r} does not match measurement target "
                    f"{measurement.target_node!r}"
                )
            if measurement.session_id != self.session_id:
                raise ValueError("substrate status measurement session_id mismatch")
            if measurement.wiring_generation != self.wiring_generation:
                raise ValueError("substrate status measurement wiring_generation mismatch")
            if measurement.source_node != self.source_node:
                raise ValueError("substrate status measurement source_node mismatch")
        return self


def encode_status_document(document: SubstrateStatusDocument) -> str:
    return document.model_dump_json()


def decode_status_document(value: str) -> SubstrateStatusDocument:
    return SubstrateStatusDocument.model_validate(json.loads(value))


def status_document_configmap_data(document: SubstrateStatusDocument) -> dict[str, str]:
    return {
        "status.json": encode_status_document(document),
        "_session_id": document.session_id,
        "_wiring_generation": document.wiring_generation,
        "_source_node": document.source_node,
    }


def decode_status_configmap_data(data: dict[str, str] | None) -> SubstrateStatusDocument:
    if not data or "status.json" not in data:
        raise ValueError("substrate status ConfigMap missing status.json")
    return decode_status_document(data["status.json"])


def now_utc() -> datetime:
    return datetime.now(UTC)
