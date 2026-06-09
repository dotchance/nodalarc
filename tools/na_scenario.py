"""na-scenario — execute scenario YAML against a running session.

PRD 13.22: reads a scenario YAML, validates against Pydantic model,
executes steps sequentially. Communicates with Scheduler via NATS
request/reply and with MI convergence gate via NATS request/reply.

Usage:
  python -m tools.na_scenario --scenario scenario.yaml --session catalog/nodalarc/sessions/earth-leo-simple.yaml
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
    RestoreSatelliteStep,
    ScenarioConfig,
    WaitConvergeStep,
    WaitStep,
)
from nodalarc.nats_channels import (
    NATS_CONNECT_OPTIONS,
    SUBJECT_MI_CONVERGENCE_GATE,
    nats_url,
    sanitize_session_id,
    scenario_inject_subject,
)

log = logging.getLogger(__name__)


def _resolve_session_id(session_path: str) -> str:
    """Read session YAML and return the sanitized session ID."""
    data = yaml.safe_load(Path(session_path).read_text())
    session_block = data.get("session", {})
    name = session_block.get("name", "")
    if not name:
        log.error("Session YAML has no session.name field")
        sys.exit(1)
    return sanitize_session_id(name)


async def _send_scheduler_cmd(nc: nats.NATS, subject: str, cmd: dict) -> dict:
    """Send a command to the Scheduler and return the response.

    Raises SystemExit on rejection — a failed scenario command means
    the rest of the scenario cannot be trusted.
    """
    payload = json.dumps(cmd).encode()
    msg = await nc.request(subject, payload, timeout=10)
    resp = json.loads(msg.data)
    status = resp.get("status", "")
    if status not in ("accepted", "ok"):
        log.error("Scheduler rejected command %s: %s", cmd.get("action"), resp)
        sys.exit(1)
    return resp


async def _inject_link_down(nc: nats.NATS, subject: str, step: InjectLinkDownStep) -> None:
    log.info("inject_link_down: %s <-> %s (reason=%s)", step.node_a, step.node_b, step.reason)
    await _send_scheduler_cmd(
        nc,
        subject,
        {
            "action": "inject_link_down",
            "node_a": step.node_a,
            "node_b": step.node_b,
            "reason": step.reason,
        },
    )


async def _inject_link_up(nc: nats.NATS, subject: str, step: InjectLinkUpStep) -> None:
    log.info("inject_link_up: %s <-> %s", step.node_a, step.node_b)
    await _send_scheduler_cmd(
        nc,
        subject,
        {
            "action": "inject_link_up",
            "node_a": step.node_a,
            "node_b": step.node_b,
        },
    )


async def _inject_satellite_loss(
    nc: nats.NATS, subject: str, step: InjectSatelliteLossStep
) -> None:
    log.info("inject_satellite_loss: %s", step.node)
    await _send_scheduler_cmd(
        nc,
        subject,
        {
            "action": "inject_satellite_loss",
            "node": step.node,
        },
    )


async def _restore_satellite(nc: nats.NATS, subject: str, step: RestoreSatelliteStep) -> None:
    log.info("restore_satellite: %s", step.node)
    await _send_scheduler_cmd(
        nc,
        subject,
        {
            "action": "restore_satellite",
            "node": step.node,
        },
    )


def _wait(step: WaitStep) -> None:
    log.info("wait: %ss", step.duration_s)
    time.sleep(step.duration_s)


async def _wait_converge(nc: nats.NATS, step: WaitConvergeStep) -> None:
    log.info("wait_converge: timeout=%ss", step.timeout_s)
    payload = json.dumps({"action": "wait_converge", "timeout_s": step.timeout_s}).encode()
    msg = await nc.request(SUBJECT_MI_CONVERGENCE_GATE, payload, timeout=step.timeout_s + 30)
    resp = json.loads(msg.data)
    log.info("Convergence result: %s", resp)


async def _measure(nc: nats.NATS, step: MeasureStep) -> None:
    log.info("measure: %ss", step.duration_s)
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


def _reconfig(step: ReconfigStep, session_path: str) -> None:
    log.info("reconfig: target=%s", step.target)
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
        log.error("na-reconfig failed: %s", result.stderr)
    else:
        log.info("reconfig complete")


async def run_scenario_async(scenario_path: str, session_path: str) -> None:
    """Load and execute a scenario YAML file."""
    session_id = _resolve_session_id(session_path)
    subject = scenario_inject_subject(session_id)

    raw = yaml.safe_load(Path(scenario_path).read_text())
    scenario = ScenarioConfig.model_validate(raw["scenario"])
    log.info("Scenario: %s — %s", scenario.name, scenario.description)
    log.info("Steps: %d, session_id: %s", len(scenario.steps), session_id)

    nc = await nats.connect(nats_url(), **NATS_CONNECT_OPTIONS)
    log.info("Connected to NATS at %s", nats_url())

    # Check if MI is configured
    session_data = yaml.safe_load(Path(session_path).read_text())
    mi_block = session_data.get("mi", {})
    mi_enabled = mi_block.get("enabled", False)

    try:
        for i, step in enumerate(scenario.steps):
            log.info("--- Step %d/%d: %s ---", i + 1, len(scenario.steps), step.action)

            match step:
                case WaitStep():
                    _wait(step)
                case InjectLinkDownStep():
                    await _inject_link_down(nc, subject, step)
                case InjectLinkUpStep():
                    await _inject_link_up(nc, subject, step)
                case InjectSatelliteLossStep():
                    await _inject_satellite_loss(nc, subject, step)
                case RestoreSatelliteStep():
                    await _restore_satellite(nc, subject, step)
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
        await _send_scheduler_cmd(nc, subject, {"action": "clear_overrides"})

    except nats.errors.TimeoutError:
        log.error("NATS timeout — is the Scheduler running?")
        sys.exit(1)
    except KeyboardInterrupt:
        log.info("Interrupted — sending clear_overrides")
        with contextlib.suppress(Exception):
            await _send_scheduler_cmd(nc, subject, {"action": "clear_overrides"})
    finally:
        await nc.close()


def run_scenario(scenario_path: str, session_path: str) -> None:
    asyncio.run(run_scenario_async(scenario_path, session_path))


def main() -> None:
    logging.basicConfig(format=LOG_FORMAT, level=logging.INFO)
    parser = argparse.ArgumentParser(description="Nodal Arc Scenario Executor")
    parser.add_argument("--scenario", required=True, help="Path to scenario YAML file")
    parser.add_argument("--session", required=True, help="Path to session YAML (required)")
    parser.add_argument(
        "--platform-config", default="configs/platform.yaml", help="Path to platform config YAML"
    )
    args = parser.parse_args()

    from nodalarc.platform_config import init_platform_config

    init_platform_config(Path(args.platform_config))

    run_scenario(args.scenario, args.session)


if __name__ == "__main__":
    main()
