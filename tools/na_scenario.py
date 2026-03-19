"""na-scenario — execute scenario YAML against a running session.

PRD 13.22: reads a scenario YAML, validates against Pydantic model,
executes steps sequentially. Communicates with TO via ZMQ REQ/REP
on port 5564 and with MI convergence gate on port 5563.

Usage:
  python -m tools.na_scenario --scenario configs/scenarios/isl-failure.yaml
  python -m tools.na_scenario --scenario configs/scenarios/isl-failure.yaml --session configs/sessions/sample.yaml
"""

from __future__ import annotations

import argparse
import contextlib
import json
import logging
import subprocess
import sys
import time
from pathlib import Path

import yaml
import zmq
from nodalarc.constants import LOG_FORMAT
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
from nodalarc.zmq_channels import (
    mi_convergence_gate_connect,
    to_scenario_inject_connect,
)

log = logging.getLogger(__name__)


def _send_to_cmd(sock: zmq.Socket, cmd: dict) -> dict:
    """Send a command to the TO and return the response."""
    sock.send(json.dumps(cmd).encode())
    raw = sock.recv()
    resp = json.loads(raw)
    if resp.get("status") != "ok":
        log.error(f"TO returned error: {resp}")
    return resp


def _inject_link_down(sock: zmq.Socket, step: InjectLinkDownStep) -> None:
    log.info(f"inject_link_down: {step.node_a} <-> {step.node_b}")
    _send_to_cmd(
        sock,
        {
            "action": "inject_link_down",
            "node_a": step.node_a,
            "node_b": step.node_b,
        },
    )


def _inject_link_up(sock: zmq.Socket, step: InjectLinkUpStep) -> None:
    log.info(f"inject_link_up: {step.node_a} <-> {step.node_b}")
    _send_to_cmd(
        sock,
        {
            "action": "inject_link_up",
            "node_a": step.node_a,
            "node_b": step.node_b,
        },
    )


def _inject_satellite_loss(sock: zmq.Socket, step: InjectSatelliteLossStep) -> None:
    log.info(f"inject_satellite_loss: {step.node}")
    _send_to_cmd(
        sock,
        {
            "action": "inject_satellite_loss",
            "node": step.node,
        },
    )


def _wait(step: WaitStep) -> None:
    log.info(f"wait: {step.duration_s}s")
    time.sleep(step.duration_s)


def _wait_converge(mi_sock: zmq.Socket, step: WaitConvergeStep) -> None:
    log.info(f"wait_converge: timeout={step.timeout_s}s")
    mi_sock.send(
        json.dumps(
            {
                "action": "wait_converge",
                "timeout_s": step.timeout_s,
            }
        ).encode()
    )
    raw = mi_sock.recv()
    resp = json.loads(raw)
    log.info(f"Convergence result: {resp}")


def _measure(mi_sock: zmq.Socket, step: MeasureStep) -> None:
    log.info(f"measure: {step.duration_s}s")
    mi_sock.send(
        json.dumps(
            {
                "action": "measure_start",
            }
        ).encode()
    )
    mi_sock.recv()
    time.sleep(step.duration_s)
    mi_sock.send(
        json.dumps(
            {
                "action": "measure_stop",
            }
        ).encode()
    )
    mi_sock.recv()
    log.info("Measurement window complete")


def _reconfig(step: ReconfigStep, session_path: str | None) -> None:
    log.info(f"reconfig: target={step.target}")
    if not session_path:
        log.error("reconfig requires --session argument")
        return
    cmd = [
        sys.executable,
        "-m",
        "tools.na_reconfig",
        "--session",
        session_path,
        "--target",
        step.target,
    ]
    for k, v in step.set_values.items():
        cmd.extend(["--set", f"{k}={v}"])
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        log.error(f"na-reconfig failed: {result.stderr}")
    else:
        log.info("reconfig complete")


def run_scenario(scenario_path: str, session_path: str | None = None) -> None:
    """Load and execute a scenario YAML file."""
    raw = yaml.safe_load(Path(scenario_path).read_text())
    scenario = ScenarioConfig.model_validate(raw["scenario"])
    log.info(f"Scenario: {scenario.name} — {scenario.description}")
    log.info(f"Steps: {len(scenario.steps)}")

    ctx = zmq.Context()

    # REQ socket to TO scenario injection (port 5564)
    to_sock = ctx.socket(zmq.REQ)
    to_sock.connect(to_scenario_inject_connect())
    to_sock.setsockopt(zmq.RCVTIMEO, 10_000)
    to_sock.setsockopt(zmq.SNDTIMEO, 5_000)

    # REQ socket to MI convergence gate (port 5563) — only if MI is configured
    mi_sock = None
    session_data = yaml.safe_load(Path(session_path).read_text())
    mi_block = session_data.get("mi", {})
    if mi_block.get("enabled", False):
        mi_sock = ctx.socket(zmq.REQ)
        mi_sock.connect(mi_convergence_gate_connect())
        mi_sock.setsockopt(zmq.RCVTIMEO, 120_000)
        mi_sock.setsockopt(zmq.SNDTIMEO, 5_000)

    try:
        for i, step in enumerate(scenario.steps):
            log.info(f"--- Step {i + 1}/{len(scenario.steps)}: {step.action} ---")

            match step:
                case WaitStep():
                    _wait(step)
                case InjectLinkDownStep():
                    _inject_link_down(to_sock, step)
                case InjectLinkUpStep():
                    _inject_link_up(to_sock, step)
                case InjectSatelliteLossStep():
                    _inject_satellite_loss(to_sock, step)
                case WaitConvergeStep():
                    if mi_sock is None:
                        log.warning(
                            "wait_converge: MI not configured — skipping, treating as converged"
                        )
                    else:
                        _wait_converge(mi_sock, step)
                case MeasureStep():
                    if mi_sock is None:
                        log.warning("measure: MI not configured — skipping measurement step")
                    else:
                        _measure(mi_sock, step)
                case ReconfigStep():
                    _reconfig(step, session_path)

        log.info("Scenario complete — sending clear_overrides to TO")
        _send_to_cmd(to_sock, {"action": "clear_overrides"})

    except zmq.error.Again:
        log.error("ZMQ timeout — is the TO running?")
        sys.exit(1)
    except KeyboardInterrupt:
        log.info("Interrupted — sending clear_overrides to TO")
        with contextlib.suppress(zmq.error.Again):
            _send_to_cmd(to_sock, {"action": "clear_overrides"})
    finally:
        to_sock.close()
        mi_sock.close()
        ctx.term()


def main() -> None:
    logging.basicConfig(format=LOG_FORMAT, level=logging.INFO)
    parser = argparse.ArgumentParser(description="Nodal Arc Scenario Executor")
    parser.add_argument(
        "--scenario",
        required=True,
        help="Path to scenario YAML file",
    )
    parser.add_argument(
        "--session",
        help="Path to session YAML (required for reconfig steps)",
    )
    parser.add_argument(
        "--platform-config", default="configs/platform.yaml", help="Path to platform config YAML"
    )
    args = parser.parse_args()

    from nodalarc.platform import init_platform_config

    init_platform_config(Path(args.platform_config))

    run_scenario(args.scenario, args.session)


if __name__ == "__main__":
    main()
