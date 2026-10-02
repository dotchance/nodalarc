"""Runs that happen only when asked for with -m."""

from __future__ import annotations

import pytest

# catalog replaces the cluster's session a dozen times. resilience damages the running session
# on purpose. platform tears the whole platform down and installs it again.
_ON_REQUEST = ("catalog", "resilience", "platform")


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    selected = config.getoption("-m") or ""
    for marker in _ON_REQUEST:
        if marker in selected:
            continue
        for item in items:
            if item.get_closest_marker(marker):
                item.add_marker(pytest.mark.skip(reason=f"this run is selected with -m {marker}"))
