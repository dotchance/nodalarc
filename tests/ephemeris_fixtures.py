# Copyright 2024-2026 .chance (dotchance)
# SPDX-License-Identifier: AGPL-3.0-only
"""Real filesystem conditions that make the platform's ephemeris data unreadable.

Each stage builds a copy of the data directory in which reading genuinely fails
for the test user, then points the physics module at that directory. The
failures are the operating system's own; nothing in the read path is replaced.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from nodalarc.physics import EphemerisLocation
from nodalarc.physics import context as physics_context
from nodalarc.physics.datasets import production_location

DATA_DENIAL_STAGES = ("stat", "open")
DENIED_FILE = "de440s.bsp"


def deny_data_file_access(
    request: pytest.FixtureRequest,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    stage: str,
) -> Path:
    """Make the planetary positions file unreadable at one access stage.

    ``stat``: the data directory cannot be searched, so looking up the file
    fails. ``open``: the file exists but its permissions deny reading. Returns
    the host directory, which a refusal must not name.
    """
    real = production_location()
    source = tmp_path / "ephemerides"
    source.mkdir()
    for item in real.source_dir.iterdir():
        if item.is_file() and item.name != DENIED_FILE:
            (source / item.name).symlink_to(item)
    denied = source / DENIED_FILE
    denied.write_bytes(b"unreadable for this test")

    def restore() -> None:
        source.chmod(0o700)
        denied.chmod(0o600)

    request.addfinalizer(restore)
    if stage == "open":
        denied.chmod(0o000)
    elif stage == "stat":
        source.chmod(0o600)
    else:
        raise ValueError(f"unknown data denial stage {stage!r}")
    injected = EphemerisLocation(source_dir=source, converted_dir=real.converted_dir)
    monkeypatch.setattr(physics_context, "production_location", lambda: injected)
    return source
