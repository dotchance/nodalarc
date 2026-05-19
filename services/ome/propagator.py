# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""OME propagator — thin re-export from shared library.

All propagation math lives in lib/nodalarc/propagator.py so it can be
imported by any component (Scheduler, VS-API) without cross-service
dependencies. This module re-exports everything for backward compatibility
with existing ``from ome.propagator import X`` imports.
"""

from nodalarc.propagator import *  # noqa: F401, F403
