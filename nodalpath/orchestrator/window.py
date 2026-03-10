"""Sliding window orchestrator — batch-mode path computation from timeline.

Reads a timeline.jsonl file, detects topology transitions (link up/down),
and produces time-indexed AlmanacEntry instances with forwarding tables.
"""

from __future__ import annotations

import logging
from pathlib import Path

from nodalarc.models.events import TimelinePositionSnapshot, VisibilityEvent
from nodalpath.engine.almanac_builder import compute_almanac_entry
from nodalpath.models.topology import TopologyNode
from nodalpath.orchestrator.almanac_store import AlmanacStore
from nodalpath.orchestrator.snapshot_builder import SnapshotBuilder
from nodalpath.orchestrator.timeline_reader import read_timeline
from nodalpath.orchestrator.transition_detector import has_transition

log = logging.getLogger(__name__)


class SlidingWindow:
    """Processes a timeline file and computes almanac entries at topology transitions.

    Events are batched by sim_time. After processing all events at a given
    timestamp, the active link set is compared to the previous state. If it
    differs, a TopologySnapshot is built and an AlmanacEntry is computed.
    """

    def __init__(
        self,
        timeline_path: Path,
        node_registry: dict[str, TopologyNode],
        interface_map: dict[tuple[str, str], tuple[str, str]],
        prefix_map: dict[str, str],
        bandwidth_map: dict[tuple[str, str], float] | None = None,
        output_path: Path | None = None,
    ) -> None:
        self.timeline_path = timeline_path
        self.builder = SnapshotBuilder(node_registry, interface_map, bandwidth_map)
        self.store = AlmanacStore(output_path)
        self.prefix_map = prefix_map

    def _check_transition(
        self,
        prev_link_set: frozenset[tuple[str, str]],
        sim_time_iso: str,
    ) -> tuple[frozenset[tuple[str, str]], bool]:
        """Check for transition and compute almanac entry if needed.

        Returns (new_prev_link_set, did_transition).
        """
        curr = self.builder.active_link_set
        if not has_transition(prev_link_set, curr):
            return prev_link_set, False

        snapshot = self.builder.build_snapshot(sim_time_iso)
        entry = compute_almanac_entry(snapshot, self.prefix_map)
        self.store.store(entry)
        log.info(
            "Transition at %s: %d active links, %d forwarding tables",
            sim_time_iso, len(curr), len(entry.forwarding_tables),
        )
        return curr, True

    def process(
        self,
        start_time: str | None = None,
        end_time: str | None = None,
    ) -> int:
        """Process timeline and return the number of topology transitions detected."""
        prev_link_set: frozenset[tuple[str, str]] = frozenset()
        transition_count = 0
        current_time_iso: str | None = None
        current_time = None  # datetime for boundary detection

        for record in read_timeline(self.timeline_path, start_time, end_time):
            record_time = record.sim_time

            # Timestamp boundary — check for transition on the previous batch
            if current_time is not None and record_time != current_time:
                prev_link_set, did = self._check_transition(
                    prev_link_set, current_time_iso,
                )
                if did:
                    transition_count += 1

            current_time = record_time
            current_time_iso = record_time.isoformat()

            if isinstance(record, TimelinePositionSnapshot):
                self.builder.apply_position_record(record)
            elif isinstance(record, VisibilityEvent):
                self.builder.apply_link_event(record)
            # ClockTick: skip

        # Final batch
        if current_time is not None:
            prev_link_set, did = self._check_transition(
                prev_link_set, current_time_iso,
            )
            if did:
                transition_count += 1

        return transition_count
