# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""Scheduler catalog-runtime seam tests."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from nodalarc.resolve_session import load_session_resolution_from_file, resolve_session
from nodalarc.session_identity import require_resolved_session_run_id
from scheduler.__main__ import (
    _dispatch_timing,
    _read_runtime_run_id_file,
    _routing_protocol_label,
    _scheduler_capacity_maps,
)

ROOT = Path(__file__).resolve().parents[2]
SESSION = ROOT / "catalog" / "nodalarc" / "sessions" / "earth-leo-heo-geo-luna-reachability.yaml"


def _resolved():
    return load_session_resolution_from_file(
        SESSION, origin="test.scheduler", run_id="run-test-0001"
    ).resolved


def test_scheduler_runtime_identity_comes_from_source_context_not_session_yaml(
    tmp_path: Path,
) -> None:
    run_id_file = tmp_path / "session_run_id"
    run_id_file.write_text("run-test-0001\n", encoding="utf-8")

    resolution = load_session_resolution_from_file(
        SESSION,
        origin="test.scheduler",
        run_id=_read_runtime_run_id_file(run_id_file),
    )

    assert require_resolved_session_run_id(resolution.resolved) == "run-test-0001"
    assert "run_id" not in resolution.catalog_session.session.model_dump(mode="python")


def test_resolved_runtime_identity_fails_loud_without_operator_run_id() -> None:
    resolved = resolve_session(yaml.safe_load(SESSION.read_text(encoding="utf-8")))

    with pytest.raises(ValueError, match="source_context.run_id"):
        require_resolved_session_run_id(resolved)


def test_scheduler_reads_access_capacity_from_catalog_mount_roles() -> None:
    resolved = _resolved()

    gs_capacities, gs_modes, sat_capacities = _scheduler_capacity_maps(resolved)
    ground_candidates = resolved.ground_candidate_satellites_by_gs()
    candidate_satellites = {sat for sats in ground_candidates.values() for sat in sats}

    assert set(gs_capacities) == set(ground_candidates)
    assert set(gs_modes) == set(ground_candidates)
    assert candidate_satellites.issubset(sat_capacities)
    assert all(capacity > 0 for capacity in gs_capacities.values())
    assert all(capacity > 0 for capacity in sat_capacities.values())


def test_scheduler_dispatch_timing_and_protocol_label_are_catalog_resolved() -> None:
    resolved = _resolved()

    assert _dispatch_timing(resolved) == (10.0, 1.0)
    assert _routing_protocol_label(resolved) == "isis"


def test_catalog_runtime_service_images_bake_static_catalog() -> None:
    consumers = (
        "services/measurement/Dockerfile",
        "services/nodalarc_operator/Dockerfile",
        "services/ome/Dockerfile",
        "services/scheduler/Dockerfile",
        "services/vs_api/Dockerfile",
    )

    for rel in consumers:
        dockerfile = (ROOT / rel).read_text(encoding="utf-8")
        assert "COPY catalog/ catalog/" in dockerfile, rel
        assert "configs/satellite-types" not in dockerfile, rel
        assert "configs/ground-stations" not in dockerfile, rel
        assert "configs/constellations" not in dockerfile, rel


def test_scheduler_dispatch_timing_requires_resolved_time_and_dispatch() -> None:
    resolved = _resolved()

    with pytest.raises(RuntimeError, match="dispatch"):
        _dispatch_timing(resolved.model_copy(update={"dispatch": None}))
    with pytest.raises(RuntimeError, match="time"):
        _dispatch_timing(resolved.model_copy(update={"time": None}))


def test_runtime_run_id_file_fails_loudly_when_missing_or_empty(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError, match="missing"):
        _read_runtime_run_id_file(tmp_path / "missing")

    empty = tmp_path / "session_run_id"
    empty.write_text("\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match="empty"):
        _read_runtime_run_id_file(empty)
