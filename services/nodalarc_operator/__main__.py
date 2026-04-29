# Copyright 2024-2026 .chance (dotchance)
# Licensed under the NodalArc Source Available License 1.0. See LICENSE file.
"""Operator entry point — runs kopf event loop."""

from __future__ import annotations

import logging
from pathlib import Path

from nodal.logging import configure as _configure_logging

_configure_logging("nodal.arc.operator", nats_level=logging.INFO)

from nodalarc.platform_config import init_platform_config

init_platform_config(Path("/etc/nodalarc/platform.yaml"))

import nodalarc_operator.handlers  # noqa: E402, F401 — registers kopf handlers
