"""na-scenario — execute scenario YAML against a running session.

PRD 13.22: reads a scenario YAML, validates against Pydantic model,
executes steps sequentially. Communicates with Scheduler via NATS
request/reply and with MI convergence gate via NATS request/reply.

Usage:
  python -m tools.na_scenario --scenario configs/scenarios/isl-failure.yaml
  python -m tools.na_scenario --scenario configs/scenarios/isl-failure.yaml --session configs/sessions/sample.yaml
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import logging
import subprocess
import sys
import time
from pathlib import Path

import nats
import yaml
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
from nodalarc.nats_channels import (
    NATS_CONNECT_OPTIONS,
    SUBJECT_MI_CONVERGENCE_GATE,
    SUBJECT_SCENARIO_INJECT,
    nats_url,
)

log = logging.getLogger(__name__)


async def _send_scheduler_cmd(nc: nats.NATS, cmd: dict) -> dict:
    """Send a command to the Scheduler and return the response."""
    payload = json.dumps(cmd).encode()
    msg = await nc.request(SUBJECT_SCENARIO_INJECT, payload, timeout=10)
    resp = json.loads(msg.data)
    if resp.get("status") != "ok":
        log.error(f"Scheduler returned error: {resp}")
    return resp


async def _inject_link_down(nc: nats.NATS, step: InjectLinkDownStep) -> None:
    log.info(f"inject_link_down: {step.node_a} <-> {step.node_b}")
    await _send_scheduler_cmd(
        nc,
        {
            "action": "inject_link_down",
            "node_a": step.node_a,
            "node_b": step.node_b,
        },
    )


async def _inject_link_up(nc: nats.NATS, step: InjectLinkUpStep) -> None:
    log.info(f"inject_link_up: {step.node_a} <-> {step.node_b}")
    await _send_scheduler_cmd(
        nc,
        {
            "action": "inject_link_up",
            "node_a": step.node_a,
            "node_b": step.node_b,
        },
    )


async def _inject_satellite_loss(nc: nats.NATS, step: InjectSatelliteLossStep) -> None:
    log.info(f"inject_satellite_loss: {step.node}")
    await _send_scheduler_cmd(
        nc,
        {
            "action": "inject_satellite_loss",
            "node": step.node,
        },
    )


def _wait(step: WaitStep) -> None:
    log.info(f"wait: {step.duration_s}s")
    time.sleep(step.duration_s)


async def _wait_converge(nc: nats.NATS, step: WaitConvergeStep) -> None:
    log.info(f"wait_converge: timeout={step.timeout_s}s")
    payload = json.dumps({"action": "wait_converge", "timeout_s": step.timeout_s}).encode()
    msg = await nc.request(SUBJECT_MI_CONVERGENCE_GATE, payload, timeout=step.timeout_s + 30)
    resp = json.loads(msg.data)
    log.info(f"Convergence result: {resp}")


async def _measure(nc: nats.NATS, step: MeasureStep) -> None:
    log.info(f"measure: {step.duration_s}s")
    await nc.request(
        SUBJECT_MI_CONVERGENCE_GATE,
        json.dumps({"action": "measure_start"}).encode(),
        timeout=10,
    )
    time.sleep(step.duration_s)
    await nc.request(
        SUBJECT_MI_CONVERGENCE_GATE,
        json.dumps({"action": "measure_stop"}).encode(),
        timeout=10,
    )
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


async def run_scenario_async(scenario_path: str, session_path: str | None = None) -> None:
    """Load and execute a scenario YAML file."""
    raw = yaml.safe_load(Path(scenario_path).read_text())
    scenario = ScenarioConfig.model_validate(raw["scenario"])
    log.info(f"Scenario: {scenario.name} — {scenario.description}")
    log.info(f"Steps: {len(scenario.steps)}")

    nc = await nats.connect(nats_url(), **NATS_CONNECT_OPTIONS)
    log.info("Connected to NATS at %s", nats_url())

    # Check if MI is configured
    mi_enabled = False
    if session_path:
        session_data = yaml.safe_load(Path(session_path).read_text())
        mi_block = session_data.get("mi", {})
        mi_enabled = mi_block.get("enabled", False)

    try:
        for i, step in enumerate(scenario.steps):
            log.info(f"--- Step {i + 1}/{len(scenario.steps)}: {step.action} ---")

            match step:
                case WaitStep():
                    _wait(step)
                case InjectLinkDownStep():
                    await _inject_link_down(nc, step)
                case InjectLinkUpStep():
                    await _inject_link_up(nc, step)
                case InjectSatelliteLossStep():
                    await _inject_satellite_loss(nc, step)
                case WaitConvergeStep():
                    if not mi_enabled:
                        log.warning("wait_converge: MI not configured — skipping")
                    else:
                        await _wait_converge(nc, step)
                case MeasureStep():
                    if not mi_enabled:
                        log.warning("measure: MI not configured — skipping")
                    else:
                        await _measure(nc, step)
                case ReconfigStep():
                    _reconfig(step, session_path)

        log.info("Scenario complete — sending clear_overrides to Scheduler")
        await _send_scheduler_cmd(nc, {"action": "clear_overrides"})

    except nats.errors.TimeoutError:
        log.error("NATS timeout — is the Scheduler running?")
        sys.exit(1)
    except KeyboardInterrupt:
        log.info("Interrupted — sending clear_overrides")
        with contextlib.suppress(Exception):
            await _send_scheduler_cmd(nc, {"action": "clear_overrides"})
    finally:
        await nc.close()


def run_scenario(scenario_path: str, session_path: str | None = None) -> None:
    asyncio.run(run_scenario_async(scenario_path, session_path))


def main() -> None:
    logging.basicConfig(format=LOG_FORMAT, level=logging.INFO)
    parser = argparse.ArgumentParser(description="Nodal Arc Scenario Executor")
    parser.add_argument("--scenario", required=True, help="Path to scenario YAML file")
    parser.add_argument("--session", help="Path to session YAML (required for reconfig steps)")
    parser.add_argument(
        "--platform-config", default="configs/platform.yaml", help="Path to platform config YAML"
    )
    args = parser.parse_args()

    from nodalarc.platform import init_platform_config

    init_platform_config(Path(args.platform_config))

    run_scenario(args.scenario, args.session)


if __name__ == "__main__":
    main()
