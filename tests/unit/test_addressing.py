"""Test AddressingScheme identity and IP derivation."""

import pytest

from nodalarc.models.addressing import AddressingScheme
from nodalarc.models.session import AddressingConfig


class TestSatelliteIdentity:
    def test_default_sat_id(self):
        a = AddressingScheme()
        assert a.sat_id(3, 7) == "sat-P03S07"

    def test_default_sat_id_zero_padded(self):
        a = AddressingScheme()
        assert a.sat_id(0, 0) == "sat-P00S00"

    def test_default_sat_id_double_digit(self):
        a = AddressingScheme()
        assert a.sat_id(12, 15) == "sat-P12S15"


class TestGroundStationIdentity:
    def test_default_gs_id(self):
        a = AddressingScheme()
        assert a.gs_id("hawthorne") == "gs-hawthorne"

    def test_gs_id_with_hyphen(self):
        a = AddressingScheme()
        assert a.gs_id("sao-paulo") == "gs-sao-paulo"


class TestSatelliteIPv4:
    def test_default_ipv4(self):
        a = AddressingScheme()
        assert a.sat_ipv4(0, 0) == "10.0.0.1"

    def test_ipv4_plane_3_slot_7(self):
        a = AddressingScheme()
        assert a.sat_ipv4(3, 7) == "10.3.7.1"


class TestSatelliteIPv6:
    def test_default_ipv6(self):
        a = AddressingScheme()
        assert a.sat_ipv6(0, 0) == "fd00::0:0:1"

    def test_ipv6_plane_3_slot_7(self):
        a = AddressingScheme()
        assert a.sat_ipv6(3, 7) == "fd00::3:7:1"


class TestGroundStationIP:
    def test_gs_ipv4(self):
        a = AddressingScheme()
        assert a.gs_ipv4(0) == "10.255.0.1"

    def test_gs_ipv4_index_5(self):
        a = AddressingScheme()
        assert a.gs_ipv4(5) == "10.255.5.1"

    def test_gs_ipv6(self):
        a = AddressingScheme()
        assert a.gs_ipv6(0) == "fd00::ff:0:1"

    def test_gs_ipv6_index_3(self):
        a = AddressingScheme()
        assert a.gs_ipv6(3) == "fd00::ff:3:1"


class TestCustomTemplates:
    def test_custom_sat_id_template(self):
        cfg = AddressingConfig(sat_id_template="node-{plane}-{slot}")
        a = AddressingScheme(cfg)
        assert a.sat_id(3, 7) == "node-3-7"

    def test_custom_gs_id_template(self):
        cfg = AddressingConfig(gs_id_template="ground-{name}")
        a = AddressingScheme(cfg)
        assert a.gs_id("hawthorne") == "ground-hawthorne"

    def test_custom_ipv4_sat_template(self):
        cfg = AddressingConfig(ipv4_sat_template="192.168.{plane}.{slot}")
        a = AddressingScheme(cfg)
        assert a.sat_ipv4(1, 2) == "192.168.1.2"


class TestInterfaceNames:
    def test_isl_interfaces(self):
        assert AddressingScheme.isl_interfaces(4) == ["isl0", "isl1", "isl2", "isl3"]

    def test_isl_interfaces_zero(self):
        assert AddressingScheme.isl_interfaces(0) == []

    def test_gnd_interfaces(self):
        assert AddressingScheme.gnd_interfaces(2) == ["gnd0", "gnd1"]

    def test_gnd_interfaces_one(self):
        assert AddressingScheme.gnd_interfaces(1) == ["gnd0"]
