"""NodalArc Operator — manages ConstellationSpec CRD lifecycle.

When imported by kopf (via -m flag), this module registers all handlers.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from nodal.logging import configure as _configure_logging

_configure_logging("nodal.arc.operator", nats_level=logging.INFO)
logging.getLogger(__name__).info(
    "Operator starting [build=%s]", os.environ.get("NODAL_BUILD", "dev")
)

from nodalarc.platform_config import init_platform_config

_platform_path = Path("/etc/nodalarc/platform.yaml")
if _platform_path.exists():
    init_platform_config(_platform_path)

import nodalarc_operator.handlers  # noqa: E402, F401 — registers kopf handlers
