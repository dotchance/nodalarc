"""Tests for inspection endpoints in nodalpath.console.server."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

from fastapi.testclient import TestClient

from nodalpath.console.server import build_app
from nodalpath.console.state import ConsoleState
from nodalpath.integration.node_inspector import NodeInspector
from nodalpath.models.inspection import (
    BindingDiff,
    BindingDiffKind,
    InspectionRun,
    NodeInspectionResult,
)


def _state():
    return ConsoleState(
        session_path="/tmp/test",
        transport="grpc",
        dry_run=False,
        nodes_in_registry=4,
    )


def _mock_inspector() -> NodeInspector:
    """Create a mock NodeInspector with controllable state."""
    inspector = MagicMock(spec=NodeInspector)
    inspector.latest_run = None
    inspector.recent_runs.return_value = []
    inspector.get_run.return_value = None
    return inspector


def _make_run(
    run_id: str = "abc123",
    trigger: str = "operator",
    results: list[NodeInspectionResult] | None = None,
) -> InspectionRun:
    run = InspectionRun(
        run_id=run_id,
        trigger=trigger,
        topology_state_id="state-1",
        started_at=datetime(2026, 3, 1, 14, 30, 0, tzinfo=UTC),
        completed_at=datetime(2026, 3, 1, 14, 30, 1, tzinfo=UTC),
        node_results=results or [],
    )
    return run


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestInspectUnavailable:
    def test_503_when_inspector_none(self):
        app = build_app(_state(), node_inspector=None)
        client = TestClient(app)
        for url in [
            "/api/v1/inspect/runs",
            "/api/v1/inspect/latest",
            "/api/v1/inspect/runs/foo",
        ]:
            r = client.get(url)
            assert r.status_code == 503
            assert "not available" in r.json()["error"]

    def test_503_on_trigger(self):
        app = build_app(_state(), node_inspector=None)
        client = TestClient(app)
        r = client.post("/api/v1/inspect/trigger")
        assert r.status_code == 503


class TestInspectRunsEmpty:
    def test_returns_empty_list(self):
        inspector = _mock_inspector()
        app = build_app(_state(), node_inspector=inspector)
        client = TestClient(app)
        r = client.get("/api/v1/inspect/runs")
        assert r.status_code == 200
        assert r.json()["runs"] == []


class TestInspectRunsPopulated:
    def test_returns_run_summaries(self):
        inspector = _mock_inspector()
        run = _make_run(
            results=[
                NodeInspectionResult(node_id="sat-P00S00", reachable=True),
            ]
        )
        inspector.recent_runs.return_value = [run]
        app = build_app(_state(), node_inspector=inspector)
        client = TestClient(app)
        r = client.get("/api/v1/inspect/runs?n=5")
        assert r.status_code == 200
        data = r.json()
        assert len(data["runs"]) == 1
        assert data["runs"][0]["run_id"] == "abc123"
        assert data["runs"][0]["nodes_inspected"] == 1


class TestInspectRunById:
    def test_returns_full_detail(self):
        inspector = _mock_inspector()
        run = _make_run(
            results=[
                NodeInspectionResult(
                    node_id="sat-P00S00",
                    reachable=True,
                    binding_diffs=[
                        BindingDiff(
                            in_label=100,
                            kind=BindingDiffKind.MISSING,
                            planned_action="swap",
                            planned_out_label=200,
                            planned_out_interface="isl0",
                        ),
                    ],
                ),
            ]
        )
        inspector.get_run.return_value = run
        app = build_app(_state(), node_inspector=inspector)
        client = TestClient(app)
        r = client.get("/api/v1/inspect/runs/abc123")
        assert r.status_code == 200
        data = r.json()
        assert data["run_id"] == "abc123"
        assert len(data["node_results"]) == 1
        nr = data["node_results"][0]
        assert nr["node_id"] == "sat-P00S00"
        assert len(nr["binding_diffs"]) == 1
        assert nr["binding_diffs"][0]["kind"] == "missing"

    def test_404_when_not_found(self):
        inspector = _mock_inspector()
        inspector.get_run.return_value = None
        app = build_app(_state(), node_inspector=inspector)
        client = TestClient(app)
        r = client.get("/api/v1/inspect/runs/nonexistent")
        assert r.status_code == 404


class TestInspectTrigger:
    def test_trigger_returns_run_id(self):
        inspector = _mock_inspector()
        run = _make_run()
        inspector.trigger_operator = AsyncMock(return_value=run)
        app = build_app(_state(), node_inspector=inspector)
        client = TestClient(app)
        r = client.post("/api/v1/inspect/trigger", json={})
        assert r.status_code == 200
        data = r.json()
        assert data["run_id"] == "abc123"
        assert data["status"] == "completed"


class TestInspectLatest:
    def test_returns_null_when_no_runs(self):
        inspector = _mock_inspector()
        inspector.latest_run = None
        app = build_app(_state(), node_inspector=inspector)
        client = TestClient(app)
        r = client.get("/api/v1/inspect/latest")
        assert r.status_code == 200
        assert r.json()["run"] is None

    def test_returns_latest_run(self):
        inspector = _mock_inspector()
        run = _make_run()
        inspector.latest_run = run
        app = build_app(_state(), node_inspector=inspector)
        client = TestClient(app)
        r = client.get("/api/v1/inspect/latest")
        assert r.status_code == 200
        data = r.json()
        assert data["run"]["run_id"] == "abc123"
        assert data["run"]["trigger"] == "operator"
