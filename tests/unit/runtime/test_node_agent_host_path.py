# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""The host network carries every emulated packet whole inside VXLAN.

Emulated interfaces keep the platform MTU wherever their pods run, so the
hosts carry the encapsulation. Before a wiring attempt creates anything, the
Node Agent proves each host path its session traffic can take with
unfragmentable packets of the full encapsulated size, and refuses the attempt
when one is not carried.
"""

from __future__ import annotations

import pytest
from nodalarc.vxlan import host_path_mtu_for


def test_the_host_path_carries_the_encapsulation_for_its_address_family() -> None:
    assert host_path_mtu_for(9000, "192.0.2.3") == 9050
    assert host_path_mtu_for(9000, "2001:db8::3") == 9070


@pytest.mark.parametrize("mtu", [1279, 9001])
def test_the_platform_refuses_an_emulated_mtu_outside_ipv6_minimum_and_9000(mtu) -> None:
    from nodalarc.platform_config import PlatformConfig
    from pydantic import ValidationError

    from tests.unit.runtime.test_platform_config import _valid_config_dict

    with pytest.raises(ValidationError, match="veth_interface_mtu_bytes"):
        PlatformConfig.model_validate({**_valid_config_dict(), "veth_interface_mtu_bytes": mtu})
