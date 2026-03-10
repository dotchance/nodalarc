"""Deviation detector — compares TO link events against almanac expectations."""

from __future__ import annotations

import logging

from nodalarc.models.link_events import LinkDown, LinkUp
from nodalpath.orchestrator.almanac_store import AlmanacStore

log = logging.getLogger(__name__)


DEVIATION_REASONS = frozenset({
    "scenario_inject_down",
    "satellite_loss",
    "scenario_reconciliation",
})


class DeviationDetector:
    """Detects when the TO's reported link state diverges from the almanac."""

    def __init__(self, almanac_store: AlmanacStore) -> None:
        self._store = almanac_store
        self._deviation_count = 0

    @property
    def deviation_count(self) -> int:
        return self._deviation_count

    def check_link_down(self, event: LinkDown) -> bool:
        """Return True if this LinkDown is a deviation from the almanac.

        A deviation occurs when:
        1. The reason is an intentional override (scenario_inject_down, satellite_loss,
           scenario_reconciliation) — not a normal vis_lost or tracking_exceeded event
        2. The almanac at event.sim_time expected this link to be active

        Normal topology events (vis_lost, gs_below_horizon, etc.) are not deviations —
        the almanac already accounts for those via the OME timeline.
        """
        if event.reason not in DEVIATION_REASONS:
            return False

        sim_time_iso = event.sim_time.isoformat()
        entry = self._store.get_entry_at(sim_time_iso)
        if entry is None:
            return False

        node_ids_with_tables = {ft.node_id for ft in entry.forwarding_tables}
        pair_a = event.node_a in node_ids_with_tables
        pair_b = event.node_b in node_ids_with_tables

        if pair_a and pair_b:
            self._deviation_count += 1
            log.warning(
                "Deviation detected: LinkDown %s <-> %s reason=%s at %s "
                "(almanac state=%s)",
                event.node_a, event.node_b, event.reason,
                sim_time_iso, entry.topology_state_id,
            )
            return True

        return False

    def check_link_up(self, event: LinkUp) -> bool:
        """Return True if this LinkUp resolves a previously injected deviation.

        Reason scenario_inject_up restores a link that was overridden down.
        """
        return event.reason == "scenario_inject_up"
