# Copyright 2024-2026 .chance (dotchance)
# SPDX-License-Identifier: AGPL-3.0-only
# Additional terms under AGPL-3.0 Section 7 are in the NOTICE file.
"""NodalArc Operator — manages ConstellationSpec CRD lifecycle.

When imported by kopf (via -m flag), this module registers all handlers.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from nodal.logging import configure as _configure_logging

_configure_logging("nodal.arc.operator", nats_level=logging.INFO)
logging.getLogger("kopf.objects").setLevel(logging.WARNING)
logging.getLogger("aiohttp.access").setLevel(logging.WARNING)
logging.getLogger(__name__).info(
    "Operator starting [build=%s]", os.environ.get("NODAL_BUILD", "dev")
)

from nodalarc.platform_config import init_platform_config

_platform_path = Path("/etc/nodalarc/platform.yaml")
if _platform_path.exists():
    init_platform_config(_platform_path)

import nodalarc_operator.handlers  # noqa: E402, F401 — registers kopf handlers
