"""Tests for ground station refactoring — individual files, sets, format detection."""

import pytest
from nodalarc.constellation_loader import (
    load_ground_station_individual,
    load_ground_station_set,
    load_ground_stations,
    load_ground_stations_from_list,
    load_ground_stations_from_set,
    set_ground_station_dirs,
)
from nodalarc.models.ground_station import (
    GroundStationConfig,
    GroundStationFile,
    GroundStationSetConfig,
    TerrestrialPrefixTemplate,
)
from pydantic import ValidationError

from tests.conftest import CONFIGS_DIR

STATIONS_DIR = CONFIGS_DIR / "ground-stations" / "stations"
SETS_DIR = CONFIGS_DIR / "ground-stations" / "sets"


@pytest.fixture(autouse=True)
def _set_gs_dirs():
    set_ground_station_dirs(stations_dir=STATIONS_DIR, sets_dir=SETS_DIR)


class TestIndividualStationRoundTrip:
    """Load each individual station file and verify it round-trips."""

    STATION_NAMES = [
        "hawthorne",
        "ashburn",
        "frankfurt",
        "singapore",
        "sao-paulo",
        "sydney",
        "tokyo",
        "london",
        "mcmurdo",
        "svalbard",
        "fairbanks",
        "punta-arenas",
    ]

    @pytest.mark.parametrize("name", STATION_NAMES)
    def test_load_and_validate(self, name: str):
        station = load_ground_station_individual(name)
        assert station.name == name

    @pytest.mark.parametrize("name", STATION_NAMES)
    def test_round_trip(self, name: str):
        station = load_ground_station_individual(name)
        data = station.model_dump()
        restored = GroundStationConfig.model_validate(data)
        assert restored.name == station.name
        assert restored.lat_deg == station.lat_deg
        assert restored.lon_deg == station.lon_deg


class TestIndividualStationDetails:
    def test_hawthorne_coordinates(self):
        s = load_ground_station_individual("hawthorne")
        assert s.lat_deg == 33.92
        assert s.lon_deg == -118.33

    def test_mcmurdo_polar_config(self):
        s = load_ground_station_individual("mcmurdo")
        assert s.min_elevation_deg == 10
        assert s.scheduling_policy == "longest-pass"
        assert s.terminals is not None
        assert s.terminals[0].type == "rf"

    def test_mcmurdo_explicit_prefixes(self):
        s = load_ground_station_individual("mcmurdo")
        assert s.terrestrial_prefixes is not None
        assert len(s.terrestrial_prefixes) == 3
        assert s.terrestrial_prefixes[0].prefix == "172.16.100.0/24"
        assert s.terrestrial_prefixes[0].metric == 50
        assert s.terrestrial_prefixes[2].prefix == "0.0.0.0/0"
        assert s.terrestrial_prefixes[2].metric == 100

    def test_svalbard_polar_config(self):
        s = load_ground_station_individual("svalbard")
        assert s.min_elevation_deg == 10
        assert s.scheduling_policy == "longest-pass"
        assert s.terminals[0].type == "rf"

    def test_ashburn_defaults(self):
        s = load_ground_station_individual("ashburn")
        assert s.min_elevation_deg == 15.0
        assert s.scheduling_policy is None  # uses default
        assert s.terrestrial_prefixes is not None
        assert s.terrestrial_prefixes[0].prefix == "172.16.2.0/24"
        assert s.terrestrial_prefixes[0].metric == 10

    def test_nonexistent_station(self):
        with pytest.raises(FileNotFoundError, match="Ground station file not found"):
            load_ground_station_individual("does-not-exist")


class TestSetRoundTrip:
    """Load each set file and verify it round-trips."""

    SET_NAMES = ["global", "polar-emphasis", "us-conus", "transatlantic", "transpacific"]

    @pytest.mark.parametrize("name", SET_NAMES)
    def test_load_and_validate(self, name: str):
        gs_set = load_ground_station_set(name)
        assert gs_set.name == name

    @pytest.mark.parametrize("name", SET_NAMES)
    def test_round_trip(self, name: str):
        gs_set = load_ground_station_set(name)
        data = gs_set.model_dump()
        restored = GroundStationSetConfig.model_validate(data)
        assert restored.name == gs_set.name
        assert restored.stations == gs_set.stations


class TestSetDetails:
    def test_global_stations(self):
        gs_set = load_ground_station_set("global")
        assert len(gs_set.stations) == 7
        assert "hawthorne" in gs_set.stations
        assert "mcmurdo" in gs_set.stations

    def test_polar_emphasis_stations(self):
        gs_set = load_ground_station_set("polar-emphasis")
        assert "svalbard" in gs_set.stations
        assert "mcmurdo" in gs_set.stations
        assert "fairbanks" in gs_set.stations

    def test_us_conus_minimal(self):
        gs_set = load_ground_station_set("us-conus")
        assert gs_set.stations == ["hawthorne", "ashburn"]

    def test_default_terrestrial_prefixes(self):
        gs_set = load_ground_station_set("global")
        assert gs_set.default_terrestrial_prefixes is not None
        assert "{gs_index}" in gs_set.default_terrestrial_prefixes.ipv4_template


class TestSetResolution:
    """Verify that loading a set resolves all station references into GroundStationFile."""

    def test_global_set_produces_gs_file(self):
        gs_file = load_ground_stations_from_set("global")
        assert isinstance(gs_file, GroundStationFile)
        assert len(gs_file.stations) == 7

    def test_global_set_station_names(self):
        gs_file = load_ground_stations_from_set("global")
        names = [s.name for s in gs_file.stations]
        assert "hawthorne" in names
        assert "mcmurdo" in names

    def test_global_set_station_names_and_coords(self):
        """Loading set 'global' produces 7 stations with expected names and coords."""
        gs_file = load_ground_stations_from_set("global")

        names = sorted(s.name for s in gs_file.stations)
        assert names == [
            "ashburn",
            "frankfurt",
            "hawthorne",
            "mcmurdo",
            "sao-paulo",
            "singapore",
            "sydney",
        ]

        # Spot-check coordinates
        hawthorne = next(s for s in gs_file.stations if s.name == "hawthorne")
        assert hawthorne.lat_deg == 33.92
        assert hawthorne.lon_deg == -118.33

    def test_mcmurdo_explicit_prefixes_preserved(self):
        """Stations with explicit prefixes keep them even when set has a template."""
        gs_file = load_ground_stations_from_set("global")
        mcmurdo = next(s for s in gs_file.stations if s.name == "mcmurdo")
        assert mcmurdo.terrestrial_prefixes is not None
        assert mcmurdo.terrestrial_prefixes[0].prefix == "172.16.100.0/24"

    def test_set_prefix_template_passed_through(self):
        gs_file = load_ground_stations_from_set("global")
        assert gs_file.default_terrestrial_prefixes is not None
        assert "{gs_index}" in gs_file.default_terrestrial_prefixes.ipv4_template


class TestLoadFromList:
    def test_list_loading(self):
        gs_file = load_ground_stations_from_list(
            ["hawthorne", "ashburn"],
            default_terrestrial_prefixes=TerrestrialPrefixTemplate(),
        )
        assert len(gs_file.stations) == 2
        assert gs_file.stations[0].name == "hawthorne"

    def test_list_preserves_order(self):
        gs_file = load_ground_stations_from_list(["tokyo", "london", "hawthorne"])
        assert [s.name for s in gs_file.stations] == ["tokyo", "london", "hawthorne"]


class TestFormatDetection:
    def test_monolithic_format(self):
        """Monolithic file loads correctly."""
        gs_file = load_ground_stations(CONFIGS_DIR / "ground-stations" / "custom-example.yaml")
        assert len(gs_file.stations) == 4

    def test_individual_format(self):
        """Individual station file loads as single-station GroundStationFile."""
        gs_file = load_ground_stations(STATIONS_DIR / "hawthorne.yaml")
        assert len(gs_file.stations) == 1
        assert gs_file.stations[0].name == "hawthorne"

    def test_set_format(self):
        """Set file loads and resolves all station references."""
        gs_file = load_ground_stations(SETS_DIR / "us-conus.yaml")
        assert len(gs_file.stations) == 2
        assert gs_file.stations[0].name == "hawthorne"


class TestValidationRejections:
    def test_duplicate_station_names_in_set(self):
        with pytest.raises(ValidationError, match="duplicate station references"):
            GroundStationSetConfig(
                name="bad",
                stations=["hawthorne", "hawthorne"],
            )

    def test_empty_set(self):
        with pytest.raises(ValidationError, match="at least one station"):
            GroundStationSetConfig(
                name="empty",
                stations=[],
            )

    def test_missing_station_file_reference(self):
        with pytest.raises(FileNotFoundError):
            load_ground_stations_from_set("nonexistent-set-name")

    def test_invalid_coordinates(self):
        with pytest.raises(ValidationError, match="lat_deg must be"):
            GroundStationConfig(
                name="bad-lat",
                lat_deg=95.0,
                lon_deg=0.0,
            )

    def test_nonexistent_set(self):
        with pytest.raises(FileNotFoundError):
            load_ground_station_set("does-not-exist")
