# Copyright 2024-2026 .chance (dotchance)
# Licensed under the NodalArc Source Available License 1.0. See LICENSE file.
"""Substrate latency lookup contract for Scheduler dispatch.

The emulator can only subtract substrate latency that has been explicitly
measured or configured. Local links have zero substrate RTT by construction;
cross-node links without a measurement are unrepresentable and must fail
before Node Agent receives a partial topology.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

from nodalarc.substrate.measurement_contract import (
    STATUS_CONFIGMAP_LABEL_KEY,
    STATUS_CONFIGMAP_LABEL_VALUE,
    RequiredSubstratePair,
    SubstrateMeasurement,
    SubstrateStatusDocument,
    decode_status_configmap_data,
    substrate_directional_key,
)


class SubstrateLocator(Protocol):
    """Placement methods required for substrate RTT resolution."""

    def k3s_node(self, node_id: str) -> str | None: ...

    def node_ip(self, k3s_node: str) -> str | None: ...


def _parse_time(value: str, *, field: str) -> datetime:
    if not value:
        raise ValueError(f"substrate measurement missing {field}")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"substrate measurement has invalid {field}: {value!r}") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"substrate measurement {field} must be timezone-aware")
    return parsed


@dataclass(frozen=True, slots=True)
class LiveSubstrateMeasurement:
    """One generation-scoped live substrate RTT measurement."""

    remote_ip: str
    session_id: str
    wiring_generation: str
    source_node: str
    measured_at: datetime
    stale_after: datetime
    status: str
    sample_count: int
    success_count: int
    median_rtt_ms: float | None
    min_rtt_ms: float | None = None
    max_rtt_ms: float | None = None
    error_message: str = ""

    @classmethod
    def from_event(
        cls,
        *,
        remote_ip: str,
        event: Mapping[str, object],
        measurement: Mapping[str, object],
    ) -> LiveSubstrateMeasurement:
        session_id = str(event.get("session_id") or "")
        wiring_generation = str(event.get("wiring_generation") or "")
        source_node = str(event.get("source_node") or "")
        measured_at = _parse_time(
            str(measurement.get("timestamp") or event.get("timestamp") or ""),
            field="timestamp",
        )
        stale_after = _parse_time(str(measurement.get("stale_after") or ""), field="stale_after")
        median = measurement.get("median_rtt_ms")
        return cls(
            remote_ip=remote_ip,
            session_id=session_id,
            wiring_generation=wiring_generation,
            source_node=source_node,
            measured_at=measured_at,
            stale_after=stale_after,
            status=str(measurement.get("status") or ""),
            sample_count=int(measurement.get("sample_count") or 0),
            success_count=int(measurement.get("success_count") or 0),
            median_rtt_ms=None if median is None else float(median),
            min_rtt_ms=(
                None if measurement.get("min_rtt_ms") is None else float(measurement["min_rtt_ms"])
            ),
            max_rtt_ms=(
                None if measurement.get("max_rtt_ms") is None else float(measurement["max_rtt_ms"])
            ),
            error_message=str(measurement.get("error_message") or ""),
        )

    def rtt_ms(
        self,
        *,
        now: datetime,
        session_id: str,
        wiring_generation: str,
    ) -> float:
        if self.session_id != session_id:
            raise ValueError(
                "Substrate RTT measurement session mismatch for "
                f"{self.remote_ip}: measurement={self.session_id!r} expected={session_id!r}"
            )
        if self.wiring_generation != wiring_generation:
            raise ValueError(
                "Substrate RTT measurement generation mismatch for "
                f"{self.remote_ip}: measurement={self.wiring_generation!r} "
                f"expected={wiring_generation!r}"
            )
        if self.status != "ok":
            raise ValueError(
                f"Substrate RTT measurement for {self.remote_ip} failed: {self.error_message}"
            )
        if self.median_rtt_ms is None:
            raise ValueError(f"Substrate RTT measurement for {self.remote_ip} has no median RTT")
        if self.success_count <= 0:
            raise ValueError(f"Substrate RTT measurement for {self.remote_ip} has no RTT samples")
        if self.stale_after <= now:
            raise ValueError(
                f"Substrate RTT measurement for {self.remote_ip} is stale: "
                f"stale_after={self.stale_after.isoformat()} now={now.isoformat()}"
            )
        return self.median_rtt_ms


def parse_substrate_measurement_event(
    event: Mapping[str, object],
) -> dict[str, LiveSubstrateMeasurement]:
    """Parse a Node Agent substrate measurement event.

    The v3 event contract uses ``measurements``. Legacy ``peers`` payloads are
    intentionally not accepted because they carry no generation or freshness.
    """
    raw_measurements = event.get("measurements")
    if not isinstance(raw_measurements, Mapping):
        raise ValueError("substrate event missing measurements map")
    parsed: dict[str, LiveSubstrateMeasurement] = {}
    for remote_ip, measurement in raw_measurements.items():
        if not isinstance(remote_ip, str) or not remote_ip:
            raise ValueError("substrate event contains an empty remote IP")
        if not isinstance(measurement, Mapping):
            raise ValueError(f"substrate measurement for {remote_ip} is not an object")
        parsed[remote_ip] = LiveSubstrateMeasurement.from_event(
            remote_ip=remote_ip,
            event=event,
            measurement=measurement,
        )
    return parsed


def load_substrate_status_documents(
    *,
    k8s_v1,
    namespace: str,
) -> dict[str, SubstrateStatusDocument]:
    """Load durable NodeAgent substrate status documents by source node."""
    cms = k8s_v1.list_namespaced_config_map(
        namespace,
        label_selector=f"{STATUS_CONFIGMAP_LABEL_KEY}={STATUS_CONFIGMAP_LABEL_VALUE}",
    )
    documents: dict[str, SubstrateStatusDocument] = {}
    for cm in cms.items:
        document = decode_status_configmap_data(cm.data)
        documents[document.source_node] = document
    return documents


def validate_required_substrate_measurements(
    *,
    required_pairs: list[RequiredSubstratePair],
    documents_by_source: Mapping[str, SubstrateStatusDocument],
    session_id: str,
    wiring_generation: str,
    now: datetime,
) -> dict[str, SubstrateMeasurement]:
    """Validate and index all required substrate measurements."""
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    indexed: dict[str, SubstrateMeasurement] = {}
    missing: list[str] = []
    for pair in sorted(required_pairs, key=lambda item: item.directional_key):
        document = documents_by_source.get(pair.source_node)
        if document is None:
            missing.append(f"{pair.directional_key}: missing source status document")
            continue
        measurement = document.measurements.get(pair.target_node)
        if measurement is None:
            missing.append(f"{pair.directional_key}: missing target measurement")
            continue
        try:
            measurement.rtt_ms(
                now=now,
                session_id=session_id,
                wiring_generation=wiring_generation,
                source_node=pair.source_node,
                source_ip=pair.source_ip,
                target_node=pair.target_node,
                target_ip=pair.target_ip,
            )
        except ValueError as exc:
            missing.append(f"{pair.directional_key}: {exc}")
            continue
        indexed[pair.directional_key] = measurement
    if missing:
        raise ValueError("Substrate measurement gate failed: " + "; ".join(missing[:20]))
    return indexed


def resolve_substrate_rtt_ms(
    *,
    locator: SubstrateLocator,
    measurements_by_direction: Mapping[str, SubstrateMeasurement],
    node_a: str,
    node_b: str,
    session_id: str = "",
    wiring_generation: str = "",
    now: datetime | None = None,
) -> float:
    """Resolve measured substrate RTT for a requested link.

    Measurements are keyed by directional Kubernetes-node pair. No cross-node
    zero fallback exists: if the substrate is unknown, the requested emulation
    cannot be proven.
    """
    k3s_a = locator.k3s_node(node_a)
    k3s_b = locator.k3s_node(node_b)
    if not k3s_a or not k3s_b:
        raise ValueError(
            f"Missing Kubernetes node placement for {node_a}<->{node_b}; "
            "refusing to treat unknown substrate locality as local"
        )
    if k3s_a == k3s_b:
        return 0.0

    if not session_id or not wiring_generation:
        raise ValueError("session_id and wiring_generation are required for substrate RTT")
    ip_a = locator.node_ip(k3s_a)
    ip_b = locator.node_ip(k3s_b)
    if not ip_a or not ip_b:
        raise ValueError(
            f"Missing Kubernetes node IP for cross-node link {node_a}<->{node_b} "
            f"({k3s_a}={ip_a!r}, {k3s_b}={ip_b!r})"
        )
    key = substrate_directional_key(k3s_a, k3s_b)
    measurement = measurements_by_direction.get(key)
    if measurement is not None:
        return measurement.rtt_ms(
            now=now or datetime.now(UTC),
            session_id=session_id,
            wiring_generation=wiring_generation,
            source_node=k3s_a,
            source_ip=ip_a,
            target_node=k3s_b,
            target_ip=ip_b,
        )

    raise ValueError(
        f"No substrate RTT measurement for cross-node link {node_a}<->{node_b} "
        f"({k3s_a}<->{k3s_b}); refusing to dispatch with unknown substrate latency"
    )
