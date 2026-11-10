# Copyright 2024-2026 .chance (dotchance)
# SPDX-License-Identifier: AGPL-3.0-only
"""Convert the ephemeris frame kernels for ANISE: ``python -m nodalarc.physics [dir]``.

Images run this at build time after copying ``configs/ephemerides/``.
"""

from __future__ import annotations

import sys
from pathlib import Path

from nodalarc.physics.datasets import EphemerisLocation, convert_datasets, production_location


def main(argv: list[str]) -> int:
    if len(argv) not in (1, 2):
        print("usage: python -m nodalarc.physics [ephemeris_dir]", file=sys.stderr)
        return 2
    if len(argv) == 2:
        source = Path(argv[1]).resolve()
        location = EphemerisLocation(source_dir=source, converted_dir=source / "anise")
    else:
        location = production_location()
    convert_datasets(location)
    return 0


raise SystemExit(main(sys.argv))
