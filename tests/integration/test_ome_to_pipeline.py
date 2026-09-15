"""Integration test: End-to-end OME pipeline verification.

PRD Appendix B: runs ome.main.run() with reference constellations,
verifies timeline contains ClockTick/Snapshot/VisibilityEvent events,
verifies terminal exhaustion events, verifies JSON Lines round-trip.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pytest
from nodalarc.configuration_yaml import load_configuration_yaml
from nodalarc.models.events import (
    ClockTick,
    VisibilityEvent,
)

from tests.seam_reference import DeclaredShell, encounter, expected_transitions

pytestmark = pytest.mark.integration

PROJECT_ROOT = Path(__file__).parent.parent.parent


def _temporary_session(source_name: str, *, step_seconds: int) -> str:
    import tempfile

    import yaml

    source = PROJECT_ROOT / "catalog" / "nodalarc" / "sessions" / source_name
    session = load_configuration_yaml(source.read_text(encoding="utf-8"))
    session["time"]["step_seconds"] = step_seconds
    with tempfile.NamedTemporaryFile(
        mode="w",
        suffix=".yaml",
        dir=str(PROJECT_ROOT),
        delete=False,
    ) as file_handle:
        yaml.safe_dump(session, file_handle, sort_keys=False)
        return file_handle.name


@pytest.fixture
def short_session_path():
    """Create a temporary canonical session for the short pipeline run."""
    return _temporary_session("earth-leo-simple.yaml", step_seconds=10)


@pytest.fixture
def sample_session_path():
    """Create a segment-session fixture for the sample timeline."""
    return _temporary_session("earth-leo-walker.yaml", step_seconds=10)


@pytest.fixture
def polar_seam_session_path():
    """The range experiment: the polar sky plus the six same-slot seam pairs."""
    return _temporary_session("earth-leo-polar-seam.yaml", step_seconds=10)


@pytest.fixture
def polar_seam_tracking_session_path():
    """The tracking experiment: the seam-crossing phasing, six two-slot-offset seam pairs."""
    return _temporary_session("earth-leo-polar-seam-tracking.yaml", step_seconds=10)


@pytest.fixture
def short_timeline(short_session_path, tmp_path):
    from ome.main import run as ome_run

    path = ome_run(short_session_path, str(tmp_path), run_id="test-short-pipeline")
    Path(short_session_path).unlink(missing_ok=True)
    return path


@pytest.fixture
def sample_timeline(sample_session_path, tmp_path):
    from ome.main import run as ome_run

    path = ome_run(sample_session_path, str(tmp_path), run_id="test-sample")
    Path(sample_session_path).unlink(missing_ok=True)
    return path


@pytest.fixture
def polar_seam_timeline(polar_seam_session_path, tmp_path):
    from ome.main import run as ome_run

    path = ome_run(polar_seam_session_path, str(tmp_path), run_id="test-polar-seam")
    Path(polar_seam_session_path).unlink(missing_ok=True)
    return path


@pytest.fixture
def polar_seam_tracking_timeline(polar_seam_tracking_session_path, tmp_path):
    from ome.main import run as ome_run

    path = ome_run(
        polar_seam_tracking_session_path, str(tmp_path), run_id="test-polar-seam-tracking"
    )
    Path(polar_seam_tracking_session_path).unlink(missing_ok=True)
    return path


def _load_events(path):
    events = []
    with open(path) as f:
        for line in f:
            if line.strip():
                events.append(json.loads(line))
    return events


class TestCanonicalPipeline:
    def test_timeline_contains_clock_ticks(self, short_timeline):
        events = _load_events(short_timeline)
        types = {e["event_type"] for e in events}
        assert "ClockTick" in types

    def test_timeline_event_types(self, short_timeline):
        events = _load_events(short_timeline)
        types = {e["event_type"] for e in events}
        assert types == {"ClockTick", "VisibilityEvent"}

    def test_timeline_contains_visibility_events(self, short_timeline):
        events = _load_events(short_timeline)
        types = {e["event_type"] for e in events}
        assert "VisibilityEvent" in types

    def test_all_events_deserialize(self, short_timeline):
        """All timeline event types must be recognized and schema-valid."""
        events = _load_events(short_timeline)
        decoded = []
        for e in events:
            if e["event_type"] == "ClockTick":
                decoded.append(ClockTick.model_validate(e["data"]))
            elif e["event_type"] == "VisibilityEvent":
                decoded.append(VisibilityEvent.model_validate(e["data"]))
            else:
                pytest.fail(f"unexpected event_type in OME output: {e['event_type']}")

        assert len(decoded) == len(events)
        assert any(isinstance(event, ClockTick) for event in decoded)
        assert any(isinstance(event, VisibilityEvent) for event in decoded)

    def test_isl_visibility_events_present(self, short_timeline):
        """The shipped LEO ring produces ISL visibility events."""
        events = _load_events(short_timeline)
        isl_vis = [
            e
            for e in events
            if e["event_type"] == "VisibilityEvent" and e["data"]["elevation_deg"] is None
        ]
        assert len(isl_vis) > 0

    def test_jsonl_write_read_round_trip(self, short_timeline, tmp_path):
        """Write → Read produces identical data."""
        from ome.event_stream import read_timeline_jsonl

        events = _load_events(short_timeline)
        round_tripped = read_timeline_jsonl(short_timeline)
        assert len(round_tripped) == len(events)
        for orig, rt in zip(events, round_tripped):
            assert orig["event_type"] == rt["event_type"]
            assert orig["timestamp_s"] == rt["timestamp_s"]


class TestStarlinkMiniTerminalExhaustion:
    def test_ground_terminal_exhaustion(self, sample_timeline):
        """Dense LEO access produces visible=True, scheduled=False ground events.

        The fixture uses a denser constellation and multi-station ground set so at
        least one station has more visible candidates than scheduled terminals.
        """
        events = _load_events(sample_timeline)
        gs_exhaustion = [
            e
            for e in events
            if e["event_type"] == "VisibilityEvent"
            and e["data"]["visible"]
            and not e["data"]["scheduled"]
            and e["data"]["elevation_deg"] is not None
        ]
        assert len(gs_exhaustion) > 0, (
            "Expected visible=True, scheduled=False ground events for terminal exhaustion"
        )


class TestPolarVisibilityTransitions:
    """The two polar-seam experiments behave as their declared data requires.

    Expectations come from tests.seam_reference, a declared-parameter model
    independent of OME's propagator and decision functions. Every ISL event
    OME emits is compared against it: simulation time anchored to the
    session's declared start (never to the output under test), visibility,
    the physical reason, the reported range, and the allocation state. The
    checkers are functions over the loaded events so the negative cases can
    prove what each assertion rejects.
    """

    SAMPLE_S = 10.0
    ORBIT_S = 6030.0
    # OME propagates J2 mean elements; the reference applies first-order
    # secular J2 rates to circular elements. Over an orbit the two differ by
    # up to 55 km in range (a 3.7 s timing offset at a 15 km/s closing speed
    # near the seam crossing); every event of the retained timelines sits
    # within 46 km. A reported range beyond this bound is a disagreement to
    # investigate, not a tolerance to widen.
    RANGE_TOLERANCE_KM = 100.0
    CLOSE_PASS_KM = 400.0

    @staticmethod
    def _shell(constellation_name: str) -> DeclaredShell:
        catalog = PROJECT_ROOT / "catalog" / "nodalarc"
        constellation = load_configuration_yaml(
            (catalog / "constellations" / "earth" / "leo" / constellation_name).read_text()
        )["constellation"]
        orbit_ref = constellation["orbit"].split(":", 1)[1]
        orbit = load_configuration_yaml((catalog / orbit_ref).read_text())["orbit"]
        return DeclaredShell(
            altitude_km=float(orbit["shape"]["altitude_km"]),
            inclination_deg=float(orbit["orientation"]["inclination_deg"]),
            raan_spacing_deg=float(constellation["planes"]["raan_spacing_deg"]),
            slots_per_plane=int(constellation["slots_per_plane"]),
            phase_offset_deg=float(constellation["phasing"]["phase_offset_deg"]),
        )

    @staticmethod
    def _terminal_limits() -> tuple[float, float]:
        terminal = load_configuration_yaml(
            (
                PROJECT_ROOT / "catalog/nodalarc/terminals/optical/optical-low-orbit-isl.yaml"
            ).read_text()
        )["terminal"]
        return float(terminal["max_range_km"]), float(terminal["limits"]["max_tracking_rate_deg_s"])

    @staticmethod
    def _declared_start(session_name: str) -> datetime:
        """The session's declared start, the only time anchor the checks accept."""
        document = load_configuration_yaml(
            (PROJECT_ROOT / "catalog" / "nodalarc" / "sessions" / session_name).read_text()
        )
        return datetime.fromisoformat(str(document["time"]["start_time"]).replace("Z", "+00:00"))

    @staticmethod
    def _isl_events_by_pair(events, start: datetime) -> dict[tuple[str, str], list[dict]]:
        """Every ISL VisibilityEvent with the fields the checks need, keyed by pair,
        its time measured from the declared session start."""
        by_pair: dict[tuple[str, str], list[dict]] = {}
        for event in events:
            if (
                event["event_type"] != "VisibilityEvent"
                or event["data"]["elevation_deg"] is not None
            ):
                continue
            data = event["data"]
            stamp = datetime.fromisoformat(data["sim_time"].replace("Z", "+00:00"))
            pair = (min(data["node_a"], data["node_b"]), max(data["node_a"], data["node_b"]))
            by_pair.setdefault(pair, []).append(
                {
                    "t": (stamp - start).total_seconds(),
                    "visible": data["visible"],
                    "reason": data["visibility_reject_reason"],
                    "range_km": float(data["range_km"]),
                    "scheduled": data["scheduled"],
                    "scheduling_state": data["scheduling_state"],
                    "unscheduled_reason": data.get("unscheduled_reason"),
                }
            )
        return by_pair

    @staticmethod
    def _plane_slot(node_id: str) -> tuple[int, int]:
        return int(node_id[-5:-3]), int(node_id[-2:])

    def _expected_events(self, shell, a, b, *, max_range_km, max_rate_deg_s):
        """Visible/invisible changes with their physical reason, as the timeline emits them:
        the verdict sequence collapsed to visibility changes, no event for an invisible start."""
        verdicts = expected_transitions(
            shell,
            a,
            b,
            duration_s=self.ORBIT_S,
            sample_s=self.SAMPLE_S,
            max_range_km=max_range_km,
            max_rate_deg_s=max_rate_deg_s,
        )
        expected = []
        visible = None
        for t_s, verdict in verdicts:
            now_visible = verdict == "ok"
            if visible is None:
                if now_visible:
                    expected.append((t_s, True, "ok"))
                visible = now_visible
                continue
            if now_visible != visible:
                expected.append((t_s, now_visible, verdict))
                visible = now_visible
        return expected

    def _assert_pair_follows_reference(self, shell, pair, observed, expected, *, max_range_km):
        """Time, visibility and reason against the reference sequence; the reported range
        against the reference geometry at the event's own time; the allocation state of an
        uncontended pair: feasible means scheduled and active, infeasible means unscheduled."""
        a, b = self._plane_slot(pair[0]), self._plane_slot(pair[1])
        assert len(observed) == len(expected), (pair, observed, expected)
        for event, (t_exp, vis_exp, reason_exp) in zip(observed, expected):
            label = (pair, event["t"], event["reason"], t_exp, reason_exp)
            assert event["visible"] == vis_exp, label
            assert event["reason"] == reason_exp, label
            assert abs(event["t"] - t_exp) <= self.SAMPLE_S, label
            reference_range, _, _ = encounter(shell, a, b, event["t"])
            assert abs(event["range_km"] - reference_range) <= self.RANGE_TOLERANCE_KM, (
                pair,
                event["t"],
                event["range_km"],
                reference_range,
            )
            if event["visible"]:
                assert event["range_km"] <= max_range_km, label
                assert event["scheduled"] is True, label
                assert event["scheduling_state"] == "active", label
                assert event["unscheduled_reason"] is None, label
            else:
                assert event["scheduled"] is False, label
                if event["reason"] == "range_exceeded":
                    assert event["range_km"] > max_range_km, label

    def check_range_experiment(self, events, start):
        max_range_km, max_rate_deg_s = self._terminal_limits()
        shell = self._shell("earth-leo-polar-36.yaml")
        by_pair = self._isl_events_by_pair(events, start)

        seam = {pair for pair in by_pair if {pair[0][-6:-3], pair[1][-6:-3]} == {"p00", "p05"}}
        corotating = set(by_pair) - seam
        assert len(seam) == 6 and len(corotating) == 30
        for pair in corotating:
            (only,) = by_pair[pair]
            assert (only["t"], only["visible"], only["reason"]) == (0.0, True, "ok"), pair
            assert only["scheduled"] is True and only["unscheduled_reason"] is None, pair
        for pair in sorted(seam):
            slot = self._plane_slot(pair[0])[1]
            expected = self._expected_events(
                shell,
                (5, slot),
                (0, slot),
                max_range_km=max_range_km,
                max_rate_deg_s=max_rate_deg_s,
            )
            self._assert_pair_follows_reference(
                shell, pair, by_pair[pair], expected, max_range_km=max_range_km
            )
        seam_events = [e for pair in seam for e in by_pair[pair]]
        assert sum(1 for e in seam_events if not e["visible"]) == 12
        assert sum(1 for e in seam_events if e["visible"]) == 14
        assert {e["reason"] for e in seam_events if not e["visible"]} == {"range_exceeded"}

    def check_tracking_experiment(self, events, start):
        max_range_km, max_rate_deg_s = self._terminal_limits()
        shell = self._shell("earth-leo-polar-36-seam-crossing.yaml")
        by_pair = self._isl_events_by_pair(events, start)

        assert len(by_pair) == 6
        for pair in sorted(by_pair):
            slot_of = {node[-6:-3]: self._plane_slot(node)[1] for node in pair}
            assert (slot_of["p05"] + 2) % 6 == slot_of["p00"], pair
            a, b = (5, slot_of["p05"]), (0, slot_of["p00"])
            expected = self._expected_events(
                shell, a, b, max_range_km=max_range_km, max_rate_deg_s=max_rate_deg_s
            )
            self._assert_pair_follows_reference(
                shell, pair, by_pair[pair], expected, max_range_km=max_range_km
            )
            for event in by_pair[pair]:
                if event["reason"] == "tracking_exceeded":
                    reference_range, rate, clear = encounter(shell, a, b, event["t"])
                    assert clear and rate > max_rate_deg_s, (pair, event, rate)
                    assert event["range_km"] < self.CLOSE_PASS_KM, (pair, event)
                    assert reference_range < self.CLOSE_PASS_KM, (pair, event, reference_range)
        events_all = [e for pair in by_pair.values() for e in pair]
        assert {e["reason"] for e in events_all if not e["visible"]} == {
            "range_exceeded",
            "tracking_exceeded",
        }
        assert sum(1 for e in events_all if e["reason"] == "tracking_exceeded") == 12
        assert sum(1 for e in events_all if e["reason"] == "range_exceeded") == 12
        assert sum(1 for e in events_all if e["visible"]) == 26

    def test_range_experiment_seam_pairs_transition_by_range_only(self, polar_seam_timeline):
        """earth-leo-polar-seam: the thirty co-rotating pairs are feasible from step 0 and
        never lose; each seam pair gains and loses by range at the reference's times."""
        self.check_range_experiment(
            _load_events(polar_seam_timeline), self._declared_start("earth-leo-polar-seam.yaml")
        )

    def test_tracking_experiment_seam_pairs_exceed_and_recover_the_tracking_limit(
        self, polar_seam_tracking_timeline
    ):
        """earth-leo-polar-seam-tracking: every seam pair loses twice by range and twice by
        tracking per orbit, recovering from each, at the reference's times and reasons;
        the tracking losses happen with line of sight clear and range far inside its limit."""
        self.check_tracking_experiment(
            _load_events(polar_seam_tracking_timeline),
            self._declared_start("earth-leo-polar-seam-tracking.yaml"),
        )

    @staticmethod
    def _isl(event) -> bool:
        return event["event_type"] == "VisibilityEvent" and event["data"]["elevation_deg"] is None

    def test_checker_rejects_a_uniformly_late_timeline(self, polar_seam_tracking_timeline):
        """Every ISL event shifted 60 s later, clock ticks unchanged: a timeline that
        would pass a check anchored to its own first event must fail one anchored to
        the declared session start."""
        from datetime import timedelta

        events = _load_events(polar_seam_tracking_timeline)
        for event in events:
            if self._isl(event):
                stamp = datetime.fromisoformat(event["data"]["sim_time"].replace("Z", "+00:00"))
                event["data"]["sim_time"] = (stamp + timedelta(seconds=60)).strftime(
                    "%Y-%m-%dT%H:%M:%SZ"
                )
        with pytest.raises(AssertionError):
            self.check_tracking_experiment(
                events, self._declared_start("earth-leo-polar-seam-tracking.yaml")
            )

    def test_checker_rejects_feasible_pairs_left_unscheduled(self, polar_seam_tracking_timeline):
        """Every feasible ISL left unscheduled and attributed to terminal capacity:
        physically correct visibility with the wrong allocation must fail."""
        events = _load_events(polar_seam_tracking_timeline)
        for event in events:
            if self._isl(event) and event["data"]["visible"]:
                event["data"]["scheduled"] = False
                event["data"]["unscheduled_reason"] = "isl_terminal_capacity"
        with pytest.raises(AssertionError):
            self.check_tracking_experiment(
                events, self._declared_start("earth-leo-polar-seam-tracking.yaml")
            )

    def test_checker_rejects_a_tracking_loss_reported_beyond_range(
        self, polar_seam_tracking_timeline
    ):
        """Every tracking-loss event reporting 7000 km, beyond the range limit: the
        reported geometry must agree with the reference, so this must fail."""
        events = _load_events(polar_seam_tracking_timeline)
        for event in events:
            if (
                self._isl(event)
                and event["data"]["visibility_reject_reason"] == "tracking_exceeded"
            ):
                event["data"]["range_km"] = 7000.0
        with pytest.raises(AssertionError):
            self.check_tracking_experiment(
                events, self._declared_start("earth-leo-polar-seam-tracking.yaml")
            )

    def test_checker_rejects_a_missing_tracking_transition(self, polar_seam_tracking_timeline):
        """One tracking-loss event removed: the reference's sequence no longer matches."""
        events = _load_events(polar_seam_tracking_timeline)
        removed = False
        kept = []
        for event in events:
            if (
                not removed
                and self._isl(event)
                and event["data"]["visibility_reject_reason"] == "tracking_exceeded"
            ):
                removed = True
                continue
            kept.append(event)
        assert removed
        with pytest.raises(AssertionError):
            self.check_tracking_experiment(
                kept, self._declared_start("earth-leo-polar-seam-tracking.yaml")
            )

    def test_tracking_experiment_resolves_its_declared_facts(
        self, polar_seam_tracking_session_path
    ):
        """Resolution carries the declared phasing, orbit and terminal limits into OME's inputs."""
        import math

        from ome.main import _load_session_config

        cfg = _load_session_config(polar_seam_tracking_session_path, run_id="test-seam-facts")
        Path(polar_seam_tracking_session_path).unlink(missing_ok=True)
        elements = {sat.node_id: sat.elements for sat in cfg.satellites}
        limits = {sat.node_id: sat.isl_terminals[0] for sat in cfg.satellites}
        max_range_km, max_rate_deg_s = self._terminal_limits()

        assert math.isclose(math.degrees(elements["leo-sat-p05s00"].raan_rad), 158.0)
        assert math.isclose(math.degrees(elements["leo-sat-p05s00"].mean_anomaly_rad), 75.0)
        assert math.isclose(math.degrees(elements["leo-sat-p00s02"].raan_rad), 0.0)
        assert math.isclose(math.degrees(elements["leo-sat-p00s02"].mean_anomaly_rad), 120.0)
        assert math.isclose(elements["leo-sat-p05s00"].semi_major_axis_km, 7158.137)
        for node in ("leo-sat-p05s00", "leo-sat-p00s02"):
            assert limits[node].max_range_km == max_range_km
            assert limits[node].max_tracking_rate_deg_s == max_rate_deg_s
        assignments = {
            (node, a.peer_node_id): (a.interface, a.link_type) for node, a in cfg.neighbors
        }
        assert assignments[("leo-sat-p05s00", "leo-sat-p00s02")] == ("isl0", "cross_plane_isl")
        assert assignments[("leo-sat-p00s02", "leo-sat-p05s00")] == ("isl0", "cross_plane_isl")
        assert len(assignments) == 12
