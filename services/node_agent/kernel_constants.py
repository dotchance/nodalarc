# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""Named Linux kernel constants used by Node Agent proof code."""

IFF_UP = 0x1

# The shaping hierarchy on an interface's egress: HTB root 1:, its rate class
# 1:1, and on the transmitting side a netem child 10: under that class.
SHAPER_ROOT_HANDLE = 0x00010000
SHAPER_CLASS_HANDLE = 0x00010001
NETEM_HANDLE = 0x00100000
# The HTB root's default class: the minor of 1:1, so traffic no filter
# classifies (all of it; the shaper installs no filters) takes the rate class.
SHAPER_DEFAULT_CLASS = 0x1

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
