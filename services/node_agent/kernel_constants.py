# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""Named Linux kernel constants used by Node Agent proof code."""

IFF_UP = 0x1
TBF_RATE32_MAX_BPS = 0xFFFFFFFF

# Netem delay is configured in microseconds but reported back by pyroute2 as
# tc scheduler ticks. A one-tick tolerance covers integer conversion rounding;
# larger drift means the kernel state no longer matches the command.
NETEM_TICK_TOLERANCE = 1

# The per-device switch that lets the kernel accept labeled packets on a
# pod interface. Written by the operation that creates the interface and
# read back by the verifier; both name it through this one function.
MPLS_INPUT_ENABLED = "1"


def mpls_input_sysctl(ifname: str) -> str:
    return f"net.mpls.conf.{ifname}.input"
