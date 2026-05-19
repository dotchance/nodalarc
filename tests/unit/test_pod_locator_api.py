"""PodLocationMap public API contracts."""

from __future__ import annotations

import inspect

from scheduler.pod_locator import PodLocationMap


def test_pod_location_loaders_do_not_expose_legacy_agent_port() -> None:
    for method_name in ("load_from_pid_map_file", "load_from_k8s_api"):
        params = inspect.signature(getattr(PodLocationMap, method_name)).parameters
        assert "agent_port" not in params
        assert "_agent_port" not in params
