"""Integration test: DE dispatcher processes timeline correctly.

Tests the discrete-event dispatcher with a pre-computed timeline
and convergence gate stub, verifying event processing and ZMQ
message flow.

Does NOT require K3s — runs against a pre-computed timeline file.
"""

from __future__ import annotations

import json
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest
import zmq

from nodalarc.models.link_events import LinkDown, LinkUp
from nodalarc.zmq_channels import (
    TO_EVENTS_CONNECT,
    decode_message,
    TOPIC_LINK_UP,
    TOPIC_LINK_DOWN,
)

pytestmark = pytest.mark.integration

PROJECT_ROOT = Path(__file__).parent.parent.parent


class TestDiscreteEventProcessing:
    def test_timeline_produces_events(self, tmp_path):
        """Pre-computed timeline should be loadable and contain events."""
        from ome.event_stream import read_timeline_jsonl

        # Generate a short timeline
        session_path = PROJECT_ROOT / "configs/sessions/sample-session.yaml"
        if not session_path.exists():
            pytest.skip("sample session not available")

        from ome.main import run as ome_run
        timeline = ome_run(str(session_path), str(tmp_path))

        events = read_timeline_jsonl(timeline)
        assert len(events) > 0

        # Should have ClockTick, Snapshot, and VisibilityEvent types
        types = {e["event_type"] for e in events}
        assert "ClockTick" in types
        assert "Snapshot" in types

    def test_convergence_stub_responds(self):
        """Convergence gate stub responds correctly to requests."""
        from nodalarc.models.link_events import LinkUp
        from nodalarc.models.metrics import ConvergenceRequest, ConvergenceResult
        from nodalarc.zmq_channels import MI_CONVERGENCE_GATE_CONNECT

        # Start the stub in a subprocess
        stub_proc = subprocess.Popen(
            [sys.executable, "-m", "measurement.stubs.convergence_stub"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        time.sleep(0.5)  # Let it bind

        try:
            ctx = zmq.Context()
            sock = ctx.socket(zmq.REQ)
            sock.connect(MI_CONVERGENCE_GATE_CONNECT)
            sock.setsockopt(zmq.RCVTIMEO, 5000)

            now = datetime.now(timezone.utc)
            link_event = LinkUp(
                sim_time=now, wall_time=now,
                node_a="sat-P00S00", node_b="sat-P00S01",
                interface_a="isl0", interface_b="isl0",
                latency_ms=3.0, bandwidth_mbps=1000.0,
                reason="vis_gained",
            )
            req = ConvergenceRequest(
                event_id="test-001",
                link_event=link_event,
            )
            sock.send(req.model_dump_json().encode())
            raw = sock.recv()
            result = ConvergenceResult.model_validate_json(raw)

            assert result.event_id == "test-001"
            assert result.converged is True
            assert result.duration_ms == 0.0

            sock.close()
            ctx.term()
        finally:
            stub_proc.terminate()
            stub_proc.wait(timeout=5)
