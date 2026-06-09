# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""MI catalog-runtime seam tests."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from measurement import mi_main
from measurement.mi_main import MIService, _mi_stack_config_from_resolved
from nodalarc.models.resolved_session import ResolvedRoutingDomain
from nodalarc.resolve_session import resolve_session

from tests.conftest import build_segment_session_dict

ROOT = Path(__file__).resolve().parents[2]


def _resolved(*, protocol: str = "isis", run_id: str | None = "run-mi-0001"):
    resolved = resolve_session(
        build_segment_session_dict(
            name="mi-catalog-runtime",
            constellation={"planes": {"count": 1, "sats_per_plane": 2}},
            ground_stations={"stations": ["a"]},
            protocol=protocol,
        )
    )
    if run_id is None:
        return resolved
    return resolved.model_copy(
        update={"source_context": resolved.source_context.model_copy(update={"run_id": run_id})}
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


def test_mi_stack_config_is_derived_from_resolved_domain() -> None:
    resolved = _resolved(protocol="isis")

    stack = _mi_stack_config_from_resolved(resolved)

    assert stack.mi_adapter == "frr_isis_adapter"
    assert stack.daemons


def test_mi_rejects_mixed_adapter_domains_until_multi_adapter_runtime_exists() -> None:
    resolved = _resolved(protocol="isis")
    domain = ResolvedRoutingDomain(
        domain_id="ospf_domain",
        protocol="ospf",
        node_ids=resolved.routing_domains[0].node_ids,
        capabilities=(),
    )
    mixed = resolved.model_copy(update={"routing_domains": resolved.routing_domains + (domain,)})

    with pytest.raises(ValueError, match="one protocol adapter"):
        _mi_stack_config_from_resolved(mixed)


def test_mi_service_requires_resolved_runtime_identity(monkeypatch, tmp_path: Path) -> None:
    resolved = _resolved(run_id=None)
    stack = _mi_stack_config_from_resolved(resolved)
    monkeypatch.setattr(mi_main, "create_adapter", lambda _name: SimpleNamespace())

    with pytest.raises(ValueError, match="source_context.run_id"):
        MIService(resolved=resolved, stack_config=stack, db_path=str(tmp_path / "mi.db"))


def test_mi_service_uses_resolved_runtime_identity(monkeypatch, tmp_path: Path) -> None:
    resolved = _resolved(run_id="run-mi-0002")
    stack = _mi_stack_config_from_resolved(resolved)
    monkeypatch.setattr(mi_main, "create_adapter", lambda _name: SimpleNamespace())

    service = MIService(resolved=resolved, stack_config=stack, db_path=str(tmp_path / "mi.db"))

    assert service._session_id == "run-mi-0002"
    service._db_conn.close()
