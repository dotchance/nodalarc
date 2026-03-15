"""Scan OME timeline JSONL for next event affecting a set of nodes.

The timeline file is append-only and concurrent-read safe. The scanner
caches the file offset between calls to avoid re-reading from the start.
"""

from __future__ import annotations

import json
import logging

log = logging.getLogger(__name__)


class TimelineScanner:
    """Find the next VisibilityEvent affecting any node in a given set."""

    def __init__(self, timeline_path: str) -> None:
        self._path = timeline_path
        self._cached_offset: int = 0

    def scan_next_event(self, node_set: set[str], after_sim_time: str) -> str | None:
        """Find next VisibilityEvent affecting any node in node_set.

        Args:
            node_set: Set of node_ids to watch for.
            after_sim_time: Only consider events with sim_time > this value.

        Returns:
            ISO 8601 sim_time of the next event, or None if nothing found.
        """
        try:
            with open(self._path) as f:
                f.seek(self._cached_offset)
                best: str | None = None
                while True:
                    line = f.readline()
                    if not line:
                        break
                    try:
                        record = json.loads(line)
                    except (json.JSONDecodeError, ValueError):
                        continue
                    if record.get("event_type") != "VisibilityEvent":
                        continue
                    data = record.get("data", {})
                    sim_time = data.get("sim_time", "")
                    if not sim_time or sim_time <= after_sim_time:
                        continue
                    node_a = data.get("node_a", "")
                    node_b = data.get("node_b", "")
                    if node_a in node_set or node_b in node_set:
                        best = sim_time
                        break  # Events are chronological; first match is earliest
                self._cached_offset = f.tell()
                return best
        except FileNotFoundError:
            log.warning("Timeline file not found: %s", self._path)
            return None
        except Exception as exc:
            log.warning("Timeline scan error: %s", exc)
            return None

    def reset(self) -> None:
        """Reset cached offset (e.g., on session switch)."""
        self._cached_offset = 0
