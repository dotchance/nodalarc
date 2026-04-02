"""NodalArc Operator — manages ConstellationSpec CRD lifecycle.

When imported by kopf (via -m flag), this module registers all handlers.
"""

from __future__ import annotations

import logging
from pathlib import Path

from nodalarc.constants import LOG_FORMAT
from nodalarc.platform import init_platform_config

logging.basicConfig(format=LOG_FORMAT, level=logging.INFO)

_platform_path = Path("/etc/nodalarc/platform.yaml")
if _platform_path.exists():
    init_platform_config(_platform_path)

import nodalarc_operator.handlers  # noqa: E402, F401 — registers kopf handlers
