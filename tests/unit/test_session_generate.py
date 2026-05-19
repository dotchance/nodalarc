"""Tests for session_generator — generate YAML, parse back, assert fields match."""

import pytest
import yaml
from nodalarc.constellation_loader import (
    expand_constellation,
    load_constellation,
    load_ground_stations,
)
from nodalarc.ground_terminals import ground_terminal_type, station_ground_terminal_type
from nodalarc.models.session import SessionConfig
from nodalarc.session_generator import (
    generate_session_yaml,
    load_constellation_presets,
)
from pydantic import ValidationError

from tests.conftest import FIXTURES_DIR


class TestLoadPresets:
    def test_loads_all_presets(self):
        presets = load_constellation_presets()
        assert len(presets) >= 5
        assert "iridium-66" in presets
        assert "starlink-early-44" in presets

    def test_preset_fields(self):
        presets = load_constellation_presets()
        p = presets["iridium-66"]
        assert p.satellite_count == 66
        assert "iridium-66.yaml" in p.constellation
        assert p.ground_stations.endswith(".yaml")

    def test_preset_ground_terminal_types_match_satellite_models(self):
        presets = load_constellation_presets()

        for name, preset in presets.items():
            constellation = load_constellation(preset.constellation)
            satellites = expand_constellation(constellation)
            satellite_types = {ground_terminal_type(sat.ground_terminals) for sat in satellites}

            ground_stations = load_ground_stations(preset.ground_stations)
            station_types = {
                station_ground_terminal_type(ground_stations, station)
                for station in ground_stations.stations
            }

            assert station_types == satellite_types, (
                f"Preset {name} has satellite ground terminals {satellite_types} "
                f"but ground station terminals {station_types}"
            )


class TestGenerateSessionYaml:
    """Generate YAML for every valid preset x protocol x extension combo."""

    @pytest.mark.parametrize(
        "constellation",
        [
            "iridium-66",
            "iridium-small-36",
            "starlink-early-44",
            "oneweb-60",
            "kuiper-50",
        ],
    )
    @pytest.mark.parametrize(
        "protocol,extensions",
        [
            ("ospf", []),
            ("ospf", ["te"]),
            ("ospf", ["te", "mpls"]),
            ("isis", []),
            ("isis", ["sr"]),
            ("isis", ["te"]),
            ("isis", ["te", "mpls"]),
            ("ospf", ["sr"]),
        ],
    )
    def test_generate_and_roundtrip(self, constellation, protocol, extensions):
        yaml_str, warnings = generate_session_yaml(
            constellation=constellation,
            protocol=protocol,
            extensions=extensions,
            orbit_propagator="keplerian-circular",
        )
        # Must parse back to valid SessionConfig
        raw = yaml.safe_load(yaml_str)
        session = SessionConfig.model_validate(raw)

        assert (
            session.session.name == f"{constellation}-{protocol}-{'-'.join(extensions) or 'plain'}"
        )
        assert session.routing.protocol == protocol
        assert session.routing.extensions == extensions
        assert session.routing.stack is None  # Wizard sessions use protocol, not stack

    @pytest.mark.parametrize("area_strategy", ["flat", "stripe", "per-plane"])
    def test_area_strategies(self, area_strategy):
        yaml_str, warnings = generate_session_yaml(
            constellation="iridium-small-36",
            protocol="ospf",
            extensions=[],
            orbit_propagator="keplerian-circular",
            area_strategy=area_strategy,
        )
        raw = yaml.safe_load(yaml_str)
        session = SessionConfig.model_validate(raw)
        assert session.routing.area_assignment is not None
        assert session.routing.area_assignment.strategy == area_strategy

    def test_longest_remaining_pass_generation_requires_horizon(self):
        with pytest.raises(ValidationError, match="lookahead_horizon_ticks"):
            generate_session_yaml(
                constellation="iridium-small-36",
                protocol="ospf",
                extensions=[],
                orbit_propagator="keplerian-circular",
                ground_policy="longest-remaining-pass",
            )

    def test_longest_remaining_pass_generation_sets_horizon(self):
        yaml_str, _ = generate_session_yaml(
            constellation="iridium-small-36",
            protocol="ospf",
            extensions=[],
            orbit_propagator="keplerian-circular",
            ground_policy="longest-remaining-pass",
            ground_lookahead_horizon_ticks=600,
        )
        raw = yaml.safe_load(yaml_str)
        session = SessionConfig.model_validate(raw)
        assert session.scheduling.ground.policy == "longest-remaining-pass"
        assert session.scheduling.ground.lookahead_horizon_ticks == 600


class TestGenerateInvalid:
    def test_unknown_constellation(self):
        with pytest.raises(ValueError, match="Unknown constellation"):
            generate_session_yaml("nonexistent", "ospf", [], orbit_propagator="keplerian-circular")

    def test_invalid_combo(self):
        with pytest.raises(ValueError, match="distributed separately"):
            generate_session_yaml(
                "iridium-66", "nodalpath", ["sr"], orbit_propagator="keplerian-circular"
            )


class TestNodalPathExternal:
    def test_nodalpath_sessions_are_not_generated_by_nodalarc(self):
        with pytest.raises(ValueError, match="distributed separately"):
            generate_session_yaml(
                "iridium-66", "nodalpath", [], orbit_propagator="keplerian-circular"
            )


class TestOrbitPropagatorGeneration:
    def test_j2_propagator_is_the_single_fidelity_knob(self):
        yaml_str, _ = generate_session_yaml(
            "iridium-small-36",
            "ospf",
            [],
            orbit_propagator="j2-mean-elements",
        )
        raw = yaml.safe_load(yaml_str)
        session = SessionConfig.model_validate(raw)

        assert session.orbit.propagator == "j2-mean-elements"
        assert session.orbit.fidelity_label == "j2-mean-elements"
        assert session.dispatch.substrate_compensation.rtt_to_one_way == "half-rtt"

    def test_sgp4_requires_tle_constellation(self):
        with pytest.raises(ValueError, match="requires a TLE constellation"):
            generate_session_yaml(
                "iridium-small-36",
                "ospf",
                [],
                orbit_propagator="sgp4-tle",
            )

    def test_sgp4_tle_custom_constellation_sets_matching_fidelity(self):
        yaml_str, _ = generate_session_yaml(
            "custom-tle",
            "ospf",
            [],
            custom_constellation={
                "mode": "tle",
                "name": "sample-tle",
                "tle_file": str(FIXTURES_DIR / "tles/sample.tle"),
                "filter": {"max_count": 1},
                "default_terminals": {
                    "isl": [
                        {
                            "type": "optical",
                            "count": 2,
                            "max_range_km": 5000,
                            "bandwidth_mbps": 1000,
                            "max_tracking_rate_deg_s": 3.0,
                        }
                    ],
                    "ground": [{"type": "rf", "count": 1, "bandwidth_mbps": 1000}],
                },
            },
            custom_ground_stations=[
                {"name": "ashburn", "lat_deg": 39.04, "lon_deg": -77.49, "alt_km": 0.095}
            ],
            orbit_propagator="sgp4-tle",
        )
        raw = yaml.safe_load(yaml_str)
        session = SessionConfig.model_validate(raw)

        assert session.orbit.propagator == "sgp4-tle"
        assert session.orbit.fidelity_label == "sgp4-tle"
        assert session.orbit.tle_max_age_days == 7.0


class TestRoutingConfigRoundtrip:
    """Verify routing config fields survive generate → YAML → parse."""

    @pytest.mark.parametrize(
        "protocol,bfd",
        [
            ("isis", False),
            ("isis", True),
            ("ospf", False),
            ("ospf", True),
        ],
    )
    def test_bfd_toggle(self, protocol, bfd):
        yaml_str, _ = generate_session_yaml(
            constellation="iridium-small-36",
            protocol=protocol,
            extensions=[],
            orbit_propagator="keplerian-circular",
            routing_config={"bfd": bfd},
        )
        raw = yaml.safe_load(yaml_str)
        session = SessionConfig.model_validate(raw)
        assert session.routing.bfd is bfd

    def test_isis_timers(self):
        timers = {
            "isis_hello_interval": 3,
            "isis_hello_multiplier": 5,
            "spf_init_delay": 100,
            "spf_short_delay": 500,
            "spf_long_delay": 2000,
            "spf_holddown": 5000,
            "spf_time_to_learn": 1000,
        }
        yaml_str, _ = generate_session_yaml(
            constellation="iridium-small-36",
            protocol="isis",
            extensions=[],
            orbit_propagator="keplerian-circular",
            routing_config=timers,
        )
        raw = yaml.safe_load(yaml_str)
        session = SessionConfig.model_validate(raw)
        assert session.routing.isis_hello_interval == 3
        assert session.routing.isis_hello_multiplier == 5
        assert session.routing.spf_init_delay == 100
        assert session.routing.spf_short_delay == 500
        assert session.routing.spf_long_delay == 2000
        assert session.routing.spf_holddown == 5000
        assert session.routing.spf_time_to_learn == 1000

    def test_ospf_timers(self):
        timers = {
            "ospf_hello_interval": 10,
            "ospf_dead_interval": 40,
            "ospf_spf_delay": 200,
            "ospf_spf_initial_hold": 1000,
            "ospf_spf_max_hold": 5000,
        }
        yaml_str, _ = generate_session_yaml(
            constellation="iridium-small-36",
            protocol="ospf",
            extensions=[],
            orbit_propagator="keplerian-circular",
            routing_config=timers,
        )
        raw = yaml.safe_load(yaml_str)
        session = SessionConfig.model_validate(raw)
        assert session.routing.ospf_hello_interval == 10
        assert session.routing.ospf_dead_interval == 40
        assert session.routing.ospf_spf_delay == 200
        assert session.routing.ospf_spf_initial_hold == 1000
        assert session.routing.ospf_spf_max_hold == 5000

    def test_bfd_timers(self):
        timers = {
            "bfd": True,
            "bfd_detect_multiplier": 5,
            "bfd_rx_interval": 100,
            "bfd_tx_interval": 100,
        }
        yaml_str, _ = generate_session_yaml(
            constellation="iridium-small-36",
            protocol="isis",
            extensions=["te"],
            orbit_propagator="keplerian-circular",
            routing_config=timers,
        )
        raw = yaml.safe_load(yaml_str)
        session = SessionConfig.model_validate(raw)
        assert session.routing.bfd is True
        assert session.routing.bfd_detect_multiplier == 5
        assert session.routing.bfd_rx_interval == 100
        assert session.routing.bfd_tx_interval == 100

    def test_defaults_when_no_routing_config(self):
        yaml_str, _ = generate_session_yaml(
            constellation="iridium-small-36",
            protocol="isis",
            extensions=[],
            orbit_propagator="keplerian-circular",
        )
        raw = yaml.safe_load(yaml_str)
        session = SessionConfig.model_validate(raw)
        assert session.routing.bfd is False
        assert session.routing.isis_hello_interval == 1
        assert session.routing.spf_long_delay == 1000

    @pytest.mark.parametrize(
        "protocol,extensions",
        [
            ("isis", []),
            ("isis", ["te"]),
            ("isis", ["te", "mpls"]),
            ("isis", ["sr"]),
            ("ospf", []),
            ("ospf", ["te"]),
            ("ospf", ["te", "mpls"]),
            ("ospf", ["sr"]),
        ],
    )
    def test_full_routing_config_all_combos(self, protocol, extensions):
        """Every protocol+extension combo with full routing config produces valid YAML."""
        routing_config = {
            "bfd": True,
            "bfd_detect_multiplier": 3,
            "bfd_rx_interval": 300,
            "bfd_tx_interval": 300,
            "isis_hello_interval": 1,
            "isis_hello_multiplier": 3,
            "spf_init_delay": 50,
            "spf_short_delay": 200,
            "spf_long_delay": 1000,
            "spf_holddown": 2000,
            "spf_time_to_learn": 500,
            "ospf_hello_interval": 1,
            "ospf_dead_interval": 3,
            "ospf_spf_delay": 50,
            "ospf_spf_initial_hold": 200,
            "ospf_spf_max_hold": 1000,
            "mbb_dispatch": True,
            "mbb_overlap_ticks": 3,
        }
        yaml_str, _ = generate_session_yaml(
            constellation="iridium-small-36",
            protocol=protocol,
            extensions=extensions,
            orbit_propagator="keplerian-circular",
            routing_config=routing_config,
        )
        raw = yaml.safe_load(yaml_str)
        session = SessionConfig.model_validate(raw)
        assert session.routing.protocol == protocol
        assert session.routing.bfd is True
        assert session.scheduling.ground.handover_mode == "mbb"
        assert session.scheduling.ground.mbb_overlap_ticks == 3
        assert session.scheduling.ground.mbb_reserve == 1
