"""Convergence gate stub — auto-responds "converged" on ZMQ REP.

Phase 1B placeholder. Validates the ZMQ wire format between TO and MI
before Phase 1C delivers the real convergence gate.

Run: python -m measurement.stubs.convergence_stub
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

import zmq

from nodalarc.constants import LOG_FORMAT
from nodalarc.models.metrics import ConvergenceRequest, ConvergenceResult
from nodalarc.zmq_channels import mi_convergence_gate_bind

log = logging.getLogger(__name__)


def run_stub() -> None:
    """Run the convergence gate stub — blocks forever."""
    ctx = zmq.Context()
    sock = ctx.socket(zmq.REP)
    sock.bind(mi_convergence_gate_bind())
    log.info(f"Convergence gate stub bound on {mi_convergence_gate_bind()}")

    try:
        while True:
            raw = sock.recv()
            req = ConvergenceRequest.model_validate_json(raw)
            log.info(f"Received convergence request: event_id={req.event_id}")
            now = datetime.now(timezone.utc)
            result = ConvergenceResult(
                event_id=req.event_id,
                converged=True,
                duration_ms=0.0,
                packets_lost=0,
                packets_sent=0,
                sim_time_start=now,
                sim_time_end=now,
                wall_time_start=now,
                wall_time_end=now,
            )
            sock.send(result.model_dump_json().encode())
    except KeyboardInterrupt:
        log.info("Convergence gate stub shutting down")
    finally:
        sock.close()
        ctx.term()


if __name__ == "__main__":
    import argparse
    from pathlib import Path
    from nodalarc.platform import init_platform_config

    logging.basicConfig(format=LOG_FORMAT, level=logging.INFO)
    parser = argparse.ArgumentParser()
    parser.add_argument("--platform-config", default="configs/platform.yaml")
    args = parser.parse_args()
    init_platform_config(Path(args.platform_config))
    run_stub()
