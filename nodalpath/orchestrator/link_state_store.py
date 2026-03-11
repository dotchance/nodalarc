"""Link state store — per-transition snapshot of full link visibility state.

Stored separately from AlmanacStore to keep AlmanacEntry focused on
forwarding tables. Keyed by topology_state_id, same key used in AlmanacStore.

Persists to JSONL on the same write path as AlmanacStore. Loaded on restart
from the same output file pattern. Future entries (from LookaheadWorker) are
also stored here — they are tagged is_future=True and not written to disk.
"""

from __future__ import annotations

import bisect
import json
import logging
from pathlib import Path

log = logging.getLogger(__name__)


class LinkRecord:
    """A single link's state at a topology snapshot moment."""

    __slots__ = ("node_a", "node_b", "visible", "scheduled", "range_km", "link_type", "is_future")

    def __init__(
        self,
        node_a: str,
        node_b: str,
        visible: bool,
        scheduled: bool,
        range_km: float,
        link_type: str,
        is_future: bool = False,
    ) -> None:
        self.node_a = node_a
        self.node_b = node_b
        self.visible = visible
        self.scheduled = scheduled
        self.range_km = range_km
        self.link_type = link_type
        self.is_future = is_future

    def to_dict(self) -> dict:
        return {
            "node_a": self.node_a,
            "node_b": self.node_b,
            "visible": self.visible,
            "scheduled": self.scheduled,
            "range_km": self.range_km,
            "link_type": self.link_type,
        }

    @classmethod
    def from_dict(cls, d: dict) -> LinkRecord:
        return cls(
            node_a=d["node_a"],
            node_b=d["node_b"],
            visible=d["visible"],
            scheduled=d["scheduled"],
            range_km=d["range_km"],
            link_type=d.get("link_type", "isl"),
            is_future=False,
        )


class LinkStateStore:
    """Stores full link state per topology_state_id.

    API:
        store(topology_state_id, full_link_state, sim_time, is_future)
        get(topology_state_id) -> list[LinkRecord] | None
        get_by_sim_time(sim_time) -> list[LinkRecord] | None
        load_from_jsonl(path) -> int
    """

    def __init__(self, output_path: Path | None = None) -> None:
        self._by_state_id: dict[str, list[LinkRecord]] = {}
        self._by_sim_time: dict[str, str] = {}
        self._times: list[str] = []
        self._state_ids: list[str] = []
        self._output_path = output_path
        if output_path is not None:
            output_path.parent.mkdir(parents=True, exist_ok=True)

    def store(
        self,
        topology_state_id: str,
        full_link_state: dict[tuple[str, str], tuple[bool, bool, float]],
        sim_time: str,
        is_future: bool = False,
    ) -> None:
        """Store the full link state for a topology transition."""
        records: list[LinkRecord] = []
        for (node_a, node_b), (visible, scheduled, range_km) in full_link_state.items():
            link_type = "ground" if node_a.startswith("gs-") or node_b.startswith("gs-") else "isl"
            records.append(LinkRecord(
                node_a=node_a,
                node_b=node_b,
                visible=visible,
                scheduled=scheduled,
                range_km=range_km,
                link_type=link_type,
                is_future=is_future,
            ))

        self._by_state_id[topology_state_id] = records
        self._by_sim_time[sim_time] = topology_state_id

        if sim_time not in set(self._times):
            self._times.append(sim_time)
            self._state_ids.append(topology_state_id)
            if len(self._times) > 1 and self._times[-1] < self._times[-2]:
                pairs = sorted(zip(self._times, self._state_ids))
                self._times = [p[0] for p in pairs]
                self._state_ids = [p[1] for p in pairs]

        if self._output_path is not None and not is_future:
            with open(self._output_path, "a") as f:
                record = {
                    "topology_state_id": topology_state_id,
                    "sim_time": sim_time,
                    "links": [r.to_dict() for r in records],
                }
                f.write(json.dumps(record) + "\n")

    def get(self, topology_state_id: str) -> list[LinkRecord] | None:
        """Return link records for a given topology_state_id."""
        return self._by_state_id.get(topology_state_id)

    def get_by_sim_time(self, sim_time: str) -> list[LinkRecord] | None:
        """Return link records for the state at or before the given sim_time."""
        if not self._times:
            return None
        idx = bisect.bisect_right(self._times, sim_time)
        if idx == 0:
            return None
        state_id = self._state_ids[idx - 1]
        return self._by_state_id.get(state_id)

    def load_from_jsonl(self, path: Path) -> int:
        """Load previously stored link states from a JSONL file."""
        if not path.exists():
            return 0

        loaded = 0
        existing_sim_times = set(self._times)

        with open(path) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                    sim_time = data["sim_time"]
                    if sim_time in existing_sim_times:
                        continue
                    state_id = data["topology_state_id"]
                    records = [LinkRecord.from_dict(r) for r in data.get("links", [])]
                    self._by_state_id[state_id] = records
                    self._by_sim_time[sim_time] = state_id
                    self._times.append(sim_time)
                    self._state_ids.append(state_id)
                    existing_sim_times.add(sim_time)
                    loaded += 1
                except Exception as exc:
                    log.warning("Skipping malformed link state entry: %s", exc)

        if loaded > 0:
            pairs = sorted(zip(self._times, self._state_ids))
            self._times = [p[0] for p in pairs]
            self._state_ids = [p[1] for p in pairs]

        return loaded

    @property
    def entry_count(self) -> int:
        return len(self._by_state_id)
