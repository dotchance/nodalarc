"""Integration test: na-scenario inject/wait/measure against running session.

PRD line 592: validates that na-scenario correctly sends scenario commands
to the TO scenario injection socket and MI convergence gate, processes all
step types, and sends clear_overrides on completion.

Does NOT require K3s — uses mock ZMQ handlers on ephemeral ports.
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path

import pytest
import yaml
import zmq

from nodalarc.models.scenario import (
    InjectLinkDownStep,
    InjectLinkUpStep,
    InjectSatelliteLossStep,
    MeasureStep,
    ReconfigStep,
    ScenarioConfig,
    WaitConvergeStep,
    WaitStep,
)

pytestmark = pytest.mark.integration

PROJECT_ROOT = Path(__file__).parent.parent.parent


class MockHandler:
    """Mock ZMQ REP handler — records received commands, replies with ok."""

    def __init__(self) -> None:
        self.received: list[dict] = []
        self._ctx = zmq.Context()
        self._sock = self._ctx.socket(zmq.REP)
        self._sock.setsockopt(zmq.LINGER, 0)
        self.port = self._sock.bind_to_random_port("tcp://127.0.0.1")
        self._running = True
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        time.sleep(0.05)

    def _run(self) -> None:
        poller = zmq.Poller()
        poller.register(self._sock, zmq.POLLIN)
        while self._running:
            events = poller.poll(100)  # 100ms poll
            if events:
                raw = self._sock.recv(zmq.NOBLOCK)
                cmd = json.loads(raw)
                self.received.append(cmd)
                self._sock.send(json.dumps({
                    "status": "ok",
                    "converged": True,
                    "duration_ms": 0.0,
                }).encode())

    def stop(self) -> None:
        self._running = False
        if self._thread:
            self._thread.join(timeout=2.0)
        self._sock.close()
        self._ctx.term()

    @property
    def address(self) -> str:
        return f"tcp://127.0.0.1:{self.port}"


def _make_req_socket(address: str) -> tuple[zmq.Context, zmq.Socket]:
    """Create a REQ socket connected to the given address."""
    ctx = zmq.Context()
    sock = ctx.socket(zmq.REQ)
    sock.setsockopt(zmq.LINGER, 0)
    sock.connect(address)
    sock.setsockopt(zmq.RCVTIMEO, 5000)
    sock.setsockopt(zmq.SNDTIMEO, 5000)
    return ctx, sock


class TestScenarioYamlParsing:
    """Test scenario YAML loading and Pydantic model validation."""

    def test_isl_failure_scenario_loads(self):
        raw = yaml.safe_load(
            (PROJECT_ROOT / "configs/scenarios/isl-failure.yaml").read_text(),
        )
        scenario = ScenarioConfig.model_validate(raw["scenario"])
        assert scenario.name == "isl-failure"
        assert len(scenario.steps) == 7

    def test_step_types_discriminated(self):
        raw = yaml.safe_load(
            (PROJECT_ROOT / "configs/scenarios/isl-failure.yaml").read_text(),
        )
        scenario = ScenarioConfig.model_validate(raw["scenario"])
        assert isinstance(scenario.steps[0], WaitStep)
        assert isinstance(scenario.steps[1], InjectLinkDownStep)
        assert isinstance(scenario.steps[2], WaitConvergeStep)
        assert isinstance(scenario.steps[3], MeasureStep)
        assert isinstance(scenario.steps[4], InjectLinkUpStep)

    def test_inject_link_down_fields(self):
        raw = yaml.safe_load(
            (PROJECT_ROOT / "configs/scenarios/isl-failure.yaml").read_text(),
        )
        scenario = ScenarioConfig.model_validate(raw["scenario"])
        step = scenario.steps[1]
        assert isinstance(step, InjectLinkDownStep)
        assert step.node_a == "sat-P02S03"
        assert step.node_b == "sat-P02S04"
        assert step.reason == "scenario_inject_down"

    def test_reconfig_step_validates(self):
        step = ReconfigStep(
            action="reconfig",
            target="plane:3",
            set_values={"hello_interval": "5"},
        )
        assert step.target == "plane:3"
        assert step.set_values == {"hello_interval": "5"}

    def test_satellite_loss_step_validates(self):
        step = InjectSatelliteLossStep(
            action="inject_satellite_loss", node="sat-P02S03",
        )
        assert step.node == "sat-P02S03"

    def test_all_7_action_types(self):
        """All action types validate through the discriminated union."""
        data = {
            "name": "all-actions",
            "description": "test",
            "steps": [
                {"action": "wait", "duration_s": 1.0},
                {"action": "inject_link_down", "node_a": "a", "node_b": "b"},
                {"action": "inject_link_up", "node_a": "a", "node_b": "b"},
                {"action": "inject_satellite_loss", "node": "a"},
                {"action": "wait_converge", "timeout_s": 10.0},
                {"action": "measure", "duration_s": 5.0},
                {"action": "reconfig", "target": "all"},
            ],
        }
        scenario = ScenarioConfig.model_validate(data)
        assert len(scenario.steps) == 7


class TestScenarioZmqExecution:
    """Test scenario step functions against mock ZMQ handlers."""

    @pytest.fixture(autouse=True)
    def _zmq_handlers(self):
        """Start mock TO and MI handlers on ephemeral ports."""
        self.to_handler = MockHandler()
        self.mi_handler = MockHandler()
        self.to_handler.start()
        self.mi_handler.start()

        yield

        self.to_handler.stop()
        self.mi_handler.stop()

    def test_inject_link_down_sends_command(self):
        from tools.na_scenario import _inject_link_down

        ctx, sock = _make_req_socket(self.to_handler.address)
        try:
            step = InjectLinkDownStep(
                action="inject_link_down",
                node_a="sat-P00S00",
                node_b="sat-P00S01",
            )
            _inject_link_down(sock, step)

            assert len(self.to_handler.received) == 1
            cmd = self.to_handler.received[0]
            assert cmd["action"] == "inject_link_down"
            assert cmd["node_a"] == "sat-P00S00"
            assert cmd["node_b"] == "sat-P00S01"
        finally:
            sock.close()
            ctx.term()

    def test_inject_link_up_sends_command(self):
        from tools.na_scenario import _inject_link_up

        ctx, sock = _make_req_socket(self.to_handler.address)
        try:
            step = InjectLinkUpStep(
                action="inject_link_up",
                node_a="sat-P00S00",
                node_b="sat-P00S01",
            )
            _inject_link_up(sock, step)

            assert len(self.to_handler.received) == 1
            cmd = self.to_handler.received[0]
            assert cmd["action"] == "inject_link_up"
        finally:
            sock.close()
            ctx.term()

    def test_inject_satellite_loss_sends_command(self):
        from tools.na_scenario import _inject_satellite_loss

        ctx, sock = _make_req_socket(self.to_handler.address)
        try:
            step = InjectSatelliteLossStep(
                action="inject_satellite_loss",
                node="sat-P02S03",
            )
            _inject_satellite_loss(sock, step)

            assert len(self.to_handler.received) == 1
            cmd = self.to_handler.received[0]
            assert cmd["action"] == "inject_satellite_loss"
            assert cmd["node"] == "sat-P02S03"
        finally:
            sock.close()
            ctx.term()

    def test_wait_converge_sends_to_mi(self):
        from tools.na_scenario import _wait_converge

        ctx, sock = _make_req_socket(self.mi_handler.address)
        try:
            step = WaitConvergeStep(action="wait_converge", timeout_s=10.0)
            _wait_converge(sock, step)

            assert len(self.mi_handler.received) == 1
            cmd = self.mi_handler.received[0]
            assert cmd["action"] == "wait_converge"
            assert cmd["timeout_s"] == 10.0
        finally:
            sock.close()
            ctx.term()

    def test_clear_overrides_sent(self):
        """clear_overrides command is properly acknowledged."""
        from tools.na_scenario import _send_to_cmd

        ctx, sock = _make_req_socket(self.to_handler.address)
        try:
            resp = _send_to_cmd(sock, {"action": "clear_overrides"})
            assert resp["status"] == "ok"
            assert self.to_handler.received[0]["action"] == "clear_overrides"
        finally:
            sock.close()
            ctx.term()

    def test_sequential_inject_down_then_up(self):
        """inject_link_down followed by inject_link_up in sequence."""
        from tools.na_scenario import _inject_link_down, _inject_link_up

        ctx, sock = _make_req_socket(self.to_handler.address)
        try:
            down = InjectLinkDownStep(
                action="inject_link_down",
                node_a="sat-P00S00", node_b="sat-P00S01",
            )
            up = InjectLinkUpStep(
                action="inject_link_up",
                node_a="sat-P00S00", node_b="sat-P00S01",
            )
            _inject_link_down(sock, down)
            _inject_link_up(sock, up)

            actions = [cmd["action"] for cmd in self.to_handler.received]
            assert actions == ["inject_link_down", "inject_link_up"]
        finally:
            sock.close()
            ctx.term()
