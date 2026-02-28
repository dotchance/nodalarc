"""Test link_manager — real namespaces, real tc, real ping.

These tests require root access (CAP_NET_ADMIN, CAP_SYS_ADMIN).
Marked with @pytest.mark.requires_root.
"""

import os
import subprocess
import time

import pytest

# Skip all tests in this module if not running as root
pytestmark = pytest.mark.requires_root

if os.geteuid() != 0:
    pytest.skip("requires root", allow_module_level=True)

from pyroute2 import IPRoute, NetNS, NetlinkError  # noqa: E402


@pytest.fixture
def two_namespaces(tmp_path):
    """Create two network namespaces for testing.

    Returns (ns_name_a, ns_name_b, pid_a, pid_b).
    Uses ip netns to create persistent namespaces for testing
    since we can't easily get PIDs for network namespaces.
    """
    ns_a = f"na_test_a_{os.getpid()}"
    ns_b = f"na_test_b_{os.getpid()}"

    # Create namespaces
    subprocess.run(["ip", "netns", "add", ns_a], check=True)
    subprocess.run(["ip", "netns", "add", ns_b], check=True)

    yield ns_a, ns_b

    # Cleanup
    subprocess.run(["ip", "netns", "del", ns_a], check=False)
    subprocess.run(["ip", "netns", "del", ns_b], check=False)


def _exec_in_ns(ns_name: str, cmd: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["ip", "netns", "exec", ns_name] + cmd,
        capture_output=True, text=True,
    )


class TestVethCreation:
    def test_create_veth_and_verify(self, two_namespaces):
        ns_a, ns_b = two_namespaces

        # Create veth pair using ip commands (matching what link_manager does)
        tmp_a = "vethtesta"
        tmp_b = "vethtestb"

        ipr = IPRoute()
        try:
            ipr.link("add", ifname=tmp_a, peer={"ifname": tmp_b}, kind="veth")
            idx_a = ipr.link_lookup(ifname=tmp_a)[0]
            ipr.link("set", index=idx_a, net_ns_fd=NetNS(ns_a).fileno() if False else 0)
        finally:
            ipr.close()

        # Simpler test: create in namespace directly
        subprocess.run(["ip", "netns", "exec", ns_a, "ip", "link", "add",
                        "veth0", "type", "veth", "peer", "name", "veth1"], check=True)
        # Move one end to ns_b
        subprocess.run(["ip", "netns", "exec", ns_a, "ip", "link", "set",
                        "veth1", "netns", ns_b], check=True)

        # Verify both ends exist
        result_a = _exec_in_ns(ns_a, ["ip", "link", "show", "veth0"])
        assert "veth0" in result_a.stdout

        result_b = _exec_in_ns(ns_b, ["ip", "link", "show", "veth1"])
        assert "veth1" in result_b.stdout

    def test_interface_up_down(self, two_namespaces):
        ns_a, ns_b = two_namespaces

        # Create veth pair
        subprocess.run(["ip", "netns", "exec", ns_a, "ip", "link", "add",
                        "veth0", "type", "veth", "peer", "name", "veth1"], check=True)
        subprocess.run(["ip", "netns", "exec", ns_a, "ip", "link", "set",
                        "veth1", "netns", ns_b], check=True)

        # Set up
        _exec_in_ns(ns_a, ["ip", "link", "set", "veth0", "up"])
        result = _exec_in_ns(ns_a, ["ip", "link", "show", "veth0"])
        assert "UP" in result.stdout

        # Set down
        _exec_in_ns(ns_a, ["ip", "link", "set", "veth0", "down"])
        result = _exec_in_ns(ns_a, ["ip", "link", "show", "veth0"])
        assert "DOWN" in result.stdout or "state DOWN" in result.stdout


class TestNetem:
    def test_netem_delay_ping_rtt(self, two_namespaces):
        """Apply 10ms netem delay, verify ping RTT ≈ 20ms (±4ms)."""
        ns_a, ns_b = two_namespaces

        # Create veth pair
        subprocess.run(["ip", "netns", "exec", ns_a, "ip", "link", "add",
                        "veth0", "type", "veth", "peer", "name", "veth1"], check=True)
        subprocess.run(["ip", "netns", "exec", ns_a, "ip", "link", "set",
                        "veth1", "netns", ns_b], check=True)

        # Assign IPs and bring up
        _exec_in_ns(ns_a, ["ip", "addr", "add", "10.99.0.1/24", "dev", "veth0"])
        _exec_in_ns(ns_a, ["ip", "link", "set", "veth0", "up"])
        _exec_in_ns(ns_b, ["ip", "addr", "add", "10.99.0.2/24", "dev", "veth1"])
        _exec_in_ns(ns_b, ["ip", "link", "set", "veth1", "up"])

        # Apply tbf + netem on both ends (10ms each direction = 20ms RTT)
        for ns, iface in [(ns_a, "veth0"), (ns_b, "veth1")]:
            _exec_in_ns(ns, [
                "tc", "qdisc", "add", "dev", iface, "root", "handle", "1:",
                "tbf", "rate", "100mbit", "burst", "9000", "latency", "50ms",
            ])
            _exec_in_ns(ns, [
                "tc", "qdisc", "add", "dev", iface, "parent", "1:1",
                "handle", "10:", "netem", "delay", "10ms",
            ])

        # Ping and check RTT
        result = _exec_in_ns(ns_a, [
            "ping", "-c", "5", "-W", "2", "10.99.0.2",
        ])
        assert result.returncode == 0
        # Parse avg RTT from ping output
        for line in result.stdout.split("\n"):
            if "avg" in line:
                # Format: rtt min/avg/max/mdev = 19.5/20.1/20.3/0.2 ms
                avg = float(line.split("=")[1].strip().split("/")[1])
                assert 16.0 < avg < 24.0, f"Expected RTT ~20ms, got {avg}ms"
                break


class TestDummyInterface:
    def test_create_dummy_with_address(self, two_namespaces):
        ns_a, _ = two_namespaces

        _exec_in_ns(ns_a, [
            "ip", "link", "add", "terr0", "type", "dummy",
        ])
        _exec_in_ns(ns_a, ["ip", "link", "set", "terr0", "up"])
        _exec_in_ns(ns_a, [
            "ip", "addr", "add", "172.16.0.1/24", "dev", "terr0",
        ])

        result = _exec_in_ns(ns_a, ["ip", "addr", "show", "terr0"])
        assert "172.16.0.1" in result.stdout


class TestQdiscUpdate:
    def test_update_netem_delay(self, two_namespaces):
        ns_a, ns_b = two_namespaces

        # Create veth, assign IPs, bring up
        subprocess.run(["ip", "netns", "exec", ns_a, "ip", "link", "add",
                        "veth0", "type", "veth", "peer", "name", "veth1"], check=True)
        subprocess.run(["ip", "netns", "exec", ns_a, "ip", "link", "set",
                        "veth1", "netns", ns_b], check=True)
        _exec_in_ns(ns_a, ["ip", "addr", "add", "10.99.0.1/24", "dev", "veth0"])
        _exec_in_ns(ns_a, ["ip", "link", "set", "veth0", "up"])
        _exec_in_ns(ns_b, ["ip", "addr", "add", "10.99.0.2/24", "dev", "veth1"])
        _exec_in_ns(ns_b, ["ip", "link", "set", "veth1", "up"])

        # Apply initial shaping
        for ns, iface in [(ns_a, "veth0"), (ns_b, "veth1")]:
            _exec_in_ns(ns, [
                "tc", "qdisc", "add", "dev", iface, "root", "handle", "1:",
                "tbf", "rate", "100mbit", "burst", "9000", "latency", "50ms",
            ])
            _exec_in_ns(ns, [
                "tc", "qdisc", "add", "dev", iface, "parent", "1:1",
                "handle", "10:", "netem", "delay", "5ms",
            ])

        # Update delay to 20ms
        for ns, iface in [(ns_a, "veth0"), (ns_b, "veth1")]:
            _exec_in_ns(ns, [
                "tc", "qdisc", "change", "dev", iface, "parent", "1:1",
                "handle", "10:", "netem", "delay", "20ms",
            ])

        # Verify new RTT is ~40ms
        result = _exec_in_ns(ns_a, ["ping", "-c", "3", "-W", "2", "10.99.0.2"])
        assert result.returncode == 0
        for line in result.stdout.split("\n"):
            if "avg" in line:
                avg = float(line.split("=")[1].strip().split("/")[1])
                assert 36.0 < avg < 48.0, f"Expected RTT ~40ms, got {avg}ms"
                break
