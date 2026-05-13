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
