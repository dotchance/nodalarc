# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""Unit tests for OME time control surface (R-OME-008B Tier 1).

Tests the protocol handler, state mutation, and boundary validation.
Does NOT test the full pacing thread (that requires a running OME);
tests the handler logic in isolation.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

from nodalarc.nats_channels import MAX_TIME_ACCEL, MIN_TIME_ACCEL

# ---------------------------------------------------------------------------
# resolve_session_epoch
# ---------------------------------------------------------------------------


class TestResolveSessionEpoch:
    """Tests for the default-now helper (R-OME-005)."""

    def test_returns_configured_time_when_set(self):
        from nodalarc.models.session import TimeConfig, resolve_session_epoch

        tc = TimeConfig(start_time="2020-01-01T00:00:00+00:00")
        result = resolve_session_epoch(tc)
        expected = datetime(2020, 1, 1, tzinfo=UTC).timestamp()
        assert abs(result - expected) < 1.0

    def test_returns_wall_clock_now_when_none(self):
        import time

        from nodalarc.models.session import TimeConfig, resolve_session_epoch

        tc = TimeConfig()  # start_time=None
        before = time.time()
        result = resolve_session_epoch(tc)
        after = time.time()
        assert before <= result <= after + 0.1

    def test_returns_wall_clock_now_when_empty_string(self):
        """Empty string is falsy — treated same as None."""
        import time

        from nodalarc.models.session import TimeConfig, resolve_session_epoch

        tc = TimeConfig(start_time="")
        before = time.time()
        result = resolve_session_epoch(tc)
        after = time.time()
        assert before <= result <= after + 0.1


# ---------------------------------------------------------------------------
# Playback control handler (extracted from OME for testability)
# ---------------------------------------------------------------------------


def _make_handler():
    """Create a testable version of the OME playback handler.

    Returns (handler_func, get_state_func) where handler_func accepts
    a mock NATS message and get_state_func returns current (paused, speed).
    """
    state = {"time_accel": 1.0, "paused": False, "seek_target": None}

    async def handle(msg):
        try:
            cmd = json.loads(msg.data)
            action = cmd.get("action", "")
            if action == "pause":
                state["paused"] = True
            elif action == "resume":
                state["paused"] = False
            elif action == "set_speed":
                factor = float(cmd.get("factor", 1.0))
                if factor < MIN_TIME_ACCEL or factor > MAX_TIME_ACCEL:
                    reply = {
                        "error": f"factor {factor} out of range [{MIN_TIME_ACCEL}, {MAX_TIME_ACCEL}]",
                        "paused": state["paused"],
                        "speed": state["time_accel"],
                    }
                    await msg.respond(json.dumps(reply).encode())
                    return
                state["time_accel"] = factor
            elif action == "seek":
                target_str = cmd.get("target_sim_time")
                if target_str:
                    state["seek_target"] = datetime.fromisoformat(target_str).timestamp()
                else:
                    state["seek_target"] = datetime.now(UTC).timestamp()
            elif action == "get_status":
                pass
            else:
                reply = {
                    "error": f"unknown action: {action}",
                    "paused": state["paused"],
                    "speed": state["time_accel"],
                }
                await msg.respond(json.dumps(reply).encode())
                return
            await msg.respond(
                json.dumps({"paused": state["paused"], "speed": state["time_accel"]}).encode()
            )
        except Exception as exc:
            await msg.respond(json.dumps({"error": str(exc)}).encode())

    def get_state():
        return state["paused"], state["time_accel"]

    def get_seek():
        return state["seek_target"]

    return handle, get_state, get_seek


def _make_msg(action_body: dict) -> MagicMock:
    """Create a mock NATS message with JSON data and async respond."""
    msg = MagicMock()
    msg.data = json.dumps(action_body).encode()
    msg.respond = AsyncMock()
    return msg


def _reply_data(msg: MagicMock) -> dict:
    """Extract the JSON reply sent via msg.respond."""
    assert msg.respond.called, "msg.respond was never called"
    raw = msg.respond.call_args[0][0]
    return json.loads(raw)


def _run(coro):
    """Run an async coroutine synchronously (no pytest-asyncio dependency)."""
    import asyncio

    return asyncio.run(coro)


class TestPlaybackHandler:
    """Protocol + state validation for the OME playback control handler."""

    def test_pause_sets_paused_true(self):
        handler, get_state, _ = _make_handler()
        msg = _make_msg({"action": "pause"})
        _run(handler(msg))
        paused, speed = get_state()
        assert paused is True
        reply = _reply_data(msg)
        assert reply["paused"] is True

    def test_resume_sets_paused_false(self):
        handler, get_state, _ = _make_handler()
        _run(handler(_make_msg({"action": "pause"})))
        msg = _make_msg({"action": "resume"})
        _run(handler(msg))
        paused, _ = get_state()
        assert paused is False
        assert _reply_data(msg)["paused"] is False

    def test_set_speed_valid_factor(self):
        handler, get_state, _ = _make_handler()
        msg = _make_msg({"action": "set_speed", "factor": 30.0})
        _run(handler(msg))
        _, speed = get_state()
        assert speed == 30.0
        assert _reply_data(msg)["speed"] == 30.0

    def test_set_speed_min_boundary(self):
        handler, get_state, _ = _make_handler()
        msg = _make_msg({"action": "set_speed", "factor": MIN_TIME_ACCEL})
        _run(handler(msg))
        _, speed = get_state()
        assert speed == MIN_TIME_ACCEL

    def test_set_speed_max_boundary(self):
        handler, get_state, _ = _make_handler()
        msg = _make_msg({"action": "set_speed", "factor": MAX_TIME_ACCEL})
        _run(handler(msg))
        _, speed = get_state()
        assert speed == MAX_TIME_ACCEL

    def test_set_speed_below_min_rejected(self):
        handler, get_state, _ = _make_handler()
        msg = _make_msg({"action": "set_speed", "factor": 0.05})
        _run(handler(msg))
        _, speed = get_state()
        assert speed == 1.0  # unchanged from default
        reply = _reply_data(msg)
        assert "error" in reply
        assert reply["speed"] == 1.0

    def test_set_speed_above_max_rejected(self):
        handler, get_state, _ = _make_handler()
        msg = _make_msg({"action": "set_speed", "factor": 2000.0})
        _run(handler(msg))
        _, speed = get_state()
        assert speed == 1.0
        assert "error" in _reply_data(msg)

    def test_get_status_returns_state_no_mutation(self):
        handler, get_state, _ = _make_handler()
        _run(handler(_make_msg({"action": "set_speed", "factor": 42.0})))
        _run(handler(_make_msg({"action": "pause"})))
        msg = _make_msg({"action": "get_status"})
        _run(handler(msg))
        paused, speed = get_state()
        assert paused is True
        assert speed == 42.0
        reply = _reply_data(msg)
        assert reply == {"paused": True, "speed": 42.0}

    def test_unknown_action_returns_error(self):
        handler, _, _ = _make_handler()
        msg = _make_msg({"action": "rewind"})
        _run(handler(msg))
        reply = _reply_data(msg)
        assert "error" in reply
        assert "unknown action" in reply["error"]

    def test_pause_preserves_speed(self):
        """Pausing should NOT reset speed — resume should return at previous rate."""
        handler, get_state, _ = _make_handler()
        _run(handler(_make_msg({"action": "set_speed", "factor": 60.0})))
        _run(handler(_make_msg({"action": "pause"})))
        paused, speed = get_state()
        assert paused is True
        assert speed == 60.0

    def test_resume_preserves_speed(self):
        handler, get_state, _ = _make_handler()
        _run(handler(_make_msg({"action": "set_speed", "factor": 60.0})))
        _run(handler(_make_msg({"action": "pause"})))
        _run(handler(_make_msg({"action": "resume"})))
        paused, speed = get_state()
        assert paused is False
        assert speed == 60.0

    def test_set_speed_while_paused(self):
        """Changing speed while paused should work — resume will use new speed."""
        handler, get_state, _ = _make_handler()
        _run(handler(_make_msg({"action": "pause"})))
        _run(handler(_make_msg({"action": "set_speed", "factor": 100.0})))
        paused, speed = get_state()
        assert paused is True
        assert speed == 100.0

    def test_negative_factor_rejected(self):
        handler, get_state, _ = _make_handler()
        msg = _make_msg({"action": "set_speed", "factor": -5.0})
        _run(handler(msg))
        _, speed = get_state()
        assert speed == 1.0
        assert "error" in _reply_data(msg)

    def test_zero_factor_rejected(self):
        """Factor=0 would divide-by-zero in pacing; users should pause() instead."""
        handler, get_state, _ = _make_handler()
        msg = _make_msg({"action": "set_speed", "factor": 0.0})
        _run(handler(msg))
        _, speed = get_state()
        assert speed == 1.0
        assert "error" in _reply_data(msg)


class TestSeekHandler:
    """Tests for the Tier 2 seek action (R-OME-008B Part 5)."""

    def test_seek_with_explicit_target(self):
        handler, _, get_seek = _make_handler()
        msg = _make_msg({"action": "seek", "target_sim_time": "2025-06-15T12:00:00+00:00"})
        _run(handler(msg))
        target = get_seek()
        expected = datetime(2025, 6, 15, 12, 0, 0, tzinfo=UTC).timestamp()
        assert target is not None
        assert abs(target - expected) < 1.0
        reply = _reply_data(msg)
        assert "error" not in reply

    def test_seek_without_target_uses_now(self):
        """Seek with no target_sim_time = 'reset to now' (R-OME-005)."""
        import time as _time

        handler, _, get_seek = _make_handler()
        before = _time.time()
        msg = _make_msg({"action": "seek"})
        _run(handler(msg))
        after = _time.time()
        target = get_seek()
        assert target is not None
        assert before <= target <= after + 0.1

    def test_seek_preserves_speed(self):
        """Seek should NOT reset speed — the rate is independent of epoch."""
        handler, get_state, get_seek = _make_handler()
        _run(handler(_make_msg({"action": "set_speed", "factor": 60.0})))
        _run(handler(_make_msg({"action": "seek"})))
        _, speed = get_state()
        assert speed == 60.0  # speed preserved through seek
        assert get_seek() is not None

    def test_seek_preserves_pause_state(self):
        """Seek while paused should stay paused at new epoch."""
        handler, get_state, get_seek = _make_handler()
        _run(handler(_make_msg({"action": "pause"})))
        _run(handler(_make_msg({"action": "seek", "target_sim_time": "2020-01-01T00:00:00+00:00"})))
        paused, _ = get_state()
        assert paused is True
        assert get_seek() is not None

    def test_seek_replies_with_current_state(self):
        handler, _, _ = _make_handler()
        _run(handler(_make_msg({"action": "set_speed", "factor": 30.0})))
        msg = _make_msg({"action": "seek", "target_sim_time": "2026-01-01T00:00:00+00:00"})
        _run(handler(msg))
        reply = _reply_data(msg)
        assert reply["speed"] == 30.0
        assert reply["paused"] is False
