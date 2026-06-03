"""Tests for stack_resolver — every valid combo and invalid combos."""

import pytest
from nodalarc.stack_resolver import resolve_stack

# --- Valid combinations ---


class TestOSPF:
    def test_ospf_plain(self):
        r = resolve_stack("ospf", [])
        assert r.daemons == ["zebra", "ospfd", "staticd"]
        assert r.image == "frr"
        assert r.mi_adapter == "frr_ospf_adapter"
        assert r.segment_routing is False
        assert r.template_variables["protocol"] == "ospf"
        assert r.template_variables["reference_bandwidth"] == 10000
        assert "te_enabled" not in r.template_variables
        template_srcs = [t.src for t in r.template_files]
        assert "zebra.conf.j2" in template_srcs
        assert "ospfd.conf.j2" in template_srcs
        assert "staticd.conf.j2" in template_srcs

    def test_ospf_te(self):
        r = resolve_stack("ospf", ["te"])
        assert r.daemons == ["zebra", "ospfd", "staticd"]
        assert r.template_variables["te_enabled"] is True
        assert "mpls_enabled" not in r.template_variables
        assert r.segment_routing is False

    def test_ospf_te_mpls(self):
        r = resolve_stack("ospf", ["te", "mpls"])
        assert r.daemons == ["zebra", "ospfd", "staticd", "ldpd"]
        assert r.template_variables["te_enabled"] is True
        assert r.template_variables["mpls_enabled"] is True
        template_srcs = [t.src for t in r.template_files]
        assert "ldpd.conf.j2" in template_srcs


class TestISIS:
    def test_isis_plain(self):
        r = resolve_stack("isis", [])
        assert r.daemons == ["zebra", "isisd", "staticd"]
        assert r.mi_adapter == "frr_isis_adapter"
        assert r.segment_routing is False
        assert r.template_variables["protocol"] == "isis"
        assert "sr_enabled" not in r.template_variables

    def test_isis_sr(self):
        r = resolve_stack("isis", ["sr"])
        assert r.daemons == ["zebra", "isisd", "staticd", "pathd"]
        assert r.segment_routing is True
        assert r.ttl_propagation == "pipe"
        assert r.sysctls["net.mpls.ip_ttl_propagate"] == "0"
        assert r.template_variables["sr_enabled"] is True
        assert r.template_variables["srgb_start"] == 16000
        assert r.template_variables["srgb_end"] == 23999
        assert r.template_variables["gs_sid_offset"] == 7900
        template_srcs = [t.src for t in r.template_files]
        assert "pathd.conf.j2" in template_srcs

    def test_isis_te(self):
        r = resolve_stack("isis", ["te"])
        assert r.daemons == ["zebra", "isisd", "staticd"]
        assert r.template_variables["te_enabled"] is True
        assert r.segment_routing is False

    def test_isis_te_mpls(self):
        r = resolve_stack("isis", ["te", "mpls"])
        assert r.daemons == ["zebra", "isisd", "staticd", "ldpd"]
        assert r.template_variables["te_enabled"] is True
        assert r.template_variables["mpls_enabled"] is True


class TestOSPFSR:
    def test_ospf_sr(self):
        r = resolve_stack("ospf", ["sr"])
        assert "pathd" in r.daemons
        assert r.segment_routing is True
        assert r.ttl_propagation == "pipe"
        assert r.sysctls["net.mpls.ip_ttl_propagate"] == "0"
        assert r.template_variables["sr_enabled"] is True
        assert r.template_variables["srgb_start"] == 16000
        assert r.template_variables["gs_sid_offset"] == 7900


class TestNodalPath:
    def test_nodalpath_is_external(self):
        with pytest.raises(ValueError, match="distributed separately"):
            resolve_stack("nodalpath", [])

    def test_nodalpath_no_extensions(self):
        with pytest.raises(ValueError, match="distributed separately"):
            resolve_stack("nodalpath", ["sr"])


# --- Invalid combinations ---


class TestInvalid:
    def test_nodalpath_rejects_extensions(self):
        with pytest.raises(ValueError, match="distributed separately"):
            resolve_stack("nodalpath", ["sr"])

    def test_nodalpath_rejects_te(self):
        with pytest.raises(ValueError, match="distributed separately"):
            resolve_stack("nodalpath", ["te"])

    def test_mpls_requires_te_isis(self):
        with pytest.raises(ValueError, match="MPLS extension requires TE"):
            resolve_stack("isis", ["mpls"])

    def test_mpls_requires_te_ospf(self):
        with pytest.raises(ValueError, match="MPLS extension requires TE"):
            resolve_stack("ospf", ["mpls"])

    def test_unknown_protocol(self):
        with pytest.raises(ValueError, match="Unknown protocol"):
            resolve_stack("bgp", [])

    def test_static_is_not_a_protocol(self):
        with pytest.raises(ValueError):
            resolve_stack("static", ["sr"])


class TestResolvedStackFrozen:
    def test_frozen(self):
        r = resolve_stack("ospf", [])
        with pytest.raises(AttributeError):
            r.image = "something"  # type: ignore


class TestTemplateFilePaths:
    """All template files have correct dst paths."""

    @pytest.mark.parametrize(
        "protocol,extensions",
        [
            ("ospf", []),
            ("ospf", ["te"]),
            ("ospf", ["te", "mpls"]),
            ("ospf", ["sr"]),
            ("isis", []),
            ("isis", ["sr"]),
            ("isis", ["te"]),
            ("isis", ["te", "mpls"]),
        ],
    )
    def test_dst_paths(self, protocol, extensions):
        r = resolve_stack(protocol, extensions)
        for tf in r.template_files:
            assert tf.dst.startswith("/etc/frr/")
            assert tf.dst.endswith(".conf")
