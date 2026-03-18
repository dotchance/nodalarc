"""Tests for stack_resolver — every valid combo and invalid combos."""

import pytest

from nodalarc.stack_resolver import ResolvedStack, resolve_stack


# --- Valid combinations ---

class TestOSPF:
    def test_ospf_plain(self):
        r = resolve_stack("ospf", [])
        assert r.daemons == ["zebra", "ospfd"]
        assert r.image == "nodalarc/frr:10"
        assert r.mi_adapter == "frr_ospf_adapter"
        assert r.segment_routing is False
        assert r.template_variables["protocol"] == "ospf"
        assert r.template_variables["reference_bandwidth"] == 10000
        assert "te_enabled" not in r.template_variables
        template_srcs = [t.src for t in r.template_files]
        assert "zebra.conf.j2" in template_srcs
        assert "ospfd.conf.j2" in template_srcs

    def test_ospf_te(self):
        r = resolve_stack("ospf", ["te"])
        assert r.daemons == ["zebra", "ospfd"]
        assert r.template_variables["te_enabled"] is True
        assert "mpls_enabled" not in r.template_variables
        assert r.segment_routing is False

    def test_ospf_te_mpls(self):
        r = resolve_stack("ospf", ["te", "mpls"])
        assert r.daemons == ["zebra", "ospfd", "ldpd"]
        assert r.template_variables["te_enabled"] is True
        assert r.template_variables["mpls_enabled"] is True
        template_srcs = [t.src for t in r.template_files]
        assert "ldpd.conf.j2" in template_srcs


class TestISIS:
    def test_isis_plain(self):
        r = resolve_stack("isis", [])
        assert r.daemons == ["zebra", "isisd"]
        assert r.mi_adapter == "frr_isis_adapter"
        assert r.segment_routing is False
        assert r.template_variables["protocol"] == "isis"
        assert "sr_enabled" not in r.template_variables

    def test_isis_sr(self):
        r = resolve_stack("isis", ["sr"])
        assert r.daemons == ["zebra", "isisd", "pathd"]
        assert r.segment_routing is True
        assert r.ttl_propagation == "uniform"
        assert r.template_variables["sr_enabled"] is True
        assert r.template_variables["srgb_start"] == 16000
        assert r.template_variables["srgb_end"] == 23999
        template_srcs = [t.src for t in r.template_files]
        assert "pathd.conf.j2" in template_srcs

    def test_isis_te(self):
        r = resolve_stack("isis", ["te"])
        assert r.daemons == ["zebra", "isisd"]
        assert r.template_variables["te_enabled"] is True
        assert r.segment_routing is False

    def test_isis_te_mpls(self):
        r = resolve_stack("isis", ["te", "mpls"])
        assert r.daemons == ["zebra", "isisd", "ldpd"]
        assert r.template_variables["te_enabled"] is True
        assert r.template_variables["mpls_enabled"] is True


class TestStatic:
    def test_static_sr(self):
        r = resolve_stack("static", ["sr"])
        assert r.daemons == ["zebra", "staticd"]
        assert r.mi_adapter is None
        assert r.segment_routing is True
        assert r.ttl_propagation == "uniform"
        assert r.max_compression == 1
        assert r.template_variables["protocol"] == "static"

    def test_static_requires_sr(self):
        with pytest.raises(ValueError, match="static protocol requires sr"):
            resolve_stack("static", [])


class TestNodalPath:
    def test_nodalpath(self):
        r = resolve_stack("nodalpath", [])
        assert r.daemons == []
        assert r.template_files == []
        assert r.image == "nodalpath-fwd:latest"
        assert r.transport == "grpc"
        assert r.mi_adapter is None
        assert r.host_modules == ["mpls_router", "mpls_iptunnel"]
        assert len(r.env) == 2
        assert r.security_context_capabilities == ["NET_ADMIN", "NET_RAW", "SYS_ADMIN"]

    def test_nodalpath_no_extensions(self):
        with pytest.raises(ValueError, match="does not accept extensions"):
            resolve_stack("nodalpath", ["sr"])


# --- Invalid combinations ---

class TestInvalid:
    def test_sr_requires_isis_or_static(self):
        with pytest.raises(ValueError, match="SR extension requires"):
            resolve_stack("ospf", ["sr"])

    def test_te_requires_ospf_or_isis(self):
        with pytest.raises(ValueError, match="TE extension requires"):
            resolve_stack("static", ["sr", "te"])

    def test_mpls_requires_te(self):
        with pytest.raises(ValueError, match="MPLS extension requires TE"):
            resolve_stack("isis", ["mpls"])

    def test_mpls_requires_te(self):
        with pytest.raises(ValueError, match="MPLS extension requires TE"):
            resolve_stack("ospf", ["mpls"])

    def test_unknown_protocol(self):
        with pytest.raises(ValueError, match="Unknown protocol"):
            resolve_stack("bgp", [])


class TestResolvedStackFrozen:
    def test_frozen(self):
        r = resolve_stack("ospf", [])
        with pytest.raises(AttributeError):
            r.image = "something"  # type: ignore


class TestTemplateFilePaths:
    """All template files have correct dst paths."""

    @pytest.mark.parametrize("protocol,extensions", [
        ("ospf", []),
        ("ospf", ["te"]),
        ("ospf", ["te", "mpls"]),
        ("isis", []),
        ("isis", ["sr"]),
        ("isis", ["te"]),
        ("isis", ["te", "mpls"]),
        ("static", ["sr"]),
    ])
    def test_dst_paths(self, protocol, extensions):
        r = resolve_stack(protocol, extensions)
        for tf in r.template_files:
            assert tf.dst.startswith("/etc/frr/")
            assert tf.dst.endswith(".conf")
