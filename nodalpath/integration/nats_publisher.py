"""Publishes AlmanacEvent records on NATS JetStream.

Convenience methods: publish_table_pushed, publish_path_computed,
publish_deviation. Transport is NATS core publish (fire-and-forget,
non-blocking).
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

import nats
from nodalarc.nats_channels import NATS_CONNECT_OPTIONS, SUBJECT_ALMANAC_EVENT, nats_url

from nodalpath.models.almanac_event import AlmanacEvent

log = logging.getLogger(__name__)


class AlmanacPublisher:
    """NATS publisher for AlmanacEvent records.

    Call connect() before publish(). Call close() on shutdown.
    Publishes to SUBJECT_ALMANAC_EVENT (nodalarc.nodalpath.almanac).
    """

    def __init__(self) -> None:
        self._nc: nats.NATS | None = None

    async def connect(self) -> None:
        """Connect to NATS. Must be called before publish()."""
        self._nc = await nats.connect(nats_url(), **NATS_CONNECT_OPTIONS)
        log.info("AlmanacPublisher connected to %s", nats_url())

    async def publish(self, event: AlmanacEvent) -> None:
        """Publish an AlmanacEvent. Never raises."""
        if self._nc is None or self._nc.is_closed:
            return
        try:
            payload = event.model_dump_json().encode()
            await self._nc.publish(SUBJECT_ALMANAC_EVENT, payload)
        except Exception as exc:
            log.warning("Failed to publish AlmanacEvent: %s", exc)

    async def publish_table_pushed(
        self,
        sim_time: datetime,
        topology_state_id: str,
        nodes_attempted: int,
        nodes_succeeded: int,
        nodes_failed: int,
        push_duration_ms: float,
    ) -> None:
        """Publish a table_pushed event."""
        await self.publish(
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

    async def publish_path_computed(
        self,
        sim_time: datetime,
        topology_state_id: str,
    ) -> None:
        """Publish a path_computed event."""
        await self.publish(
            AlmanacEvent(
                event_type="path_computed",
                sim_time=sim_time,
                wall_time=datetime.now(UTC),
                topology_state_id=topology_state_id,
            )
        )

    async def publish_deviation(
        self,
        sim_time: datetime,
        topology_state_id: str,
        node_a: str,
        node_b: str,
        reason: str,
    ) -> None:
        """Publish a deviation_detected event."""
        await self.publish(
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

    async def close(self) -> None:
        if self._nc is not None and not self._nc.is_closed:
            await self._nc.drain()
            await self._nc.close()
