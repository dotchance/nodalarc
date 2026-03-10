"""Lookahead worker — reads ahead in the OME JSONL timeline and pre-computes
future almanac entries up to the configured horizon.

Runs as an asyncio task alongside LiveOrchestrator. Does not interfere with
the live transition path. Stores future AlmanacEntry objects in the shared
AlmanacStore tagged is_future=True.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from nodalpath.console.state import ConsoleState

from nodalarc.models.events import TimelinePositionSnapshot, VisibilityEvent
from nodalpath.engine.almanac_builder import compute_almanac_entry
from nodalpath.orchestrator.almanac_store import AlmanacStore
from nodalpath.orchestrator.snapshot_builder import SnapshotBuilder
from nodalpath.orchestrator.transition_detector import has_transition

log = logging.getLogger(__name__)

LOOKAHEAD_DEFAULT_S = 5700
POLL_INTERVAL_S = 5.0


class LookaheadWorker:
    """Pre-computes future almanac entries by reading ahead in the OME JSONL."""

    def __init__(
        self,
        timeline_path: Path,
        node_registry: dict,
        interface_map: dict,
        prefix_map: dict[str, str],
        bandwidth_map: dict | None,
        almanac_store: AlmanacStore,
        lookahead_horizon_s: int = LOOKAHEAD_DEFAULT_S,
        console_state: ConsoleState | None = None,
    ) -> None:
        self._timeline_path = timeline_path
        self._builder = SnapshotBuilder(node_registry, interface_map, bandwidth_map)
        self._prefix_map = prefix_map
        self._store = almanac_store
        self._horizon_s = lookahead_horizon_s
        self._console_state = console_state
        self._running = False

    def stop(self) -> None:
        self._running = False

    async def run(self) -> None:
        """Main loop. Reads ahead in JSONL until stopped or horizon reached."""
        self._running = True
        self._set_status("starting")
        log.info(
            "LookaheadWorker started (horizon=%ds, file=%s)",
            self._horizon_s, self._timeline_path,
        )

        # Wait for the live orchestrator to process at least one transition
        while self._running:
            ticks = self._store.get_timeline_ticks()
            past_ticks = [t for t in ticks if not t["is_future"]]
            if past_ticks:
                break
            await asyncio.sleep(1.0)

        if not self._running:
            return

        baseline_sim_time = past_ticks[0]["sim_time"]
        horizon_end = _add_seconds_to_iso(baseline_sim_time, self._horizon_s)

        log.info(
            "LookaheadWorker baseline=%s horizon_end=%s",
            baseline_sim_time, horizon_end,
        )

        prev_link_set: frozenset = frozenset()
        current_sim_time: str | None = None
        file_position = 0

        try:
            while self._running:
                new_events, file_position = _read_new_events(
                    self._timeline_path, file_position
                )

                if not new_events:
                    self._set_status("waiting")
                    await asyncio.sleep(POLL_INTERVAL_S)
                    continue

                self._set_status("computing")

                for event_type, data in new_events:
                    if not self._running:
                        break

                    event_sim_time = data.get("sim_time", "")
                    if event_sim_time <= baseline_sim_time:
                        continue
                    if event_sim_time > horizon_end:
                        log.info("LookaheadWorker reached horizon at %s", horizon_end)
                        self._set_status("complete")
                        self._running = False
                        break

                    if event_type == "VisibilityEvent":
                        try:
                            event = VisibilityEvent.model_validate(data)
                        except Exception as exc:
                            log.warning("LookaheadWorker bad VisibilityEvent: %s", exc)
                            continue

                        if current_sim_time is not None and event.sim_time.isoformat() != current_sim_time:
                            prev_link_set = await self._check_transition(
                                current_sim_time, prev_link_set
                            )
                        current_sim_time = event.sim_time.isoformat()
                        self._builder.apply_link_event(event)

                    elif event_type == "Snapshot":
                        try:
                            snapshot = TimelinePositionSnapshot.model_validate(data)
                        except Exception as exc:
                            log.warning("LookaheadWorker bad Snapshot: %s", exc)
                            continue
                        self._builder.apply_position_record(snapshot)

        except asyncio.CancelledError:
            log.info("LookaheadWorker cancelled")
        finally:
            log.info("LookaheadWorker stopped")

    async def _check_transition(
        self, sim_time_iso: str, prev_link_set: frozenset
    ) -> frozenset:
        """Check for topology transition and store future entry if changed."""
        curr = self._builder.active_link_set
        if not has_transition(prev_link_set, curr):
            return curr

        snapshot = self._builder.build_snapshot(sim_time_iso)
        entry = compute_almanac_entry(snapshot, self._prefix_map)

        entry = entry.model_copy(update={"is_future": True})
        self._store.store(entry)

        log.debug(
            "LookaheadWorker: future transition at %s, %d tables",
            sim_time_iso, len(entry.forwarding_tables),
        )

        return curr

    def _set_status(self, status: str) -> None:
        if self._console_state is not None:
            self._console_state.record_lookahead_status(status)


def _read_new_events(path: Path, from_position: int) -> tuple[list[tuple[str, dict]], int]:
    """Read new lines from JSONL file starting at byte offset from_position.

    Returns (events, new_position). Events are (event_type, data) tuples.
    Only VisibilityEvent and Snapshot records are returned — ClockTick is skipped.
    """
    if not path.exists():
        return [], from_position

    events = []
    new_position = from_position

    try:
        with open(path, "rb") as f:
            f.seek(from_position)
            while True:
                line = f.readline()
                if not line:
                    break
                new_position = f.tell()
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                    event_type = record.get("event_type", "")
                    if event_type in ("VisibilityEvent", "Snapshot"):
                        events.append((event_type, record.get("data", {})))
                except json.JSONDecodeError:
                    pass
    except OSError as exc:
        log.warning("LookaheadWorker read error: %s", exc)

    return events, new_position


def _add_seconds_to_iso(iso_time: str, seconds: int) -> str:
    """Add seconds to an ISO 8601 timestamp string."""
    dt = datetime.fromisoformat(iso_time.replace("Z", "+00:00"))
    return (dt + timedelta(seconds=seconds)).isoformat()
