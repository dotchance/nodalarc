"""PodLocationMap public API contracts."""

from __future__ import annotations

import pytest
from scheduler.pod_locator import PodLocationMap


def test_an_unknown_node_or_node_ip_is_refused() -> None:
    from scheduler.pod_locator import PodLocationError

    loc = PodLocationMap()
    loc._node_of["sat-a"] = "node01"
    loc._agent_addrs["node01"] = "node01"
    loc._node_ips["node01"] = "192.168.10.201"

    assert (loc.k3s_node("sat-a"), loc.agent_addr("sat-a"), loc.node_ip("node01")) == (
        "node01",
        "node01",
        "192.168.10.201",
    )
    with pytest.raises(PodLocationError, match="no pod location for node sat-ghost"):
        loc.k3s_node("sat-ghost")
    with pytest.raises(PodLocationError, match="no pod location for node sat-ghost"):
        loc.agent_addr("sat-ghost")
    with pytest.raises(PodLocationError, match="no pod location for node sat-ghost"):
        loc.link_locality("sat-a", "sat-ghost")
    with pytest.raises(PodLocationError, match="no InternalIP for Kubernetes node node09"):
        loc.node_ip("node09")
