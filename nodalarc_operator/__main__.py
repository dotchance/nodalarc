"""Operator entry point — runs kopf event loop."""

from __future__ import annotations

import logging
from pathlib import Path

from nodalarc.constants import LOG_FORMAT
from nodalarc.platform import init_platform_config

logging.basicConfig(format=LOG_FORMAT, level=logging.INFO)

init_platform_config(Path("/etc/nodalarc/platform.yaml"))

import nodalarc_operator.handlers  # noqa: E402, F401 — registers kopf handlers
