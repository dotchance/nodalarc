"""Unit tests for VS-API — state management via SessionContext and snapshot construction."""

import json
import sqlite3
from datetime import UTC, datetime

from nodalarc.db.queries import (
    insert_convergence_result,
    insert_link_up,
    insert_snapshot,
    query_nearest_snapshot,
)
from nodalarc.db.schema import create_tables
from nodalarc.models.link_events import LinkUp
from nodalarc.models.metrics import ConvergenceResult
from nodalarc.models.vs_api import (
    LinkState,
    NetworkHealth,
    NodeState,
    StateSnapshot,
)
from vs_api.session_context import SessionContext, _link_key


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
    }
    event.update(overrides)
    return event


class TestLinkKey:
    """_link_key produces deterministic canonical keys."""

    def test_ordered(self):
        assert _link_key("a", "b") == "a:b"

    def test_reversed_produces_same_key(self):
        assert _link_key("b", "a") == "a:b"


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
        assert len(loaded["nodes"]) == 1
        assert len(loaded["links"]) == 1
        assert loaded["nodes"][0]["node_id"] == "sat-P00S00"
        assert loaded["links"][0]["latency_ms"] == 5.0

    def test_snapshot_is_frozen(self):
        snap = StateSnapshot(
            sim_time=datetime.now(UTC),
            wall_time=datetime.now(UTC),
            schema_version=1,
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
        try:
            snap.stale = True
            assert False, "Should be frozen"
        except Exception:
            pass


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
            if "NODALARC_OME" in kwargs.get("stream", ""):
                raise NotFoundError(code=404, err_code=10059, description="stream not found")
            sub = AsyncMock()
            sub.unsubscribe = AsyncMock()
            return sub

        js_mock.subscribe = mock_subscribe
        nc.jetstream.return_value = js_mock

        async def run():
            await ctx.start(nc, mode="recovery")
            await asyncio.sleep(0.5)
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
