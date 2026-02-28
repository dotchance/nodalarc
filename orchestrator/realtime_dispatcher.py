"""Real-Time dispatcher — subscribe to OME ZMQ, pace at wall-clock.

Subscribes to OME ZMQ PUB (port 5560). Processes events as they arrive
(pre-paced by OME publisher). Calls link_manager for state changes and
latency_model for position updates.

Does NOT run probes, evaluate convergence, or manage priority queue.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Any

import zmq

from nodalarc.models.events import (
    ClockTick,
    TimelinePositionSnapshot,
    VisibilityEvent,
)
from nodalarc.models.link_events import LatencyUpdate, LinkDown, LinkUp
from nodalarc.zmq_channels import (
    OME_EVENTS_CONNECT,
    TO_EVENTS_BIND,
    TOPIC_CLOCK_TICK,
    TOPIC_LATENCY_UPDATE,
    TOPIC_LINK_DOWN,
    TOPIC_LINK_UP,
    TOPIC_VISIBILITY_EVENT,
    decode_message,
    encode_message,
)
from orchestrator.discrete_event_dispatcher import ActiveLinkInfo
from orchestrator.latency_model import PositionTable

log = logging.getLogger(__name__)


class RealtimeDispatcher:
    """Process OME events in real-time from ZeroMQ subscription."""

    def __init__(
        self,
        interface_map: dict[tuple[str, str], tuple[str, str]],
        bandwidth_map: dict[tuple[str, str], float],
        override_set: set[tuple[str, str]],
        override_lock: Any,
        pid_map: dict[str, int] | None = None,
        latency_update_interval_s: int = 10,
    ) -> None:
        self._interface_map = interface_map
        self._bandwidth_map = bandwidth_map
        self._override_set = override_set
        self._override_lock = override_lock
        self._pid_map = pid_map or {}
        self._latency_update_interval_s = latency_update_interval_s

        self._position_table = PositionTable()
        self._active_links: dict[tuple[str, str], ActiveLinkInfo] = {}
        self._last_latencies: dict[tuple[str, str], float] = {}
        self._last_latency_update_time: float = 0.0

    def run(self) -> None:
        """Subscribe to OME ZMQ and process events as they arrive."""
        ctx = zmq.Context()

        # Subscribe to OME events
        sub_sock = ctx.socket(zmq.SUB)
        sub_sock.connect(OME_EVENTS_CONNECT)
        sub_sock.setsockopt(zmq.SUBSCRIBE, b"")  # All topics

        # Publish TO events
        pub_sock = ctx.socket(zmq.PUB)
        pub_sock.bind(TO_EVENTS_BIND)

        log.info(f"RT dispatcher subscribed to {OME_EVENTS_CONNECT}")

        try:
            while True:
                raw = sub_sock.recv()
                topic, payload = decode_message(raw)

                if topic == TOPIC_CLOCK_TICK:
                    pass  # Informational

                elif topic == b"Snapshot":
                    snap = TimelinePositionSnapshot.model_validate_json(payload)
                    self._position_table.update_from_snapshot(snap)
                    self._maybe_update_latencies(pub_sock)

                elif topic == TOPIC_VISIBILITY_EVENT:
                    vis = VisibilityEvent.model_validate_json(payload)
                    self._handle_visibility(vis, pub_sock)

        except KeyboardInterrupt:
            log.info("RT dispatcher shutting down")
        finally:
            sub_sock.close()
            pub_sock.close()
            ctx.term()

    def _handle_visibility(
        self,
        vis: VisibilityEvent,
        pub_sock: zmq.Socket,
    ) -> None:
        """Process a visibility event."""
        pair = (vis.node_a, vis.node_b)

        with self._override_lock:
            if pair in self._override_set:
                return

        if vis.visible and vis.scheduled:
            self._link_up(vis, pub_sock)
        elif not vis.visible:
            self._link_down(vis, pub_sock)

    def _link_up(self, vis: VisibilityEvent, pub_sock: zmq.Socket) -> None:
        pair = (vis.node_a, vis.node_b)
        if pair in self._active_links:
            return

        ifaces = self._interface_map.get(pair)
        if not ifaces:
            return

        bandwidth = self._bandwidth_map.get(pair, 1000.0)
        latency = self._position_table.compute_link_latency(vis.node_a, vis.node_b)
        if latency is None:
            latency = 3.0

        self._active_links[pair] = ActiveLinkInfo(
            interface_a=ifaces[0], interface_b=ifaces[1],
            latency_ms=latency, bandwidth_mbps=bandwidth,
            pid_a=self._pid_map.get(vis.node_a, 0),
            pid_b=self._pid_map.get(vis.node_b, 0),
        )
        self._last_latencies[pair] = latency

        info = self._active_links[pair]
        if info.pid_a and info.pid_b:
            from orchestrator import link_manager
            is_gs_link = vis.node_a.startswith("gs-") or vis.node_b.startswith("gs-")
            if is_gs_link:
                link_manager.create_veth_pair(
                    info.pid_a, info.pid_b, ifaces[0], ifaces[1],
                )
                link_manager.enable_mpls_input(info.pid_a, ifaces[0])
                link_manager.enable_mpls_input(info.pid_b, ifaces[1])
            link_manager.set_interface_up(info.pid_a, ifaces[0])
            link_manager.set_interface_up(info.pid_b, ifaces[1])
            link_manager.apply_link_shaping(info.pid_a, ifaces[0], latency, bandwidth)
            link_manager.apply_link_shaping(info.pid_b, ifaces[1], latency, bandwidth)

        now = datetime.now(timezone.utc)
        event = LinkUp(
            sim_time=vis.sim_time, wall_time=now,
            node_a=vis.node_a, node_b=vis.node_b,
            interface_a=ifaces[0], interface_b=ifaces[1],
            latency_ms=latency, bandwidth_mbps=bandwidth,
            reason="vis_gained",
        )
        pub_sock.send(encode_message(TOPIC_LINK_UP, event.model_dump_json().encode()))

    def _link_down(self, vis: VisibilityEvent, pub_sock: zmq.Socket) -> None:
        pair = (vis.node_a, vis.node_b)
        info = self._active_links.pop(pair, None)
        if info is None:
            return

        if info.pid_a and info.pid_b:
            from orchestrator import link_manager
            is_gs_link = vis.node_a.startswith("gs-") or vis.node_b.startswith("gs-")
            if is_gs_link:
                link_manager.destroy_veth_pair(info.pid_a, info.interface_a)
            else:
                link_manager.set_interface_down(info.pid_a, info.interface_a)
                link_manager.set_interface_down(info.pid_b, info.interface_b)

        self._last_latencies.pop(pair, None)
        now = datetime.now(timezone.utc)
        event = LinkDown(
            sim_time=vis.sim_time, wall_time=now,
            node_a=vis.node_a, node_b=vis.node_b,
            interface_a=info.interface_a, interface_b=info.interface_b,
            reason="vis_lost",
        )
        pub_sock.send(encode_message(TOPIC_LINK_DOWN, event.model_dump_json().encode()))

    def _maybe_update_latencies(self, pub_sock: zmq.Socket) -> None:
        """Check if it's time for a latency update pass."""
        now = time.monotonic()
        if now - self._last_latency_update_time < self._latency_update_interval_s:
            return
        self._last_latency_update_time = now

        active_set = set(self._active_links.keys())
        updates = self._position_table.get_links_needing_update(
            active_set, self._last_latencies,
        )
        now_dt = datetime.now(timezone.utc)
        for node_a, node_b, new_lat, range_km in updates:
            pair = (node_a, node_b)
            info = self._active_links.get(pair)
            if not info:
                continue

            if info.pid_a and info.pid_b:
                from orchestrator import link_manager
                link_manager.update_delay(info.pid_a, info.interface_a, new_lat)
                link_manager.update_delay(info.pid_b, info.interface_b, new_lat)

            info.latency_ms = new_lat
            self._last_latencies[pair] = new_lat

            event = LatencyUpdate(
                sim_time=now_dt, wall_time=now_dt,
                node_a=node_a, node_b=node_b,
                latency_ms=new_lat, range_km=range_km,
            )
            pub_sock.send(encode_message(
                TOPIC_LATENCY_UPDATE, event.model_dump_json().encode(),
            ))
