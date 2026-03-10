"""Timeline reader — parse OME JSONL timeline files.

Each line: {"timestamp_s": float, "event_type": str, "data": {...}}
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path

from nodalarc.models.events import (
    ClockTick,
    TimelinePositionSnapshot,
    VisibilityEvent,
)

log = logging.getLogger(__name__)

_EVENT_MAP: dict[str, type] = {
    "ClockTick": ClockTick,
    "Snapshot": TimelinePositionSnapshot,
    "VisibilityEvent": VisibilityEvent,
}


def read_timeline(
    path: Path,
    start_time: str | None = None,
    end_time: str | None = None,
) -> Iterator[ClockTick | TimelinePositionSnapshot | VisibilityEvent]:
    """Yield parsed event records from a timeline JSONL file.

    Filters by sim_time if start_time/end_time are provided (ISO 8601 strings).
    Malformed lines are logged and skipped.
    """
    start_dt = datetime.fromisoformat(start_time) if start_time else None
    end_dt = datetime.fromisoformat(end_time) if end_time else None

    with open(path) as f:
        for line_num, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue

            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                log.warning("Malformed JSON at line %d, skipping", line_num)
                continue

            event_type = record.get("event_type")
            data = record.get("data", {})

            model_cls = _EVENT_MAP.get(event_type)
            if model_cls is None:
                log.warning(
                    "Unknown event_type %r at line %d, skipping",
                    event_type, line_num,
                )
                continue

            try:
                obj = model_cls.model_validate(data)
            except Exception:
                log.warning(
                    "Failed to parse %s at line %d, skipping",
                    event_type, line_num,
                )
                continue

            # Time filtering on parsed datetime
            if start_dt is not None and obj.sim_time < start_dt:
                continue
            if end_dt is not None and obj.sim_time > end_dt:
                continue

            yield obj
