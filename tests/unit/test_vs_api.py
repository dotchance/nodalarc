"""Unit tests for VS-API — state management via SessionContext and snapshot construction."""

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest
from nodalarc.db.queries import (
    insert_convergence_result,
    insert_link_up,
    insert_snapshot,
    query_nearest_snapshot,
)
from nodalarc.db.schema import create_tables
from nodalarc.models.events import EphemerisNodeTLE, SessionEphemeris
from nodalarc.models.link_events import LinkUp
from nodalarc.models.metrics import ConvergenceResult
from nodalarc.models.vs_api import (
    LinkDecisionTrace,
    LinkState,
    NetworkHealth,
    NodeState,
    StateSnapshot,
)
from nodalarc.nats_channels import STREAM_OME_EVENTS
from vs_api.session_context import SessionContext, _link_key

ISS_TLE_EPOCH = 1615896900.000275
ISS_TLE_LINE_1 = "1 25544U 98067A   21075.51041667  .00001264  00000-0  29660-4 0  9993"
ISS_TLE_LINE_2 = "2 25544  51.6442  21.5417 0002426  95.1670  21.8444 15.48974333273145"


def _session_yaml_text(name: str = "demo-36-ospf.yaml") -> str:
    return Path("configs/sessions", name).read_text(encoding="utf-8")


def _constellation_cr(
    *,
    phase: str = "Ready",
    generation: int = 2,
    observed_generation: int = 2,
    ready_pods: int = 43,
    pod_count: int = 43,
    wired_pods: int = 43,
    session_yaml: str | None = None,
    session_run_id: str = "run-test-0001",
    session_name: str | None = "demo-36-ospf",
) -> dict:
    status = {
        "phase": phase,
        "observedGeneration": observed_generation,
        "readyPods": ready_pods,
        "podCount": pod_count,
        "wiredPods": wired_pods,
        "sessionRunId": session_run_id,
    }
    if session_name is not None:
        status["sessionName"] = session_name
    return {
        "metadata": {"generation": generation},
        "spec": {"sessionYaml": session_yaml if session_yaml is not None else _session_yaml_text()},
        "status": status,
    }


def _make_provenance(**overrides):
    provenance = {
        "geometry_authority": "ome",
        "authority_source": "visibility_event",
        "authority_sim_time": "2026-01-01T00:00:00+00:00",
        "authority_sequence": None,
        "authority_age_ms": 0.0,
        "range_km": 1500.0,
        "orbital_one_way_ms": 5.0,
        "substrate_rtt_ms": 0.0,
        "substrate_one_way_ms": 0.0,
        "netem_one_way_ms": 5.0,
        "rtt_to_one_way_policy": "half-rtt",
    }
    provenance.update(overrides)
    return provenance


def _make_link_up_event(node_a="sat-P00S00", node_b="sat-P00S01", **overrides):
    """Create a complete LinkUp event dict with all required fields."""
    event = {
        "node_a": node_a,
        "node_b": node_b,
        "interface_a": "isl0",
        "interface_b": "isl1",
        "latency_ms": 5.0,
        "bandwidth_mbps": 1000.0,
        "range_km": 1500.0,
        "reason": "vis_gained",
        "sim_time": datetime.now(UTC).isoformat(),
        "link_type": "isl",
        "provenance": _make_provenance(),
    }
    event.update(overrides)
    return event


def _make_link_down_event(node_a="sat-P00S00", node_b="sat-P00S01", **overrides):
    """Create a complete LinkDown event dict with all required fields."""
    event = {
        "node_a": node_a,
        "node_b": node_b,
        "interface_a": "isl0",
        "interface_b": "isl1",
        "reason": "vis_lost",
        "sim_time": datetime.now(UTC).isoformat(),
        "link_type": "isl",
    }
    event.update(overrides)
    return event


class TestLinkKey:
    """_link_key produces deterministic canonical keys."""

    def test_ordered(self):
        assert _link_key("a", "b") == "a:b"

    def test_reversed_produces_same_key(self):
        assert _link_key("b", "a") == "a:b"


class TestApiAttribution:
    """Public API exposes project provenance."""

    def test_about_returns_project_attribution(self):
        import vs_api.main as m
        from nodalarc.project_info import project_version

        payload = m.about()

        assert payload["name"] == "NodalArc"
        assert payload["version"] == project_version()
        assert payload["revision"]
        assert payload["build_date"]
        assert payload["author"] == ".chance (dotchance)"
        assert payload["source"] == "https://github.com/dotchance/nodalarc"
        assert payload["notice"] == "See NOTICE and THIRD_PARTY_NOTICES.md."

    def test_about_uses_runtime_build_metadata(self, monkeypatch):
        import vs_api.main as m

        monkeypatch.setenv("NODALARC_VERSION", "9.8.7")
        monkeypatch.setenv("NODALARC_BUILD_REVISION", "abc1234")
        monkeypatch.setenv("NODALARC_BUILD_DATE", "2026-05-19T22:00:00Z")

        payload = m.about()

        assert payload["version"] == "9.8.7"
        assert payload["revision"] == "abc1234"
        assert payload["build_date"] == "2026-05-19T22:00:00Z"


class TestConstellationCRReadiness:
    """VS-API only trusts ready, generation-consistent session CR state."""

    def test_extract_ready_cr_session_accepts_current_ready_generation(self):
        import vs_api.main as m

        ready = m._extract_ready_cr_session(_constellation_cr())

        assert ready is not None
        assert ready.session_id == "run-test-0001"
        assert ready.session_name == "demo-36-ospf"
        assert ready.generation == 2
        assert ready.session.session.name == "demo-36-ospf"

    def test_extract_ready_cr_session_requires_runtime_identity(self):
        import vs_api.main as m

        with pytest.raises(ValueError, match="sessionRunId"):
            m._extract_ready_cr_session(_constellation_cr(session_run_id=""))

    def test_extract_ready_cr_session_rejects_observed_generation_mismatch(self):
        import vs_api.main as m

        ready = m._extract_ready_cr_session(_constellation_cr(generation=3, observed_generation=2))

        assert ready is None

    def test_generation_current_helper_rejects_stale_error_status(self):
        import vs_api.main as m

        cr = _constellation_cr(phase="Error", generation=3, observed_generation=2)

        assert m._cr_status_observes_current_generation(cr) is False

    def test_generation_current_helper_accepts_current_error_status(self):
        import vs_api.main as m

        cr = _constellation_cr(phase="Error", generation=3, observed_generation=3)

        assert m._cr_status_observes_current_generation(cr) is True

    def test_extract_ready_cr_session_rejects_non_ready_phase(self):
        import vs_api.main as m

        ready = m._extract_ready_cr_session(_constellation_cr(phase="Wiring"))

        assert ready is None

    def test_extract_ready_cr_session_rejects_incomplete_pod_status(self):
        import vs_api.main as m

        ready = m._extract_ready_cr_session(_constellation_cr(ready_pods=42, pod_count=43))

        assert ready is None

    def test_extract_ready_cr_session_rejects_incomplete_wiring_status(self):
        import vs_api.main as m

        ready = m._extract_ready_cr_session(_constellation_cr(wired_pods=42, pod_count=43))

        assert ready is None

    def test_extract_ready_cr_session_requires_status_session_name(self):
        import vs_api.main as m

        with pytest.raises(ValueError, match="sessionName"):
            m._extract_ready_cr_session(_constellation_cr(session_name=None))

    def test_extract_ready_cr_session_rejects_session_name_mismatch(self):
        import vs_api.main as m

        with pytest.raises(ValueError, match="status.sessionName"):
            m._extract_ready_cr_session(_constellation_cr(session_name="wrong-name"))

    def test_extract_ready_cr_session_fails_loudly_without_session_yaml(self):
        import vs_api.main as m

        with pytest.raises(ValueError, match="sessionYaml"):
            m._extract_ready_cr_session(_constellation_cr(session_yaml=""))


class TestStateSnapshot:
    """Test that SessionContext state manipulation produces correct snapshots."""

    def test_link_up_adds_to_state(self):
        ctx = SessionContext.__new__(SessionContext)
        ctx._init_state_only()
        event = _make_link_up_event()
        key = _link_key(event["node_a"], event["node_b"])
        with ctx.state_lock:
            ctx.links[key] = LinkState(
                node_a=event["node_a"],
                node_b=event["node_b"],
                state="active",
                link_type="intra_plane_isl",
                link_reason=event["reason"],
                latency_ms=event["latency_ms"],
                bandwidth_mbps=event["bandwidth_mbps"],
                range_km=event["range_km"],
                traffic_load_pct=None,
                interface_a=event["interface_a"],
                interface_b=event["interface_b"],
            )
        assert key in ctx.links
        assert ctx.links[key].latency_ms == 5.0

    def test_link_down_removes_link(self):
        ctx = SessionContext.__new__(SessionContext)
        ctx._init_state_only()
        event_up = _make_link_up_event()
        key = _link_key(event_up["node_a"], event_up["node_b"])
        with ctx.state_lock:
            ctx.links[key] = LinkState(
                node_a=event_up["node_a"],
                node_b=event_up["node_b"],
                state="active",
                link_type="intra_plane_isl",
                link_reason="vis_gained",
                latency_ms=5.0,
                bandwidth_mbps=1000.0,
                range_km=1500.0,
                traffic_load_pct=None,
                interface_a="isl0",
                interface_b="isl1",
            )
        assert key in ctx.links
        with ctx.state_lock:
            ctx.links.pop(key, None)
        assert key not in ctx.links

    def test_latency_update(self):
        ctx = SessionContext.__new__(SessionContext)
        ctx._init_state_only()
        event = _make_link_up_event(latency_ms=5.0, range_km=1500.0)
        key = _link_key(event["node_a"], event["node_b"])
        with ctx.state_lock:
            ctx.links[key] = LinkState(
                node_a=event["node_a"],
                node_b=event["node_b"],
                state="active",
                link_type="intra_plane_isl",
                link_reason="vis_gained",
                latency_ms=5.0,
                bandwidth_mbps=1000.0,
                range_km=1500.0,
                traffic_load_pct=None,
                interface_a="isl0",
                interface_b="isl1",
            )
            existing = ctx.links[key]
            ctx.links[key] = existing.model_copy(update={"latency_ms": 10.0, "range_km": 3000.0})
        assert ctx.links[key].latency_ms == 10.0
        assert ctx.links[key].range_km == 3000.0


class TestSnapshotModel:
    """Test StateSnapshot Pydantic model serialization."""

    def test_full_snapshot_round_trip(self):
        snap = StateSnapshot(
            sim_time=datetime.now(UTC),
            wall_time=datetime.now(UTC),
            schema_version=1,
            session_id="run-test-0001",
            nodes=[
                NodeState(
                    node_id="sat-P00S00",
                    node_type="satellite",
                    lat_deg=0.0,
                    lon_deg=0.0,
                    alt_km=550.0,
                    vel_x_km_s=None,
                    vel_y_km_s=None,
                    vel_z_km_s=None,
                    plane=0,
                    slot=0,
                    routing_area=None,
                    neighbor_count=2,
                    isl_count=2,
                    gnd_count=0,
                    prefix=None,
                    min_elevation_deg=None,
                    beam_falloff_exponent=None,
                )
            ],
            links=[
                LinkState(
                    node_a="sat-P00S00",
                    node_b="sat-P00S01",
                    state="active",
                    link_type="intra_plane_isl",
                    link_reason="vis_gained",
                    latency_ms=5.0,
                    bandwidth_mbps=1000.0,
                    range_km=1500.0,
                    traffic_load_pct=None,
                    interface_a="isl0",
                    interface_b="isl1",
                )
            ],
            traced_paths=[],
            active_flows=[],
            recent_events=[],
            network_health=NetworkHealth(
                status="converged",
                converging_since_ms=None,
                unreachable_flows=0,
                last_convergence_ms=None,
            ),
            routing_stack="isis-te",
            constellation_name="test",
            session_status="ready",
            session_status_detail=None,
            playback_paused=False,
            playback_speed=1.0,
            stale=False,
        )
        dumped = snap.model_dump_json()
        loaded = json.loads(dumped)
        assert loaded["schema_version"] == 1
        assert loaded["session_id"] == "run-test-0001"
        assert len(loaded["nodes"]) == 1
        assert len(loaded["links"]) == 1
        assert loaded["nodes"][0]["node_id"] == "sat-P00S00"
        assert loaded["links"][0]["latency_ms"] == 5.0

    def test_snapshot_is_frozen(self):
        snap = StateSnapshot(
            sim_time=datetime.now(UTC),
            wall_time=datetime.now(UTC),
            schema_version=1,
            session_id="run-test-0001",
            nodes=[],
            links=[],
            traced_paths=[],
            active_flows=[],
            recent_events=[],
            network_health=NetworkHealth(
                status="converged",
                converging_since_ms=None,
                unreachable_flows=0,
                last_convergence_ms=None,
            ),
            routing_stack=None,
            constellation_name=None,
            session_status=None,
            session_status_detail=None,
            playback_paused=False,
            playback_speed=1.0,
            stale=False,
        )
        with pytest.raises(Exception, match="frozen"):
            snap.stale = True


class TestSQLiteQueries:
    """Test SQLite query functions with a real in-memory DB."""

    def test_query_link_events(self):
        conn = sqlite3.connect(":memory:")
        create_tables(conn)
        event = LinkUp(
            sim_time=datetime(2025, 1, 1, tzinfo=UTC),
            wall_time=datetime(2025, 1, 1, tzinfo=UTC),
            node_a="sat-P00S00",
            node_b="sat-P00S01",
            link_type="isl",
            interface_a="isl0",
            interface_b="isl1",
            latency_ms=5.0,
            bandwidth_mbps=1000.0,
            range_km=1500.0,
            reason="vis_gained",
        )
        insert_link_up(conn, event)
        from nodalarc.db.queries import query_link_events

        results = query_link_events(conn)
        assert len(results) >= 1
        conn.close()

    def test_query_convergence_events(self):
        conn = sqlite3.connect(":memory:")
        create_tables(conn)
        t = datetime(2025, 1, 1, tzinfo=UTC)
        result = ConvergenceResult(
            event_id="test-001",
            converged=True,
            duration_ms=150.0,
            packets_lost=0,
            packets_sent=100,
            sim_time_start=t,
            sim_time_end=t,
            wall_time_start=t,
            wall_time_end=t,
        )
        insert_convergence_result(conn, result)
        from nodalarc.db.queries import query_convergence_events

        results = query_convergence_events(conn)
        assert len(results) >= 1
        conn.close()


class TestSnapshotStorage:
    """Test SQLite snapshot storage for historical playback."""

    def test_insert_and_query_snapshot(self):
        conn = sqlite3.connect(":memory:")
        create_tables(conn)
        insert_snapshot(
            conn,
            sim_time="2025-01-01T00:00:00+00:00",
            wall_time="2025-01-01T00:00:00+00:00",
            snapshot_json='{"nodes":[],"links":[]}',
        )
        result = query_nearest_snapshot(conn, "2025-01-01T00:00:00+00:00")
        assert result is not None
        data = json.loads(result["snapshot_json"])
        assert data["nodes"] == []
        conn.close()

    def test_nearest_snapshot_selection(self):
        conn = sqlite3.connect(":memory:")
        create_tables(conn)
        for t in [
            "2025-01-01T00:00:00+00:00",
            "2025-01-01T00:05:00+00:00",
            "2025-01-01T00:10:00+00:00",
        ]:
            insert_snapshot(conn, sim_time=t, wall_time=t, snapshot_json=f'{{"sim_time":"{t}"}}')
        result = query_nearest_snapshot(conn, "2025-01-01T00:04:00+00:00")
        assert result is not None
        data = json.loads(result["snapshot_json"])
        assert "00:05:00" in data["sim_time"] or "00:00:00" in data["sim_time"]
        conn.close()

    def test_no_snapshots_returns_none(self):
        conn = sqlite3.connect(":memory:")
        create_tables(conn)
        result = query_nearest_snapshot(conn, "2025-01-01T00:00:00+00:00")
        assert result is None
        conn.close()


class TestSessionContextInit:
    """Test that SessionContext state initialization is correct."""

    def test_empty_context_has_no_links(self):
        ctx = SessionContext.__new__(SessionContext)
        ctx._init_state_only()
        assert len(ctx.links) == 0
        assert len(ctx.nodes) == 0
        assert ctx.playback_paused is False
        assert ctx.playback_speed == 1.0

    def test_is_stale_false_initially(self):
        ctx = SessionContext.__new__(SessionContext)
        ctx._init_state_only()
        assert ctx.is_stale() is False


class TestEphemerisPositionPropagation:
    def test_tle_ephemeris_updates_satellite_position(self):
        ctx = SessionContext.__new__(SessionContext)
        ctx._init_state_only()
        ctx.cached_ephemeris_obj = SessionEphemeris(
            epoch_id=0,
            sim_time=datetime.fromtimestamp(ISS_TLE_EPOCH, UTC),
            epoch_unix=ISS_TLE_EPOCH,
            nodes={
                "sat-P00S00": EphemerisNodeTLE(
                    tle_line_1=ISS_TLE_LINE_1,
                    tle_line_2=ISS_TLE_LINE_2,
                    plane=0,
                    slot=0,
                    norad_id=25544,
                )
            },
        )

        ctx._propagate_positions_from_time(datetime.fromtimestamp(ISS_TLE_EPOCH, UTC).isoformat())

        node = ctx.nodes["sat-P00S00"]
        assert node.lat_deg == pytest.approx(44.4565, abs=1e-3)
        assert node.lon_deg == pytest.approx(152.9363, abs=1e-3)
        assert node.alt_km > 400.0


class TestLinkDecisionTraceState:
    """VS-API retains active-link decision traces for auditability."""

    def test_link_up_records_decision_trace(self):
        ctx = SessionContext.__new__(SessionContext)
        ctx._init_state_only()

        import asyncio
        from unittest.mock import MagicMock

        event = _make_link_up_event()
        msg = MagicMock()
        msg.data = json.dumps(event).encode()

        asyncio.run(ctx._on_link_up(msg))

        key = _link_key("sat-P00S00", "sat-P00S01")
        trace = ctx.link_decision_traces[key]
        assert isinstance(trace, LinkDecisionTrace)
        assert trace.geometry_authority == "ome"
        assert trace.authority_source == "visibility_event"
        assert trace.range_km == 1500.0
        assert trace.netem_one_way_ms == 5.0

    def test_link_up_requires_provenance(self):
        ctx = SessionContext.__new__(SessionContext)
        ctx._init_state_only()

        import asyncio
        from unittest.mock import MagicMock

        event = _make_link_up_event()
        event.pop("provenance")
        msg = MagicMock()
        msg.data = json.dumps(event).encode()

        with pytest.raises(ValueError, match="provenance"):
            asyncio.run(ctx._on_link_up(msg))

    def test_link_up_rejects_contradictory_provenance(self):
        ctx = SessionContext.__new__(SessionContext)
        ctx._init_state_only()

        import asyncio
        from unittest.mock import MagicMock

        event = _make_link_up_event(
            provenance=_make_provenance(range_km=1499.0),
        )
        msg = MagicMock()
        msg.data = json.dumps(event).encode()

        with pytest.raises(ValueError, match="range_km disagrees"):
            asyncio.run(ctx._on_link_up(msg))

    def test_snapshot_records_ome_authority_trace(self):
        ctx = SessionContext.__new__(SessionContext)
        ctx._init_state_only()

        import asyncio
        from unittest.mock import MagicMock

        msg = MagicMock()
        msg.data = json.dumps(
            {
                "snapshot_seq": 12,
                "sim_time": "2025-01-01T00:00:00+00:00",
                "interval_s": 1.0,
                "epoch_id": 0,
                "links": [
                    {
                        "node_a": "sat-P00S00",
                        "node_b": "sat-P00S01",
                        "interface_a": "isl0",
                        "interface_b": "isl1",
                        "admin": "UP",
                        "carrier": "UP",
                        "routing": "UNKNOWN",
                        "range_km": 900.0,
                        "latency_ms": 3.0,
                        "bandwidth_mbps": 1000.0,
                        "link_type": "isl",
                        "sim_time": "2025-01-01T00:00:00+00:00",
                    }
                ],
            }
        ).encode()

        asyncio.run(ctx._on_link_state_snapshot(msg))

        trace = ctx.link_decision_traces[_link_key("sat-P00S00", "sat-P00S01")]
        assert trace.authority_source == "link_state_snapshot"
        assert trace.authority_sequence == 12
        assert trace.range_km == 900.0
        assert trace.orbital_one_way_ms == 3.0
        assert trace.netem_one_way_ms is None

    def test_latency_update_refreshes_decision_trace(self):
        ctx = SessionContext.__new__(SessionContext)
        ctx._init_state_only()

        import asyncio
        from unittest.mock import MagicMock

        up = MagicMock()
        up.data = json.dumps(_make_link_up_event()).encode()
        asyncio.run(ctx._on_link_up(up))

        latency = MagicMock()
        latency.data = json.dumps(
            {
                "node_a": "sat-P00S00",
                "node_b": "sat-P00S01",
                "latency_ms": 6.0,
                "range_km": 1800.0,
                "provenance": _make_provenance(
                    authority_source="link_state_snapshot",
                    authority_sequence=99,
                    range_km=1800.0,
                    orbital_one_way_ms=6.0,
                    netem_one_way_ms=6.0,
                ),
            }
        ).encode()

        asyncio.run(ctx._on_latency_update(latency))

        key = _link_key("sat-P00S00", "sat-P00S01")
        assert ctx.links[key].latency_ms == 6.0
        assert ctx.link_decision_traces[key].authority_sequence == 99
        assert ctx.link_decision_traces[key].range_km == 1800.0


class TestSubscriberResilience:
    """Test that the NATS subscriber loop survives missing optional streams.

    The NODALARC_MI stream only exists when MI is enabled. The subscriber
    loop must not crash when it fails to subscribe to this stream —
    all other subscriptions (ephemeris, clock, links) must continue
    working. This was a production bug: the MI subscription failure
    killed the entire subscriber task and all working subscriptions,
    causing STALE DATA in the VF.
    """

    def test_subscriber_survives_missing_mi_stream(self):
        """Verify that a missing NODALARC_MI stream doesn't kill the
        subscriber task. The MI subscription is wrapped in try/except
        and logged at INFO level."""
        import asyncio
        from unittest.mock import AsyncMock, MagicMock

        from nats.js.errors import NotFoundError

        ctx = SessionContext.__new__(SessionContext)
        ctx._init_state_only()

        nc = MagicMock()
        js_mock = MagicMock()

        subscribe_calls = []

        async def mock_subscribe(subject, **kwargs):
            if "NODALARC_MI" in kwargs.get("stream", ""):
                raise NotFoundError(code=404, err_code=10059, description="stream not found")
            sub = AsyncMock()
            sub.unsubscribe = AsyncMock()
            subscribe_calls.append(subject)
            return sub

        js_mock.subscribe = mock_subscribe
        nc.jetstream.return_value = js_mock

        async def run():
            await ctx.start(nc, mode="recovery")
            await asyncio.sleep(0.1)
            assert not ctx._stopped, "Subscriber should still be alive"
            assert len(ctx._subscriptions) > 0, "Some subscriptions should have succeeded"
            await ctx.stop()

        asyncio.run(run())
        assert len(subscribe_calls) > 5, (
            f"Expected 6+ successful subscriptions, got {len(subscribe_calls)}"
        )

    def test_subscriber_crashes_on_required_stream_failure(self):
        """If a required stream (NODALARC_OME, NODALARC_LINKS, etc.)
        fails, the subscriber SHOULD crash — fail loud."""
        import asyncio
        from unittest.mock import AsyncMock, MagicMock

        from nats.js.errors import NotFoundError

        ctx = SessionContext.__new__(SessionContext)
        ctx._init_state_only()

        nc = MagicMock()
        js_mock = MagicMock()

        async def mock_subscribe(subject, **kwargs):
            if STREAM_OME_EVENTS in kwargs.get("stream", ""):
                raise NotFoundError(code=404, err_code=10059, description="stream not found")
            sub = AsyncMock()
            sub.unsubscribe = AsyncMock()
            return sub

        js_mock.subscribe = mock_subscribe
        nc.jetstream.return_value = js_mock

        async def run():
            await ctx.start(nc, mode="recovery")
            with pytest.raises(NotFoundError):
                await asyncio.wait_for(ctx._subscriber_task, timeout=0.5)
            return ctx._subscriber_task.done()

        task_done = asyncio.run(run())
        assert task_done, "Subscriber should have crashed on required stream failure"

    def test_snapshot_seq_rejects_stale(self):
        """Snapshots with seq <= last are discarded to prevent jitter."""
        ctx = SessionContext.__new__(SessionContext)
        ctx._init_state_only()
        ctx.last_snapshot_seq = 100

        import asyncio
        from unittest.mock import MagicMock

        msg = MagicMock()
        msg.data = b'{"snapshot_seq": 50, "sim_time": "2025-01-01T00:00:00+00:00", "links": [], "interval_s": 5.0, "epoch_id": 0}'

        asyncio.run(ctx._on_link_state_snapshot(msg))
        assert ctx.last_snapshot_seq == 100, "Stale snapshot should not update seq"
        assert len(ctx.links) == 0

    def test_snapshot_seq_accepts_newer(self):
        """Snapshots with seq > last are applied."""
        ctx = SessionContext.__new__(SessionContext)
        ctx._init_state_only()
        ctx.last_snapshot_seq = 10

        import asyncio
        from unittest.mock import MagicMock

        msg = MagicMock()
        msg.data = b'{"snapshot_seq": 11, "sim_time": "2025-01-01T00:00:00+00:00", "links": [], "interval_s": 5.0, "epoch_id": 0}'

        asyncio.run(ctx._on_link_state_snapshot(msg))
        assert ctx.last_snapshot_seq == 11

    def test_snapshot_uses_authoritative_range_and_interfaces(self):
        """VS-API must not derive range from latency or discard interfaces."""
        ctx = SessionContext.__new__(SessionContext)
        ctx._init_state_only()

        import asyncio
        from unittest.mock import MagicMock

        msg = MagicMock()
        msg.data = json.dumps(
            {
                "snapshot_seq": 12,
                "sim_time": "2025-01-01T00:00:00+00:00",
                "interval_s": 1.0,
                "epoch_id": 0,
                "links": [
                    {
                        "node_a": "sat-P00S00",
                        "node_b": "sat-P00S01",
                        "interface_a": "isl0",
                        "interface_b": "isl1",
                        "admin": "UP",
                        "carrier": "UP",
                        "routing": "UNKNOWN",
                        "range_km": 900.0,
                        "latency_ms": 3.0,
                        "bandwidth_mbps": 1000.0,
                        "link_type": "isl",
                        "sim_time": "2025-01-01T00:00:00+00:00",
                    }
                ],
            }
        ).encode()

        asyncio.run(ctx._on_link_state_snapshot(msg))

        link = ctx.links[_link_key("sat-P00S00", "sat-P00S01")]
        assert link.range_km == 900.0
        assert link.interface_a == "isl0"
        assert link.interface_b == "isl1"
        assert ctx.last_snapshot_seq == 12

    def test_malformed_snapshot_does_not_advance_sequence_or_replace_state(self):
        ctx = SessionContext.__new__(SessionContext)
        ctx._init_state_only()
        ctx.last_snapshot_seq = 11
        existing_key = _link_key("sat-P00S00", "sat-P00S01")
        ctx.links[existing_key] = LinkState(
            node_a="sat-P00S00",
            node_b="sat-P00S01",
            state="active",
            link_type="intra_plane_isl",
            link_reason="vis_gained",
            latency_ms=5.0,
            bandwidth_mbps=1000.0,
            range_km=1500.0,
            traffic_load_pct=None,
            interface_a="isl0",
            interface_b="isl1",
        )

        import asyncio
        from unittest.mock import MagicMock

        msg = MagicMock()
        msg.data = json.dumps(
            {
                "snapshot_seq": 12,
                "sim_time": "2025-01-01T00:00:00+00:00",
                "interval_s": 1.0,
                "epoch_id": 0,
                "links": [
                    {
                        "node_a": "sat-P00S00",
                        "node_b": "sat-P00S01",
                        "interface_a": "isl0",
                        "interface_b": "isl1",
                        "admin": "UP",
                        "carrier": "UP",
                        "routing": "UNKNOWN",
                        "latency_ms": 3.0,
                        "bandwidth_mbps": 1000.0,
                        "link_type": "isl",
                        "sim_time": "2025-01-01T00:00:00+00:00",
                    }
                ],
            }
        ).encode()

        with pytest.raises(ValueError, match="missing required authoritative field"):
            asyncio.run(ctx._on_link_state_snapshot(msg))

        assert ctx.last_snapshot_seq == 11
        assert ctx.links[existing_key].range_km == 1500.0

    def test_link_up_requires_explicit_link_type(self):
        ctx = SessionContext.__new__(SessionContext)
        ctx._init_state_only()

        import asyncio
        from unittest.mock import MagicMock

        event = _make_link_up_event()
        event.pop("link_type")
        msg = MagicMock()
        msg.data = json.dumps(event).encode()

        with pytest.raises(ValueError, match="link_type"):
            asyncio.run(ctx._on_link_up(msg))

    def test_link_down_requires_explicit_link_type(self):
        ctx = SessionContext.__new__(SessionContext)
        ctx._init_state_only()

        import asyncio
        from unittest.mock import MagicMock

        event = _make_link_down_event()
        event.pop("link_type")
        msg = MagicMock()
        msg.data = json.dumps(event).encode()

        with pytest.raises(ValueError, match="link_type"):
            asyncio.run(ctx._on_link_down(msg))

    def test_ready_requires_ephemeris_and_snapshot(self):
        """is_ready() only returns True when both ephemeris AND snapshot
        have been received — prevents ghost snapshot race."""
        ctx = SessionContext.__new__(SessionContext)
        ctx._init_state_only()

        assert not ctx.is_ready()

        ctx._snapshot_received = True
        ctx._check_ready()
        assert not ctx.is_ready(), "Should not be ready without ephemeris"

        ctx._ephemeris_received = True
        ctx._check_ready()
        assert ctx.is_ready(), "Should be ready with both"


# ---------------------------------------------------------------------------
# On-demand debug: ref-counting and cleanup
# ---------------------------------------------------------------------------


class TestDebugRefCounting:
    """Tests for VS-API debug source ref-counting across WebSocket clients."""

    def setup_method(self):
        import vs_api.main as m

        self._m = m
        self._orig_sources = m._debug_sources.copy()
        self._orig_clients = m._debug_clients.copy()
        m._debug_sources = set()
        m._debug_clients = {}
        m._debug_sub = None
        m._debug_events.clear()

    def teardown_method(self):
        self._m._debug_sources = self._orig_sources
        self._m._debug_clients = self._orig_clients

    def test_handle_debug_stream_adds_to_client_and_sources(self):
        import asyncio
        from unittest.mock import AsyncMock, MagicMock

        m = self._m
        nc_mock = MagicMock()
        resp_mock = MagicMock()
        resp_mock.data = b'{"status": "ok", "level": "debug"}'
        nc_mock.request = AsyncMock(return_value=resp_mock)
        nc_mock.jetstream = MagicMock(
            return_value=MagicMock(
                subscribe=AsyncMock(return_value=MagicMock()),
            )
        )
        m._nats_connection = nc_mock

        asyncio.run(
            m._handle_ws_debug_command(1001, {"action": "debug_stream", "sources": ["scheduler"]})
        )

        assert "scheduler" in m._debug_sources
        assert "scheduler" in m._debug_clients.get(1001, set())

    def test_two_clients_same_source_ref_counted(self):
        import asyncio
        from unittest.mock import AsyncMock, MagicMock

        m = self._m
        nc_mock = MagicMock()
        resp_mock = MagicMock()
        resp_mock.data = b'{"status": "ok", "level": "debug"}'
        nc_mock.request = AsyncMock(return_value=resp_mock)
        nc_mock.jetstream = MagicMock(
            return_value=MagicMock(
                subscribe=AsyncMock(return_value=MagicMock()),
            )
        )
        m._nats_connection = nc_mock

        asyncio.run(
            m._handle_ws_debug_command(1001, {"action": "debug_stream", "sources": ["scheduler"]})
        )
        asyncio.run(
            m._handle_ws_debug_command(1002, {"action": "debug_stream", "sources": ["scheduler"]})
        )

        assert "scheduler" in m._debug_sources
        assert "scheduler" in m._debug_clients[1001]
        assert "scheduler" in m._debug_clients[1002]

    def test_first_client_disconnect_keeps_source_active(self):
        import asyncio
        from unittest.mock import AsyncMock, MagicMock

        m = self._m
        nc_mock = MagicMock()
        resp_mock = MagicMock()
        resp_mock.data = b'{"status": "ok", "level": "debug"}'
        nc_mock.request = AsyncMock(return_value=resp_mock)
        nc_mock.jetstream = MagicMock(
            return_value=MagicMock(
                subscribe=AsyncMock(return_value=MagicMock()),
            )
        )
        m._nats_connection = nc_mock

        asyncio.run(
            m._handle_ws_debug_command(1001, {"action": "debug_stream", "sources": ["scheduler"]})
        )
        asyncio.run(
            m._handle_ws_debug_command(1002, {"action": "debug_stream", "sources": ["scheduler"]})
        )
        asyncio.run(m._cleanup_debug_client(1001))

        assert "scheduler" in m._debug_sources, (
            "Source should stay active — client 1002 still wants it"
        )
        assert 1001 not in m._debug_clients

    def test_last_client_disconnect_disables_source(self):
        import asyncio
        from unittest.mock import AsyncMock, MagicMock

        m = self._m
        nc_mock = MagicMock()
        resp_mock = MagicMock()
        resp_mock.data = b'{"status": "ok", "level": "debug"}'
        nc_mock.request = AsyncMock(return_value=resp_mock)
        nc_mock.jetstream = MagicMock(
            return_value=MagicMock(
                subscribe=AsyncMock(return_value=MagicMock()),
            )
        )
        m._nats_connection = nc_mock

        asyncio.run(
            m._handle_ws_debug_command(1001, {"action": "debug_stream", "sources": ["scheduler"]})
        )
        asyncio.run(m._cleanup_debug_client(1001))

        assert "scheduler" not in m._debug_sources, "Source should be disabled — no clients left"

    def test_enable_failed_source_not_added(self):
        import asyncio
        from unittest.mock import AsyncMock, MagicMock

        m = self._m
        nc_mock = MagicMock()
        resp_mock = MagicMock()
        resp_mock.data = b'{"status": "error", "error": "service not running"}'
        nc_mock.request = AsyncMock(return_value=resp_mock)
        m._nats_connection = nc_mock
        m._publish_system_ops_event = AsyncMock()

        asyncio.run(
            m._handle_ws_debug_command(1001, {"action": "debug_stream", "sources": ["scheduler"]})
        )

        assert "scheduler" not in m._debug_sources
        assert "scheduler" not in m._debug_clients.get(1001, set())
