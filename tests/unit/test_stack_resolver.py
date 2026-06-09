"""Tests for stack_resolver — every valid combo and invalid combos."""

import pytest
from nodalarc.models.resolved_session import ResolvedRoutingDomain
from nodalarc.stack_resolver import domain_extensions, resolve_stack, validate_sid_indices


def _domain(protocol: str, capabilities: tuple[str, ...] = ()) -> ResolvedRoutingDomain:
    return ResolvedRoutingDomain(
        domain_id="d1",
        protocol=protocol,
        node_ids=("node-a",),
        capabilities=capabilities,
    )


class TestDomainExtensions:
    def test_bare_mpls_capability_maps_to_ldp_extension(self):
        assert domain_extensions(_domain("isis", ("mpls",))) == ["mpls"]

    def test_mpls_with_segment_routing_is_consumed_by_sr_mpls(self):
        # SR-MPLS provides the MPLS data plane; the declared mpls capability
        # is satisfied by the sr extension, not silently dropped.
        assert domain_extensions(_domain("isis", ("mpls", "segment_routing"))) == ["sr"]

    def test_static_domain_rejects_capabilities(self):
        with pytest.raises(ValueError, match="static domains carry no"):
            domain_extensions(_domain("static", ("mpls",)))

    def test_static_domain_without_capabilities_is_valid(self):
        assert domain_extensions(_domain("static")) == []

    def test_unimplemented_stack_protocol_rejected(self):
        with pytest.raises(ValueError, match="implemented FRR stacks"):
            domain_extensions(_domain("bgp"))


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

    def test_unknown_protocol(self):
        with pytest.raises(ValueError, match="Unknown protocol"):
            resolve_stack("bgp", [])

    def test_static_rejects_sr_extension(self):
        with pytest.raises(ValueError, match="SR extension requires ospf or isis"):
            resolve_stack("static", ["sr"])


class TestMplsWithoutTe:
    """LDP-distributed MPLS is valid without TE on both IGPs.

    The old mpls-requires-te constraint forced the capability layer to
    silently drop a declared bare mpls capability — the rendering must be
    total over the declared grammar instead.
    """

    def test_isis_mpls_alone_resolves_ldp(self):
        r = resolve_stack("isis", ["mpls"])
        assert r.daemons == ["zebra", "isisd", "staticd", "ldpd"]
        assert r.template_variables["mpls_enabled"] is True
        assert "te_enabled" not in r.template_variables
        assert r.sysctls["net.mpls.platform_labels"] == "100000"

    def test_ospf_mpls_alone_resolves_ldp(self):
        r = resolve_stack("ospf", ["mpls"])
        assert r.daemons == ["zebra", "ospfd", "staticd", "ldpd"]
        assert r.template_variables["mpls_enabled"] is True
        assert "te_enabled" not in r.template_variables
        assert r.sysctls["net.mpls.platform_labels"] == "100000"


class TestStaticStack:
    def test_static_plain(self):
        r = resolve_stack("static", [])
        assert r.daemons == ["zebra", "staticd"]
        assert r.image == "frr"
        assert r.mi_adapter is None
        assert r.segment_routing is False
        assert r.template_variables == {"protocol": "static"}
        template_srcs = [t.src for t in r.template_files]
        assert template_srcs == ["zebra.conf.j2", "staticd.conf.j2"]


class TestResolvedStackFrozen:
    def test_frozen(self):
        r = resolve_stack("ospf", [])
        with pytest.raises(AttributeError):
            r.image = "something"  # type: ignore


class TestSidValidation:
    def test_sid_indices_within_srgb_ok(self):
        validate_sid_indices(resolve_stack("isis", ["sr"]), {"space-sat-p00s00": 1})

    def test_segment_routing_requires_resolved_sid_indices(self):
        with pytest.raises(ValueError, match="requires resolved SID"):
            validate_sid_indices(resolve_stack("isis", ["sr"]), {})

    def test_sid_indices_must_fit_srgb(self):
        with pytest.raises(ValueError, match="exceeds SRGB"):
            validate_sid_indices(resolve_stack("isis", ["sr"]), {"space-sat-p00s00": 8001})

    def test_non_sr_stack_ignores_sid_indices(self):
        validate_sid_indices(resolve_stack("isis", []), {})


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
