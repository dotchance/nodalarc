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
    independent of OME's propagator and decision functions; OME's timeline
    must reproduce every transition within one 10 s sample with the same
    physical reason. "Some gains and some losses" is not the requirement.
    """

    ISL_MAX_RANGE_KM = 6000.0
    ISL_MAX_RATE_DEG_S = 2.0
    SAMPLE_S = 10.0
    ORBIT_S = 6030.0

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

    @classmethod
    def _terminal_limits(cls) -> tuple[float, float]:
        terminal = load_configuration_yaml(
            (
                PROJECT_ROOT / "catalog/nodalarc/terminals/optical/optical-low-orbit-isl.yaml"
            ).read_text()
        )["terminal"]
        return float(terminal["max_range_km"]), float(terminal["limits"]["max_tracking_rate_deg_s"])

    @staticmethod
    def _isl_events_by_pair(path) -> dict[tuple[str, str], list[tuple[float, bool, str]]]:
        events = _load_events(path)
        by_pair: dict[tuple[str, str], list[tuple[float, bool, str]]] = {}
        epoch = None
        for event in events:
            if (
                event["event_type"] != "VisibilityEvent"
                or event["data"]["elevation_deg"] is not None
            ):
                continue
            data = event["data"]
            stamp = datetime.fromisoformat(data["sim_time"].replace("Z", "+00:00"))
            epoch = epoch or stamp
            pair = (min(data["node_a"], data["node_b"]), max(data["node_a"], data["node_b"]))
            by_pair.setdefault(pair, []).append(
                ((stamp - epoch).total_seconds(), data["visible"], data["visibility_reject_reason"])
            )
        return by_pair

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

    def _assert_pair_follows_reference(self, observed, expected, label):
        assert len(observed) == len(expected), (label, observed, expected)
        for (t_obs, vis_obs, reason_obs), (t_exp, vis_exp, reason_exp) in zip(observed, expected):
            assert vis_obs == vis_exp, (label, t_obs, reason_obs, t_exp, reason_exp)
            assert reason_obs == reason_exp, (label, t_obs, reason_obs, t_exp, reason_exp)
            assert abs(t_obs - t_exp) <= self.SAMPLE_S, (label, t_obs, t_exp, reason_exp)

    def test_range_experiment_seam_pairs_transition_by_range_only(self, polar_seam_timeline):
        """earth-leo-polar-seam: the thirty co-rotating pairs are feasible from step 0 and
        never lose; each seam pair gains and loses by range at the reference's times."""
        max_range_km, max_rate_deg_s = self._terminal_limits()
        shell = self._shell("earth-leo-polar-36.yaml")
        by_pair = self._isl_events_by_pair(polar_seam_timeline)

        seam = {pair for pair in by_pair if {pair[0][-6:-3], pair[1][-6:-3]} == {"p00", "p05"}}
        corotating = set(by_pair) - seam
        assert len(seam) == 6 and len(corotating) == 30
        for pair in corotating:
            assert by_pair[pair] == [(0.0, True, "ok")], pair
        for pair in sorted(seam):
            slot_a = int(pair[0][-2:])
            expected = self._expected_events(
                shell,
                (5, slot_a),
                (0, slot_a),
                max_range_km=max_range_km,
                max_rate_deg_s=max_rate_deg_s,
            )
            self._assert_pair_follows_reference(by_pair[pair], expected, pair)
        seam_events = [e for pair in seam for e in by_pair[pair]]
        assert sum(1 for e in seam_events if not e[1]) == 12
        assert sum(1 for e in seam_events if e[1]) == 14
        assert {e[2] for e in seam_events if not e[1]} == {"range_exceeded"}

    def test_tracking_experiment_seam_pairs_exceed_and_recover_the_tracking_limit(
        self, polar_seam_tracking_timeline
    ):
        """earth-leo-polar-seam-tracking: every seam pair loses twice by range and twice by
        tracking per orbit, recovering from each, at the reference's times and reasons;
        the tracking losses happen with line of sight clear and range far inside its limit."""
        max_range_km, max_rate_deg_s = self._terminal_limits()
        shell = self._shell("earth-leo-polar-36-seam-crossing.yaml")
        by_pair = self._isl_events_by_pair(polar_seam_tracking_timeline)

        assert len(by_pair) == 6
        for pair in sorted(by_pair):
            slot_of = {node[-6:-3]: int(node[-2:]) for node in pair}
            a, b = (5, slot_of["p05"]), (0, slot_of["p00"])
            assert (slot_of["p05"] + 2) % 6 == slot_of["p00"], pair
            expected = self._expected_events(
                shell, a, b, max_range_km=max_range_km, max_rate_deg_s=max_rate_deg_s
            )
            self._assert_pair_follows_reference(by_pair[pair], expected, pair)
            for t_s, visible, reason in by_pair[pair]:
                if reason == "tracking_exceeded":
                    range_km, rate, clear = encounter(shell, a, b, t_s)
                    assert clear and range_km < 400.0 and rate > max_rate_deg_s, (
                        pair,
                        t_s,
                        range_km,
                        rate,
                    )
        events = [e for pair in by_pair.values() for e in pair]
        reasons = {reason for _, visible, reason in events if not visible}
        assert reasons == {"range_exceeded", "tracking_exceeded"}
        assert sum(1 for e in events if e[2] == "tracking_exceeded") == 12
        assert sum(1 for e in events if e[2] == "range_exceeded") == 12
        assert sum(1 for e in events if e[1]) == 26

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

        assert math.isclose(math.degrees(elements["leo-sat-p05s00"].raan_rad), 158.0)
        assert math.isclose(math.degrees(elements["leo-sat-p05s00"].mean_anomaly_rad), 75.0)
        assert math.isclose(math.degrees(elements["leo-sat-p00s02"].raan_rad), 0.0)
        assert math.isclose(math.degrees(elements["leo-sat-p00s02"].mean_anomaly_rad), 120.0)
        assert math.isclose(elements["leo-sat-p05s00"].semi_major_axis_km, 7158.137)
        for node in ("leo-sat-p05s00", "leo-sat-p00s02"):
            assert limits[node].max_range_km == self.ISL_MAX_RANGE_KM
            assert limits[node].max_tracking_rate_deg_s == self.ISL_MAX_RATE_DEG_S
        assignments = {
            (node, a.peer_node_id): (a.interface, a.link_type) for node, a in cfg.neighbors
        }
        assert assignments[("leo-sat-p05s00", "leo-sat-p00s02")] == ("isl0", "cross_plane_isl")
        assert assignments[("leo-sat-p00s02", "leo-sat-p05s00")] == ("isl0", "cross_plane_isl")
        assert len(assignments) == 12
