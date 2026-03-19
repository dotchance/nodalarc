"""Publishes AlmanacEvent records on the nodalpath-events ZMQ channel."""

from __future__ import annotations

import logging
from datetime import UTC, datetime

import zmq
from nodalarc.zmq_channels import TOPIC_ALMANAC_EVENT, encode_message

from nodalpath.models.almanac_event import AlmanacEvent

log = logging.getLogger(__name__)


class AlmanacPublisher:
    """ZMQ PUB socket wrapper for AlmanacEvent publishing.

    Call bind() before publish(). Call close() on shutdown.
    """

    def __init__(self, bind_address: str) -> None:
        self._bind_address = bind_address
        self._ctx = zmq.Context()
        self._sock = self._ctx.socket(zmq.PUB)
        self._sock.bind(bind_address)
        log.info("AlmanacPublisher bound to %s", bind_address)

    def publish(self, event: AlmanacEvent) -> None:
        """Publish an AlmanacEvent. Never raises."""
        try:
            payload = event.model_dump_json().encode()
            self._sock.send(encode_message(TOPIC_ALMANAC_EVENT, payload), zmq.NOBLOCK)
        except Exception as exc:
            log.warning("Failed to publish AlmanacEvent: %s", exc)

    def publish_table_pushed(
        self,
        sim_time: datetime,
        topology_state_id: str,
        nodes_attempted: int,
        nodes_succeeded: int,
        nodes_failed: int,
        push_duration_ms: float,
    ) -> None:
        """Convenience method: publish a table_pushed event."""
        self.publish(
            AlmanacEvent(
                event_type="table_pushed",
                sim_time=sim_time,
                wall_time=datetime.now(UTC),
                topology_state_id=topology_state_id,
                nodes_attempted=nodes_attempted,
                nodes_succeeded=nodes_succeeded,
                nodes_failed=nodes_failed,
                push_duration_ms=push_duration_ms,
            )
        )

    def publish_path_computed(
        self,
        sim_time: datetime,
        topology_state_id: str,
    ) -> None:
        """Convenience method: publish a path_computed event."""
        self.publish(
            AlmanacEvent(
                event_type="path_computed",
                sim_time=sim_time,
                wall_time=datetime.now(UTC),
                topology_state_id=topology_state_id,
            )
        )

    def publish_deviation(
        self,
        sim_time: datetime,
        topology_state_id: str,
        node_a: str,
        node_b: str,
        reason: str,
    ) -> None:
        """Convenience method: publish a deviation_detected event."""
        self.publish(
            AlmanacEvent(
                event_type="deviation_detected",
                sim_time=sim_time,
                wall_time=datetime.now(UTC),
                topology_state_id=topology_state_id,
                deviation_node_a=node_a,
                deviation_node_b=node_b,
                deviation_reason=reason,
            )
        )

    def close(self) -> None:
        self._sock.close()
        self._ctx.term()
