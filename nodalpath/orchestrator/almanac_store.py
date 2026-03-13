"""Almanac store — time-indexed storage for AlmanacEntry instances.

Append-only, chronological. Optional JSONL output file.
Uses bisect for efficient lookup by sim_time.
"""

from __future__ import annotations

import bisect
import json
import logging
import re
from pathlib import Path

from nodalpath.models.almanac import AlmanacEntry, ForwardingTable

log = logging.getLogger(__name__)


def _plane_from_node_id(node_id: str) -> int | None:
    """Extract plane number from sat node ID like 'sat-P00S03'."""
    m = re.match(r"sat-P(\d+)S\d+", node_id)
    return int(m.group(1)) if m else None


def _slot_from_node_id(node_id: str) -> int | None:
    """Extract slot number from sat node ID like 'sat-P00S03'."""
    m = re.match(r"sat-P\d+S(\d+)", node_id)
    return int(m.group(1)) if m else None


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
        if self._output_path is not None and not entry.is_future:
            with open(self._output_path, "a") as f:
                f.write(entry.model_dump_json(exclude={"is_future"}) + "\n")

    def load_from_jsonl(self, path: Path) -> int:
        """Load previously computed entries from a JSONL file.

        Called on startup to restore history across restarts. Entries are
        appended in chronological order. If the store already has entries,
        only entries with sim_time > the last stored entry are appended
        (handles partial overlap at restart boundary).

        Returns the number of entries loaded.
        """
        if not path.exists():
            return 0

        loaded = 0
        last_time = self._times[-1] if self._times else ""

        with open(path) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                    entry = AlmanacEntry.model_validate(data)
                    if entry.sim_time > last_time:
                        self._entries.append(entry)
                        self._times.append(entry.sim_time)
                        last_time = entry.sim_time
                        loaded += 1
                except Exception as exc:
                    log.warning("Skipping malformed almanac entry: %s", exc)

        return loaded

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

    def get_forwarding_entries_for_node(
        self, node_id: str, topology_state_id: str,
    ) -> ForwardingTable | None:
        """Return the forwarding table for a node at a given topology state."""
        for entry in reversed(self._entries):
            if entry.topology_state_id == topology_state_id:
                for ft in entry.forwarding_tables:
                    if ft.node_id == node_id:
                        return ft
                return None
        return None

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

    def get_timeline_ticks(self) -> list[dict]:
        """Return a lightweight summary of all stored entries for the timeline.

        Each tick contains:
            sim_time: str (ISO 8601)
            topology_state_id: str
            node_count: int — total node count in forwarding tables
            is_future: bool
        """
        ticks = []
        for entry in self._entries:
            ticks.append({
                "sim_time": entry.sim_time,
                "topology_state_id": entry.topology_state_id,
                "node_count": len(entry.forwarding_tables),
                "is_future": entry.is_future,
            })
        return ticks

    def get_topology_at(self, sim_time: str, prefix_map: dict[str, list[str]]) -> dict | None:
        """Return a console-format topology dict for the state at sim_time.

        Uses get_entry_at() for O(log n) lookup. Returns None if no entry
        exists at or before the requested sim_time.

        The returned dict matches the format produced by _check_transition()
        in LiveOrchestrator — the frontend consumes both identically.
        """
        entry = self.get_entry_at(sim_time)
        if entry is None:
            return None

        nodes = []
        for ft in entry.forwarding_tables:
            neighbor_count = len(ft.lsr_bindings) + len(ft.ler_ingress_rules)
            nodes.append({
                "node_id": ft.node_id,
                "node_type": "ground_station" if ft.node_id.startswith("gs-") else "satellite",
                "plane": _plane_from_node_id(ft.node_id),
                "slot": _slot_from_node_id(ft.node_id),
                "routing_area": None,
                "neighbor_count": neighbor_count,
                "isl_count": 0,
                "gnd_count": 0,
                "prefix": ", ".join(prefix_map.get(ft.node_id, [])),
            })

        return {
            "topology_state_id": entry.topology_state_id,
            "sim_time": entry.sim_time,
            "is_future": entry.is_future,
            "nodes": nodes,
            "links": [],
        }

    @property
    def entry_count(self) -> int:
        return len(self._entries)

    @property
    def entries(self) -> list[AlmanacEntry]:
        return list(self._entries)

    @property
    def transition_times(self) -> list[str]:
        return list(self._times)
