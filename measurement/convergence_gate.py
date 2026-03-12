"""Convergence gate — real ZMQ REP replacing Phase 1B stub.

Receives ConvergenceRequest from TO, delegates to convergence_detector,
returns ConvergenceResult.

Run: python -m measurement.convergence_gate --session <path>
"""

from __future__ import annotations

import argparse
import logging
from datetime import datetime, timezone
from typing import Any

import zmq

from nodalarc.constants import LOG_FORMAT
from nodalarc.models.metrics import ConvergenceRequest, ConvergenceResult
from nodalarc.models.session import SessionConfig
from nodalarc.zmq_channels import mi_convergence_gate_bind
from measurement.convergence_detector import measure_convergence

log = logging.getLogger(__name__)


class ConvergenceGate:
    """ZMQ REP server for convergence measurement requests."""

    def __init__(
        self,
        convergence_config,
        active_flows_fn=None,
        adapter=None,
    ) -> None:
        """
        Args:
            convergence_config: ConvergenceConfig from session
            active_flows_fn: callable returning dict of active flows
            adapter: protocol adapter for trace_path
        """
        self._config = convergence_config
        self._active_flows_fn = active_flows_fn or (lambda: {})
        self._adapter = adapter

    def handle_request(self, raw: bytes) -> bytes:
        """Process a single convergence request and return response bytes."""
        req = ConvergenceRequest.model_validate_json(raw)
        log.info(f"Convergence request: event_id={req.event_id}")

        active_flows = self._active_flows_fn()
        result = measure_convergence(
            event_id=req.event_id,
            link_event=req.link_event,
            convergence_config=self._config,
            active_flows=active_flows,
            adapter=self._adapter,
        )

        log.info(
            f"Convergence result: event_id={req.event_id} "
            f"converged={result.converged} duration={result.duration_ms}ms"
        )
        return result.model_dump_json().encode()

    def run(self, bind_addr: str | None = None) -> None:
        """Run the convergence gate — blocks forever."""
        if bind_addr is None:
            bind_addr = mi_convergence_gate_bind()
        ctx = zmq.Context()
        sock = ctx.socket(zmq.REP)
        sock.bind(bind_addr)
        log.info(f"Convergence gate bound on {bind_addr}")

        try:
            while True:
                raw = sock.recv()
                response = self.handle_request(raw)
                sock.send(response)
        except KeyboardInterrupt:
            log.info("Convergence gate shutting down")
        finally:
            sock.close()
            ctx.term()
