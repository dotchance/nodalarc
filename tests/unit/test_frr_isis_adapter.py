"""Unit tests for FRR IS-IS adapter — parse canned vtysh output."""

from measurement.adapters.frr_isis_adapter import (
    parse_isis_neighbors,
    parse_isis_log_line,
)


class TestParseIsisNeighbors:
    """Test IS-IS neighbor output parsing."""

    def test_empty_output(self):
        assert parse_isis_neighbors("") == {}

    def test_header_only(self):
        output = (
            "Area NODAL:\n"
            "  System Id           Interface   L  State        Holdtime SNPA\n"
        )
        assert parse_isis_neighbors(output) == {}

    def test_single_neighbor_up(self):
        output = (
            "Area NODAL:\n"
            "  System Id           Interface   L  State        Holdtime SNPA\n"
            "  0000.0001.0001      isl0        1  Up           29       P2P\n"
        )
        result = parse_isis_neighbors(output)
        assert len(result) == 1
        key = "0000.0001.0001:isl0"
        assert key in result
        assert result[key]["system_id"] == "0000.0001.0001"
        assert result[key]["interface"] == "isl0"
        assert result[key]["state"] == "Up"

    def test_multiple_neighbors(self):
        output = (
            "Area NODAL:\n"
            "  System Id           Interface   L  State        Holdtime SNPA\n"
            "  0000.0001.0001      isl0        1  Up           29       P2P\n"
            "  0000.0002.0001      isl1        1  Up           28       P2P\n"
            "  0001.0000.0001      isl2        1  Initializing 25       P2P\n"
        )
        result = parse_isis_neighbors(output)
        assert len(result) == 3
        assert result["0000.0001.0001:isl0"]["state"] == "Up"
        assert result["0000.0002.0001:isl1"]["state"] == "Up"
        assert result["0001.0000.0001:isl2"]["state"] == "Initializing"

    def test_dual_level_same_neighbor_prefers_up(self):
        """Same neighbor on same interface at L1 and L2."""
        output = (
            "Area NODAL:\n"
            "  System Id           Interface   L  State        Holdtime SNPA\n"
            "  0000.0001.0001      isl0        1  Initializing 29       P2P\n"
            "  0000.0001.0001      isl0        2  Up           28       P2P\n"
        )
        result = parse_isis_neighbors(output)
        # Should keep the Up state (last one wins if Up, or Up always wins)
        assert result["0000.0001.0001:isl0"]["state"] == "Up"

    def test_handles_malformed_lines(self):
        output = (
            "Area NODAL:\n"
            "  bad line\n"
            "  0000.0001.0001      isl0        1  Up           29       P2P\n"
            "  \n"
        )
        result = parse_isis_neighbors(output)
        assert len(result) == 1


class TestParseIsisLogLine:
    """Test IS-IS log line parsing for SPF/LSP events."""

    def test_spf_start(self):
        line = "2024-01-01 00:00:01 ISIS-SPF: Scheduling L1 SPF run"
        result = parse_isis_log_line(line)
        assert result is not None
        assert result["type"] == "spf_start"

    def test_spf_end(self):
        line = "2024-01-01 00:00:02 ISIS-SPF: L1 SPF completed in 5ms"
        result = parse_isis_log_line(line)
        assert result is not None
        assert result["type"] == "spf_end"

    def test_lsp_flood(self):
        line = "2024-01-01 00:00:03 ISIS: LSP flood 0000.0001.0001.00-00"
        result = parse_isis_log_line(line)
        assert result is not None
        assert result["type"] == "lsp_flood"

    def test_irrelevant_line(self):
        line = "2024-01-01 00:00:04 ISIS: Hello received on isl0"
        result = parse_isis_log_line(line)
        assert result is None

    def test_empty_line(self):
        assert parse_isis_log_line("") is None

    def test_spf_algorithm_started(self):
        line = "SPF algorithm started for area 49.0001"
        result = parse_isis_log_line(line)
        assert result is not None
        assert result["type"] == "spf_start"

    def test_spf_algorithm_complete(self):
        line = "SPF algorithm complete, 4 vertices processed"
        result = parse_isis_log_line(line)
        assert result is not None
        assert result["type"] == "spf_end"
