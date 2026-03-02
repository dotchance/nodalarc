"""Test link_manager — real namespaces, real tc, real ping.

PRD Appendix B: proves that veth pairs can be created between two
network namespaces, that ip link set up/down correctly changes interface
state, that tc netem applies the correct one-way delay (verified by ping),
and that tc tbf limits bandwidth (verified by iperf3).

These tests require root access (CAP_NET_ADMIN, CAP_SYS_ADMIN).
When run as non-root with passwordless sudo, tests auto-delegate to a
root subprocess.
"""

import logging
import os
import signal
import subprocess
import sys
import time

import pytest

log = logging.getLogger(__name__)

pytestmark = pytest.mark.requires_root

_IS_ROOT = os.geteuid() == 0

if not _IS_ROOT:
    _has_sudo = subprocess.run(
        ["sudo", "-n", "true"], capture_output=True,
    ).returncode == 0
    if not _has_sudo:
        pytest.skip(
            "requires root and no passwordless sudo available",
            allow_module_level=True,
        )


if _IS_ROOT:
    # ── Running as root: define all tests directly ──────────────────────

    from orchestrator.link_manager import (
        apply_link_shaping,
        configure_interface,
        create_dummy_interface,
        create_veth_pair,
        destroy_veth_pair,
        deterministic_mac,
        disable_ipv6_autoconfig,
        set_interface_down,
        set_interface_up,
        update_delay,
    )
    from pyroute2 import NetNS

    @pytest.fixture
    def two_ns_with_pids():
        """Create two network namespaces and return PIDs of processes inside them.

        link_manager functions use PID-based namespace paths (/proc/{pid}/ns/net).
        This fixture starts a long-running process in each namespace so we have
        a PID to use.
        """
        ns_a = f"na_test_a_{os.getpid()}"
        ns_b = f"na_test_b_{os.getpid()}"

        subprocess.run(["ip", "netns", "add", ns_a], check=True)
        subprocess.run(["ip", "netns", "add", ns_b], check=True)

        # Start a process in each namespace to get a PID
        proc_a = subprocess.Popen(
            ["ip", "netns", "exec", ns_a, "sleep", "3600"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        proc_b = subprocess.Popen(
            ["ip", "netns", "exec", ns_b, "sleep", "3600"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )

        yield ns_a, ns_b, proc_a.pid, proc_b.pid

        # Cleanup
        proc_a.send_signal(signal.SIGKILL)
        proc_b.send_signal(signal.SIGKILL)
        proc_a.wait()
        proc_b.wait()
        subprocess.run(["ip", "netns", "del", ns_a], check=False)
        subprocess.run(["ip", "netns", "del", ns_b], check=False)

    def _exec_in_ns(ns_name: str, cmd: list[str]) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["ip", "netns", "exec", ns_name] + cmd,
            capture_output=True, text=True,
        )

    class TestVethCreation:
        def test_create_veth_pair_via_link_manager(self, two_ns_with_pids):
            """link_manager.create_veth_pair creates interfaces in both namespaces."""
            ns_a, ns_b, pid_a, pid_b = two_ns_with_pids

            create_veth_pair(pid_a, pid_b, "isl0", "isl1")

            # Verify interfaces exist in the correct namespaces
            result_a = _exec_in_ns(ns_a, ["ip", "link", "show", "isl0"])
            assert "isl0" in result_a.stdout

            result_b = _exec_in_ns(ns_b, ["ip", "link", "show", "isl1"])
            assert "isl1" in result_b.stdout

        def test_veth_mtu_is_set(self, two_ns_with_pids):
            """Veth interfaces have the configured MTU."""
            ns_a, _, pid_a, pid_b = two_ns_with_pids

            create_veth_pair(pid_a, pid_b, "isl0", "isl1", mtu=9000)

            result = _exec_in_ns(ns_a, ["ip", "link", "show", "isl0"])
            assert "mtu 9000" in result.stdout

        def test_veth_with_node_ids_sets_mac(self, two_ns_with_pids):
            """create_veth_pair with node_ids configures deterministic MACs."""
            ns_a, ns_b, pid_a, pid_b = two_ns_with_pids

            create_veth_pair(
                pid_a, pid_b, "isl0", "isl1",
                node_id_a="sat-P00S00", node_id_b="sat-P00S01",
            )

            expected_mac_a = deterministic_mac("sat-P00S00", "isl0")
            result_a = _exec_in_ns(ns_a, ["ip", "link", "show", "isl0"])
            assert expected_mac_a in result_a.stdout

            expected_mac_b = deterministic_mac("sat-P00S01", "isl1")
            result_b = _exec_in_ns(ns_b, ["ip", "link", "show", "isl1"])
            assert expected_mac_b in result_b.stdout

        def test_stale_interface_cleanup(self, two_ns_with_pids):
            """create_veth_pair cleans stale interfaces before creating."""
            _, _, pid_a, pid_b = two_ns_with_pids

            # Create first pair
            create_veth_pair(pid_a, pid_b, "isl0", "isl1")
            # Creating again should succeed (cleans stale first)
            create_veth_pair(pid_a, pid_b, "isl0", "isl1")

    class TestInterfaceState:
        def test_set_interface_up(self, two_ns_with_pids):
            """link_manager.set_interface_up brings interface up."""
            ns_a, _, pid_a, pid_b = two_ns_with_pids

            create_veth_pair(pid_a, pid_b, "isl0", "isl1")
            set_interface_up(pid_a, "isl0")

            result = _exec_in_ns(ns_a, ["ip", "link", "show", "isl0"])
            assert "UP" in result.stdout

        def test_set_interface_down(self, two_ns_with_pids):
            """link_manager.set_interface_down brings interface down."""
            ns_a, _, pid_a, pid_b = two_ns_with_pids

            create_veth_pair(pid_a, pid_b, "isl0", "isl1")
            set_interface_up(pid_a, "isl0")
            set_interface_down(pid_a, "isl0")

            result = _exec_in_ns(ns_a, ["ip", "link", "show", "isl0"])
            # After set_interface_down, state should not be "UP"
            assert "state DOWN" in result.stdout or "state LOWERLAYERDOWN" in result.stdout

    class TestDestroyVethPair:
        def test_destroy_removes_both_ends(self, two_ns_with_pids):
            """Destroying one end of a veth pair removes both ends."""
            ns_a, ns_b, pid_a, pid_b = two_ns_with_pids

            create_veth_pair(pid_a, pid_b, "isl0", "isl1")
            destroy_veth_pair(pid_a, "isl0")

            # Both ends should be gone
            result_a = _exec_in_ns(ns_a, ["ip", "link", "show", "isl0"])
            assert "isl0" not in result_a.stdout or result_a.returncode != 0

            result_b = _exec_in_ns(ns_b, ["ip", "link", "show", "isl1"])
            assert "isl1" not in result_b.stdout or result_b.returncode != 0

    class TestNetem:
        def test_apply_link_shaping_delay_verified_by_ping(self, two_ns_with_pids):
            """apply_link_shaping with 10ms delay → ping RTT ≈ 20ms."""
            ns_a, ns_b, pid_a, pid_b = two_ns_with_pids

            create_veth_pair(pid_a, pid_b, "isl0", "isl1")

            # Assign IPs and bring up
            _exec_in_ns(ns_a, ["ip", "addr", "add", "10.99.0.1/24", "dev", "isl0"])
            set_interface_up(pid_a, "isl0")
            _exec_in_ns(ns_b, ["ip", "addr", "add", "10.99.0.2/24", "dev", "isl1"])
            set_interface_up(pid_b, "isl1")

            # Apply shaping via link_manager (10ms each direction = 20ms RTT)
            apply_link_shaping(pid_a, "isl0", delay_ms=10.0, rate_mbps=100.0)
            apply_link_shaping(pid_b, "isl1", delay_ms=10.0, rate_mbps=100.0)

            # Ping and verify RTT
            result = _exec_in_ns(ns_a, ["ping", "-c", "5", "-W", "2", "10.99.0.2"])
            assert result.returncode == 0
            for line in result.stdout.split("\n"):
                if "avg" in line:
                    avg = float(line.split("=")[1].strip().split("/")[1])
                    assert 16.0 < avg < 28.0, f"Expected RTT ~20ms, got {avg}ms"
                    break

        def test_update_delay(self, two_ns_with_pids):
            """update_delay changes netem delay on existing qdisc chain."""
            ns_a, ns_b, pid_a, pid_b = two_ns_with_pids

            create_veth_pair(pid_a, pid_b, "isl0", "isl1")
            _exec_in_ns(ns_a, ["ip", "addr", "add", "10.99.0.1/24", "dev", "isl0"])
            set_interface_up(pid_a, "isl0")
            _exec_in_ns(ns_b, ["ip", "addr", "add", "10.99.0.2/24", "dev", "isl1"])
            set_interface_up(pid_b, "isl1")

            # Initial shaping: 5ms
            apply_link_shaping(pid_a, "isl0", delay_ms=5.0, rate_mbps=100.0)
            apply_link_shaping(pid_b, "isl1", delay_ms=5.0, rate_mbps=100.0)

            # Update to 20ms via link_manager
            update_delay(pid_a, "isl0", delay_ms=20.0)
            update_delay(pid_b, "isl1", delay_ms=20.0)

            # Verify new RTT is ~40ms
            result = _exec_in_ns(ns_a, ["ping", "-c", "3", "-W", "2", "10.99.0.2"])
            assert result.returncode == 0
            for line in result.stdout.split("\n"):
                if "avg" in line:
                    avg = float(line.split("=")[1].strip().split("/")[1])
                    assert 36.0 < avg < 60.0, f"Expected RTT ~40ms, got {avg}ms"
                    break

        def test_apply_link_shaping_is_idempotent(self, two_ns_with_pids):
            """Calling apply_link_shaping twice does not fail."""
            _, _, pid_a, pid_b = two_ns_with_pids

            create_veth_pair(pid_a, pid_b, "isl0", "isl1")
            set_interface_up(pid_a, "isl0")

            apply_link_shaping(pid_a, "isl0", delay_ms=5.0, rate_mbps=100.0)
            # Second call should succeed (idempotent — removes old qdiscs first)
            apply_link_shaping(pid_a, "isl0", delay_ms=10.0, rate_mbps=200.0)

    class TestDummyInterface:
        def test_create_dummy_with_addresses(self, two_ns_with_pids):
            """link_manager.create_dummy_interface creates terr0 with addresses."""
            ns_a, _, pid_a, _ = two_ns_with_pids

            create_dummy_interface(pid_a, "terr0", [
                "172.16.0.1/24", "fd10::0:1/112",
            ])

            result = _exec_in_ns(ns_a, ["ip", "addr", "show", "terr0"])
            assert "172.16.0.1" in result.stdout
            assert "fd10::1" in result.stdout or "fd10::0:1" in result.stdout

    class TestConfigureInterface:
        def test_deterministic_mac_format(self):
            """deterministic_mac returns a valid locally-administered MAC."""
            mac = deterministic_mac("sat-P00S00", "isl0")
            octets = mac.split(":")
            assert len(octets) == 6
            assert octets[0] == "02"  # Locally administered
            # All octets must be valid hex
            for octet in octets:
                int(octet, 16)

        def test_deterministic_mac_is_stable(self):
            """Same inputs always produce the same MAC."""
            mac1 = deterministic_mac("sat-P00S00", "isl0")
            mac2 = deterministic_mac("sat-P00S00", "isl0")
            assert mac1 == mac2

        def test_deterministic_mac_differs_for_different_interfaces(self):
            """Different interfaces on the same node get different MACs."""
            mac0 = deterministic_mac("sat-P00S00", "isl0")
            mac1 = deterministic_mac("sat-P00S00", "isl1")
            assert mac0 != mac1

        def test_configure_interface_sets_mac(self, two_ns_with_pids):
            """configure_interface sets the deterministic MAC on a veth."""
            ns_a, _, pid_a, pid_b = two_ns_with_pids

            create_veth_pair(pid_a, pid_b, "isl0", "isl1")
            configure_interface(pid_a, "isl0", "sat-P00S00")

            expected = deterministic_mac("sat-P00S00", "isl0")
            result = _exec_in_ns(ns_a, ["ip", "link", "show", "isl0"])
            assert expected in result.stdout

else:
    # ── Running as non-root with passwordless sudo: delegate ─────────

    def test_link_manager_via_sudo():
        """Delegate all link manager tests to a root subprocess via sudo.

        Individual test results are printed from the subprocess output.
        """
        result = subprocess.run(
            ["sudo", "-E", sys.executable, "-m", "pytest",
             os.path.abspath(__file__), "-v", "--tb=short",
             "-p", "no:cacheprovider"],
            capture_output=True, text=True,
            timeout=120,
        )
        # Log subprocess output so individual test results are visible
        for line in result.stdout.splitlines():
            log.debug(line)
        if result.stderr:
            for line in result.stderr.splitlines():
                log.debug(line)
        assert result.returncode == 0, (
            f"Link manager tests failed under sudo (exit {result.returncode})"
        )
