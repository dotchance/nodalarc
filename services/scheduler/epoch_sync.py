# Copyright 2024-2026 .chance (dotchance)
# Licensed under the NodalArc Source Available License 1.0. See LICENSE file.
"""Scheduler seek epoch synchronization state machine."""

from __future__ import annotations

from dataclasses import dataclass, field

from nodalarc.models.link_state import LinkStateSnapshot


def _empty_deps() -> dict[str, bool]:
    return {"ephemeris": False, "snapshot": False}


@dataclass
class EpochSyncState:
    """Mutable state for Tier 2 seek synchronization.

    Dispatcher owns I/O and dispatch queueing. This object owns the pure state
    transitions that determine whether a new epoch has all dependencies needed
    before link actuation can resume.
    """

    suspended: bool = False
    expected_epoch_id: int = 0
    playback_playing_received: bool = False
    deps_met: dict[str, bool] = field(default_factory=_empty_deps)
    buffered_snapshot: LinkStateSnapshot | None = None
    stale: bool = False

    def begin_seek(self, epoch_id: int) -> bool:
        """Enter suspended state for a newer epoch."""
        if epoch_id <= self.expected_epoch_id:
            return False
        self.suspended = True
        self.stale = False
        self.expected_epoch_id = epoch_id
        self.playback_playing_received = False
        self.deps_met = _empty_deps()
        self.buffered_snapshot = None
        return True

    def mark_ephemeris(self, epoch_id: int) -> bool:
        """Mark matching-epoch ephemeris as available."""
        if epoch_id != self.expected_epoch_id:
            return False
        self.deps_met["ephemeris"] = True
        return True

    def mark_playing(self, epoch_id: int) -> bool:
        """Mark matching-epoch PlaybackState(playing) as received."""
        if epoch_id != self.expected_epoch_id:
            return False
        self.playback_playing_received = True
        return True

    def buffer_snapshot(self, snapshot: LinkStateSnapshot) -> bool:
        """Buffer a matching-epoch LinkStateSnapshot while suspended."""
        if snapshot.epoch_id != self.expected_epoch_id:
            return False
        self.buffered_snapshot = snapshot
        self.deps_met["snapshot"] = True
        return True

    def missing_resume_dependencies(self) -> list[str]:
        """Return dependency names still blocking resume."""
        missing: list[str] = []
        if not self.playback_playing_received:
            missing.append("PlaybackState(playing)")
        if not self.deps_met["ephemeris"]:
            missing.append("SessionEphemeris")
        if not self.deps_met["snapshot"]:
            missing.append("LinkStateSnapshot")
        return missing

    def ready_for_clock_resume(self) -> bool:
        return not self.missing_resume_dependencies()

    def resume(self) -> LinkStateSnapshot | None:
        """Leave suspended state and return the buffered snapshot."""
        if not self.ready_for_clock_resume():
            raise RuntimeError(
                "Cannot resume epoch sync with missing dependencies: "
                f"{', '.join(self.missing_resume_dependencies())}"
            )
        snapshot = self.buffered_snapshot
        self.suspended = False
        self.stale = False
        self.buffered_snapshot = None
        return snapshot

    def mark_watchdog_timeout(self, epoch_id: int) -> bool:
        """Mark stale if the active suspended epoch timed out."""
        if self.suspended and self.expected_epoch_id == epoch_id:
            self.stale = True
            return True
        return False
