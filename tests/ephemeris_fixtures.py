"""Filesystem denial injection for ephemeris kernel access."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

KERNEL_DENIAL_STAGES = ("resolve", "open")


def deny_kernel_access(monkeypatch: pytest.MonkeyPatch, stage: str) -> tuple[str, str]:
    """Make every ``.bsp`` path fail at one access stage.

    Returns the owner's diagnostic fragment and the injected error text, both
    of which the typed failure must carry.
    """

    if stage == "resolve":
        real_resolve = Path.resolve
        injected = "injected kernel path traversal denial"

        def denied_resolve(self: Path, *args: Any, **kwargs: Any):
            if self.suffix == ".bsp":
                raise PermissionError(injected)
            return real_resolve(self, *args, **kwargs)

        monkeypatch.setattr(Path, "resolve", denied_resolve)
        return "ephemeris kernel path could not be resolved", injected
    if stage == "open":
        real_open = Path.open
        injected = "injected BSP read denial"

        def denied_open(self: Path, *args: Any, **kwargs: Any):
            if self.suffix == ".bsp":
                raise PermissionError(injected)
            return real_open(self, *args, **kwargs)

        monkeypatch.setattr(Path, "open", denied_open)
        return "ephemeris kernel file could not be read", injected
    raise ValueError(f"unknown kernel denial stage {stage!r}")
