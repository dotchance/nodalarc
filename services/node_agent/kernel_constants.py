# Copyright 2024-2026 .chance (dotchance)
# Licensed under the NodalArc Source Available License 1.0. See LICENSE file.
"""Named Linux kernel constants used by Node Agent proof code."""

IFF_UP = 0x1
TC_H_INGRESS = 0xFFFF0000
TBF_RATE32_MAX_BPS = 0xFFFFFFFF

# Netem delay is configured in microseconds but reported back by pyroute2 as
# tc scheduler ticks. A one-tick tolerance covers integer conversion rounding;
# larger drift means the kernel state no longer matches the command.
NETEM_TICK_TOLERANCE = 1
