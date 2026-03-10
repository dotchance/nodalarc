"""Almanac store — time-indexed storage for AlmanacEntry instances.

Append-only, chronological. Optional JSONL output file.
Uses bisect for efficient lookup by sim_time.
"""

from __future__ import annotations

import bisect
from pathlib import Path

from nodalpath.models.almanac import AlmanacEntry, ForwardingTable


class AlmanacStore:
    """Time-indexed store for computed almanac entries."""

    def __init__(self, output_path: Path | None = None) -> None:
        self._entries: list[AlmanacEntry] = []
        self._times: list[str] = []  # parallel list for bisect
        self._output_path = output_path
        if output_path is not None:
            output_path.write_text("")

    def store(self, entry: AlmanacEntry) -> None:
        """Append an entry (must be chronologically ordered)."""
        self._entries.append(entry)
        self._times.append(entry.sim_time)
        if self._output_path is not None:
            with open(self._output_path, "a") as f:
                f.write(entry.model_dump_json() + "\n")

    def get_entry_at(self, sim_time: str) -> AlmanacEntry | None:
        """Return the most recent entry with sim_time <= the query time.

        Uses bisect_right for O(log n) lookup.
        """
        if not self._times:
            return None
        idx = bisect.bisect_right(self._times, sim_time)
        if idx == 0:
            return None
        return self._entries[idx - 1]

    def get_forwarding_table(
        self, sim_time: str, node_id: str,
    ) -> ForwardingTable | None:
        """Return forwarding table for a specific node at a given time."""
        entry = self.get_entry_at(sim_time)
        if entry is None:
            return None
        for ft in entry.forwarding_tables:
            if ft.node_id == node_id:
                return ft
        return None

    @property
    def entry_count(self) -> int:
        return len(self._entries)

    @property
    def entries(self) -> list[AlmanacEntry]:
        return list(self._entries)

    @property
    def transition_times(self) -> list[str]:
        return list(self._times)
