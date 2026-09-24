# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""MI catalog-runtime seam tests."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest
from measurement.adapters import create_adapter
from measurement.adapters.frr_isis_adapter import FrrIsisAdapter
from measurement.adapters.frr_ospf_adapter import FrrOspfAdapter
from measurement.mi_main import MIService, _measurement_target
from nodalarc.catalog_closure import FilesystemCatalogReadView
from nodalarc.models.resolved_session import ResolvedRoutingDomain, SourceContext
from nodalarc.resolve_session import SessionResolution, resolve_session_with_assets

from tests.catalog_session_fixtures import build_catalog_session_fixture

ROOT = Path(__file__).resolve().parents[2]


def _resolution(*, protocol: str = "isis", run_id: str | None = "run-mi-0001") -> SessionResolution:
    fixture = build_catalog_session_fixture(
        name="mi-catalog-runtime",
        constellation={"planes": {"count": 1, "sats_per_plane": 2}},
        ground_stations={"stations": ["a"]},
        protocol=protocol,
    )
    return resolve_session_with_assets(
        fixture,
        catalog=FilesystemCatalogReadView(fixture.roots),
        source_context=SourceContext(origin="test.mi", run_id=run_id),
    )


def test_mi_sources_do_not_import_old_session_projection() -> None:
    for relpath in (
        "services/measurement/mi_main.py",
        "services/measurement/flow_manager.py",
    ):
        source = (ROOT / relpath).read_text(encoding="utf-8")
        assert "nodalarc.models.session" not in source
        assert "nodalarc.models.ground_station" not in source
        assert "AddressingScheme" not in source
        assert ".runtime_session" not in source
        assert ".primary_ground_set" not in source


@pytest.mark.parametrize(
    ("protocol", "adapter_type"), [("isis", FrrIsisAdapter), ("ospf", FrrOspfAdapter)]
)
def test_mi_observes_the_engine_and_protocol_the_session_routers_run(
    protocol, adapter_type
) -> None:
    target = _measurement_target(_resolution(protocol=protocol))

    assert target == ("frr", protocol)
    assert isinstance(create_adapter(*target), adapter_type)


def test_mi_has_no_adapter_for_an_engine_protocol_pair_it_cannot_observe() -> None:
    with pytest.raises(ValueError, match="no measurement adapter observes 'static'"):
        create_adapter("frr", "static")


def test_mi_rejects_mixed_adapter_domains_until_multi_adapter_runtime_exists() -> None:
    resolution = _resolution(protocol="isis")
    resolved = resolution.resolved
    domain = ResolvedRoutingDomain(
        domain_id="ospf_domain",
        protocol="ospf",
        node_ids=resolved.routing_domains[0].node_ids,
        capabilities=(),
    )
    mixed = resolved.model_copy(update={"routing_domains": resolved.routing_domains + (domain,)})

    with pytest.raises(ValueError, match="one protocol adapter"):
        _measurement_target(replace(resolution, resolved=mixed))


def test_mi_service_requires_resolved_runtime_identity(tmp_path: Path) -> None:
    resolved = _resolution(run_id=None).resolved

    with pytest.raises(ValueError, match="source_context.run_id"):
        MIService(resolved=resolved, adapter=SimpleNamespace(), db_path=str(tmp_path / "mi.db"))


def test_mi_service_uses_resolved_runtime_identity(tmp_path: Path) -> None:
    resolved = _resolution(run_id="run-mi-0002").resolved

    service = MIService(
        resolved=resolved, adapter=SimpleNamespace(), db_path=str(tmp_path / "mi.db")
    )

    assert service._session_id == "run-mi-0002"
    service._db_conn.close()
