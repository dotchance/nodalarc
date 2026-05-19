# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""Convergence gate — handles convergence measurement requests.

Receives ConvergenceRequest, delegates to convergence_detector,
returns ConvergenceResult. Transport-agnostic — called by MI main
via NATS request/reply.
"""

from __future__ import annotations

import logging

from nodalarc.models.metrics import ConvergenceRequest

from measurement.convergence_detector import measure_convergence

log = logging.getLogger(__name__)


class ConvergenceGate:
    """Convergence measurement request handler."""

    def __init__(
        self,
        convergence_config,
        active_flows_fn=None,
        adapter=None,
    ) -> None:
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
