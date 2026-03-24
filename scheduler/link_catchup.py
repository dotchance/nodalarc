"""R-TO-009: Link state catch-up REP socket on port 5569.

Serves current _active_links on demand to late-connecting subscribers.
Runs in a daemon thread. Same pattern as scenario_handler on port 5564.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

import zmq
from nodalarc.zmq_channels import to_link_catchup_bind

log = logging.getLogger(__name__)


def run_link_catchup_handler(active_links: dict) -> None:
    """Serve current _active_links on demand. Runs in daemon thread."""
    ctx = zmq.Context()
    sock = ctx.socket(zmq.REP)
    sock.bind(to_link_catchup_bind())
    log.info("Link catchup REP bound on %s", to_link_catchup_bind())

    try:
        while True:
            sock.recv()  # Block until request
            # Snapshot _active_links at this moment
            links = []
            for (node_a, node_b), info in dict(active_links).items():
                links.append(
                    {
                        "node_a": node_a,
                        "node_b": node_b,
                        "interface_a": info.interface_a,
                        "interface_b": info.interface_b,
                        "latency_ms": info.latency_ms,
                        "bandwidth_mbps": info.bandwidth_mbps,
                    }
                )
            resp = {
                "sim_time": datetime.now(UTC).isoformat(),
                "active_links": links,
            }
            sock.send_json(resp)
    except Exception as exc:
        log.error("Link catchup handler crashed: %s", exc)
