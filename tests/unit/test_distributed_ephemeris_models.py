# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""Tests for PRD v0.71 distributed ephemeris models.

Verifies serialization round-trips, frozen enforcement, discriminated union
dispatch, and backward-compatible epoch_id defaults on ClockTick and
LinkStateSnapshot.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from nodalarc.models.events import (
    ClockTick,
    EphemerisNodeFixed,
    EphemerisNodeKeplerian,
    EphemerisNodeTLE,
    PlaybackState,
    SessionEphemeris,
)
from nodalarc.models.link_state import LinkState, LinkStateSnapshot
from pydantic import ValidationError

# ---------------------------------------------------------------------------
# EphemerisNodeKeplerian
# ---------------------------------------------------------------------------


class TestEphemerisNodeKeplerian:
    def test_round_trip(self):
        node = EphemerisNodeKeplerian(
            propagator="keplerian-circular",
            altitude_km=550.0,
            inclination_deg=53.0,
            raan_deg=0.0,
            true_anomaly_deg=45.0,
            plane=3,
            slot=7,
        )
        data = node.model_dump(mode="json")
        restored = EphemerisNodeKeplerian.model_validate(data)
        assert restored == node
        assert data["type"] == "keplerian"
        assert data["propagator"] == "keplerian-circular"

    def test_frozen(self):
        node = EphemerisNodeKeplerian(
            propagator="keplerian-circular",
            altitude_km=550.0,
            inclination_deg=53.0,
            raan_deg=0.0,
            true_anomaly_deg=0.0,
            plane=0,
            slot=0,
        )
        with pytest.raises(ValidationError):
            node.altitude_km = 600.0

    def test_type_discriminator_default(self):
        node = EphemerisNodeKeplerian(
            propagator="keplerian-circular",
            altitude_km=550.0,
            inclination_deg=53.0,
            raan_deg=0.0,
            true_anomaly_deg=0.0,
            plane=0,
            slot=0,
        )
        assert node.type == "keplerian"
        assert node.propagator == "keplerian-circular"

    def test_j2_propagator_identity_round_trip(self):
        node = EphemerisNodeKeplerian(
            propagator="j2-mean-elements",
            altitude_km=550.0,
            inclination_deg=53.0,
            raan_deg=0.0,
            true_anomaly_deg=0.0,
            plane=0,
            slot=0,
        )
        restored = EphemerisNodeKeplerian.model_validate(node.model_dump(mode="json"))
        assert restored.propagator == "j2-mean-elements"

    def test_propagator_identity_required(self):
        with pytest.raises(ValidationError, match="propagator"):
            EphemerisNodeKeplerian(
                altitude_km=550.0,
                inclination_deg=53.0,
                raan_deg=0.0,
                true_anomaly_deg=0.0,
                plane=0,
                slot=0,
            )


# ---------------------------------------------------------------------------
# EphemerisNodeTLE
# ---------------------------------------------------------------------------


class TestEphemerisNodeTLE:
    def test_round_trip(self):
        node = EphemerisNodeTLE(
            tle_line_1="1 25544U 98067A   21075.51041667  .00001264  00000-0  29660-4 0  9993",
            tle_line_2="2 25544  51.6442  21.5417 0002426  95.1670  21.8444 15.48974333273145",
            plane=0,
            slot=0,
            norad_id=25544,
        )
        data = node.model_dump(mode="json")
        restored = EphemerisNodeTLE.model_validate(data)
        assert restored == node
        assert data["type"] == "tle"

    def test_frozen(self):
        node = EphemerisNodeTLE(
            tle_line_1="1 25544U 98067A   21075.51041667  .00001264  00000-0  29660-4 0  9993",
            tle_line_2="2 25544  51.6442  21.5417 0002426  95.1670  21.8444 15.48974333273145",
            plane=0,
            slot=0,
        )
        with pytest.raises(ValidationError):
            node.plane = 1


# ---------------------------------------------------------------------------
# EphemerisNodeFixed
# ---------------------------------------------------------------------------


class TestEphemerisNodeFixed:
    def test_round_trip(self):
        node = EphemerisNodeFixed(lat_deg=39.04, lon_deg=-77.49, alt_km=0.095)
        data = node.model_dump(mode="json")
        restored = EphemerisNodeFixed.model_validate(data)
        assert restored == node
        assert data["type"] == "fixed"

    def test_frozen(self):
        node = EphemerisNodeFixed(lat_deg=0.0, lon_deg=0.0, alt_km=0.0)
        with pytest.raises(ValidationError):
            node.lat_deg = 10.0


# ---------------------------------------------------------------------------
# SessionEphemeris — discriminated union dispatch
# ---------------------------------------------------------------------------


class TestSessionEphemeris:
    def _make(self) -> SessionEphemeris:
        return SessionEphemeris(
            epoch_id=0,
            sim_time=datetime(2025, 1, 1, tzinfo=UTC),
            epoch_unix=1735689600.0,
            nodes={
                "sat-P00S00": EphemerisNodeKeplerian(
                    propagator="keplerian-circular",
                    altitude_km=550.0,
                    inclination_deg=53.0,
                    raan_deg=0.0,
                    true_anomaly_deg=0.0,
                    plane=0,
                    slot=0,
                ),
                "gs-ashburn": EphemerisNodeFixed(lat_deg=39.04, lon_deg=-77.49, alt_km=0.095),
            },
        )

    def test_round_trip_json(self):
        eph = self._make()
        json_str = eph.model_dump_json()
        restored = SessionEphemeris.model_validate_json(json_str)
        assert restored == eph

    def test_discriminated_union_keplerian(self):
        eph = self._make()
        sat = eph.nodes["sat-P00S00"]
        assert isinstance(sat, EphemerisNodeKeplerian)
        assert sat.type == "keplerian"
        assert sat.altitude_km == 550.0

    def test_discriminated_union_fixed(self):
        eph = self._make()
        gs = eph.nodes["gs-ashburn"]
        assert isinstance(gs, EphemerisNodeFixed)
        assert gs.type == "fixed"
        assert gs.lat_deg == 39.04

    def test_frozen(self):
        eph = self._make()
        with pytest.raises(ValidationError):
            eph.epoch_id = 1

    def test_empty_nodes_valid(self):
        eph = SessionEphemeris(
            epoch_id=0,
            sim_time=datetime(2025, 1, 1, tzinfo=UTC),
            epoch_unix=1735689600.0,
            nodes={},
        )
        assert len(eph.nodes) == 0


# ---------------------------------------------------------------------------
# PlaybackState
# ---------------------------------------------------------------------------


class TestPlaybackState:
    def test_round_trip(self):
        ps = PlaybackState(epoch_id=3, state="seeking")
        data = ps.model_dump(mode="json")
        restored = PlaybackState.model_validate(data)
        assert restored == ps
        assert data["state"] == "seeking"

    def test_valid_states(self):
        for state in ("seeking", "playing", "paused"):
            ps = PlaybackState(epoch_id=0, state=state)
            assert ps.state == state

    def test_invalid_state_rejected(self):
        with pytest.raises(ValidationError):
            PlaybackState(epoch_id=0, state="fast_forward")

    def test_frozen(self):
        ps = PlaybackState(epoch_id=0, state="playing")
        with pytest.raises(ValidationError):
            ps.state = "paused"


# ---------------------------------------------------------------------------
# ClockTick — epoch_id backward compatibility
# ---------------------------------------------------------------------------


class TestClockTickEpochId:
    def test_default_epoch_id_is_zero(self):
        ct = ClockTick(
            sim_time=datetime(2025, 1, 1, tzinfo=UTC),
            wall_time=datetime(2025, 1, 1, tzinfo=UTC),
            compression_ratio=1.0,
        )
        assert ct.epoch_id == 0

    def test_explicit_epoch_id(self):
        ct = ClockTick(
            sim_time=datetime(2025, 1, 1, tzinfo=UTC),
            wall_time=datetime(2025, 1, 1, tzinfo=UTC),
            compression_ratio=1.0,
            epoch_id=5,
        )
        assert ct.epoch_id == 5

    def test_round_trip_preserves_epoch_id(self):
        ct = ClockTick(
            sim_time=datetime(2025, 1, 1, tzinfo=UTC),
            wall_time=datetime(2025, 1, 1, tzinfo=UTC),
            compression_ratio=10.0,
            epoch_id=42,
        )
        restored = ClockTick.model_validate_json(ct.model_dump_json())
        assert restored.epoch_id == 42

    def test_deserialization_without_epoch_id_defaults_to_zero(self):
        """Pre-v0.71 ClockTick payloads lack epoch_id. Must default to 0."""
        json_str = '{"sim_time":"2025-01-01T00:00:00Z","wall_time":"2025-01-01T00:00:00Z","compression_ratio":1.0}'
        ct = ClockTick.model_validate_json(json_str)
        assert ct.epoch_id == 0


# ---------------------------------------------------------------------------
# LinkStateSnapshot — epoch_id backward compatibility
# ---------------------------------------------------------------------------


class TestLinkStateSnapshotEpochId:
    def _make_link(self) -> LinkState:
        from nodalarc.models.link_state import AdminState, CarrierState, RoutingState

        return LinkState(
            node_a="sat-P00S00",
            node_b="sat-P00S01",
            interface_a="isl0",
            interface_b="isl0",
            admin=AdminState.UP,
            carrier=CarrierState.UP,
            routing=RoutingState.UNKNOWN,
            latency_ms=3.0,
            bandwidth_mbps=1000.0,
            link_type="isl",
            sim_time=datetime(2025, 1, 1, tzinfo=UTC),
        )

    def test_default_epoch_id_is_zero(self):
        snap = LinkStateSnapshot(
            sim_time=datetime(2025, 1, 1, tzinfo=UTC),
            snapshot_seq=1,
            links=(self._make_link(),),
            interval_s=5.0,
        )
        assert snap.epoch_id == 0

    def test_explicit_epoch_id(self):
        snap = LinkStateSnapshot(
            sim_time=datetime(2025, 1, 1, tzinfo=UTC),
            snapshot_seq=1,
            links=(),
            interval_s=5.0,
            epoch_id=7,
        )
        assert snap.epoch_id == 7

    def test_round_trip_preserves_epoch_id(self):
        snap = LinkStateSnapshot(
            sim_time=datetime(2025, 1, 1, tzinfo=UTC),
            snapshot_seq=99,
            links=(),
            interval_s=5.0,
            epoch_id=12,
        )
        restored = LinkStateSnapshot.model_validate_json(snap.model_dump_json())
        assert restored.epoch_id == 12
        assert restored.snapshot_seq == 99
