"""Unit tests for FRR OSPF adapter — parse canned vtysh output."""

from measurement.adapters.frr_ospf_adapter import (
    parse_ospf_neighbors,
    parse_ospf_log_line,
    _is_full_state,
)


class TestParseOspfNeighbors:
    """Test OSPF neighbor output parsing."""

    def test_empty_output(self):
        assert parse_ospf_neighbors("") == {}

    def test_header_only(self):
        output = (
            "Neighbor ID     Pri State           Up Time         "
            "Dead Time Address         Interface\n"
        )
        assert parse_ospf_neighbors(output) == {}

    def test_single_neighbor_full(self):
        output = (
            "Neighbor ID     Pri State           Up Time         "
            "Dead Time Address         Interface\n"
            "10.0.0.1          1 Full/DROther    0:01:23         "
            "0:00:35   10.0.1.1        isl0:10.0.1.2\n"
        )
        result = parse_ospf_neighbors(output)
        assert len(result) == 1
        key = "10.0.0.1:isl0"
        assert key in result
        assert result[key]["router_id"] == "10.0.0.1"
        assert result[key]["interface"] == "isl0"
        assert result[key]["state"] == "Full/DROther"

    def test_multiple_neighbors(self):
        output = (
            "Neighbor ID     Pri State           Up Time         "
            "Dead Time Address         Interface\n"
            "10.0.0.1          1 Full/DR         0:01:23         "
            "0:00:35   10.0.1.1        isl0:10.0.1.2\n"
            "10.0.1.1          1 Full/DROther    0:01:20         "
            "0:00:38   10.0.2.1        isl1:10.0.2.2\n"
            "10.0.2.1          1 2-Way/DROther   0:00:05         "
            "0:00:30   10.0.3.1        isl2:10.0.3.2\n"
        )
        result = parse_ospf_neighbors(output)
        assert len(result) == 3
        assert result["10.0.0.1:isl0"]["state"] == "Full/DR"
        assert result["10.0.1.1:isl1"]["state"] == "Full/DROther"
        assert result["10.0.2.1:isl2"]["state"] == "2-Way/DROther"

    def test_handles_malformed_lines(self):
        output = (
            "Neighbor ID     Pri State\n"
            "bad line\n"
            "10.0.0.1          1 Full/DR         0:01:23         "
            "0:00:35   10.0.1.1        isl0:10.0.1.2\n"
        )
        result = parse_ospf_neighbors(output)
        assert len(result) == 1


class TestIsFullState:
    """Test OSPF Full state detection."""

    def test_full_dr(self):
        assert _is_full_state("Full/DR") is True

    def test_full_drother(self):
        assert _is_full_state("Full/DROther") is True

    def test_full_plain(self):
        assert _is_full_state("Full") is True

    def test_two_way(self):
        assert _is_full_state("2-Way/DROther") is False

    def test_init(self):
        assert _is_full_state("Init") is False

    def test_down(self):
        assert _is_full_state("Down") is False


class TestParseOspfLogLine:
    """Test OSPF log line parsing for SPF/LSA events."""

    def test_spf_start(self):
        line = "2024-01-01 00:00:01 OSPF: SPF timer fire for area 0.0.0.0"
        result = parse_ospf_log_line(line)
        assert result is not None
        assert result["type"] == "spf_start"

    def test_spf_end(self):
        line = "2024-01-01 00:00:02 OSPF: SPF processing completed in 3ms"
        result = parse_ospf_log_line(line)
        assert result is not None
        assert result["type"] == "spf_end"

    def test_lsa_flood(self):
        line = "2024-01-01 00:00:03 OSPF: Originating Router-LSA for area 0.0.0.0"
        result = parse_ospf_log_line(line)
        assert result is not None
        assert result["type"] == "lsa_flood"

    def test_irrelevant_line(self):
        line = "2024-01-01 00:00:04 OSPF: Hello received from 10.0.0.1 on isl0"
        result = parse_ospf_log_line(line)
        assert result is None

    def test_empty_line(self):
        assert parse_ospf_log_line("") is None

    def test_spf_calculation_started(self):
        line = "SPF calculation started for area 0.0.0.0"
        result = parse_ospf_log_line(line)
        assert result is not None
        assert result["type"] == "spf_start"
