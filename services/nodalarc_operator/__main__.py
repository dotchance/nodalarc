# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""Operator entry point — runs kopf event loop.

configure() is called by __init__.py (triggered by the handlers import).
Do NOT call it here — double-configure destroys the first NatsHandler's
deque, losing any records buffered between the two calls.
"""

from __future__ import annotations

from pathlib import Path

from nodalarc.platform_config import init_platform_config

init_platform_config(Path("/etc/nodalarc/platform.yaml"))

import nodalarc_operator.handlers  # noqa: E402, F401 — registers kopf handlers
