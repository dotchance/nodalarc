# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""FRR image contract: the FRR adapter's ``daemons`` file is the only daemon
selection. The image ships no daemons file of its own, edits none, and
refuses to start FRR when the mounted one is missing.
"""

from __future__ import annotations

from pathlib import Path

IMAGE_DIR = Path(__file__).resolve().parents[2] / "images" / "frr"
ENTRYPOINT = IMAGE_DIR / "entrypoint.sh"


def _code_lines(text: str) -> list[str]:
    return [
        line for line in text.splitlines() if line.strip() and not line.lstrip().startswith("#")
    ]


def test_image_ships_and_edits_no_daemon_selection() -> None:
    assert not (IMAGE_DIR / "daemons").exists()
    dockerfile = _code_lines((IMAGE_DIR / "Dockerfile").read_text())
    assert not any("daemons" in line for line in dockerfile if line.startswith("COPY"))
    entrypoint = _code_lines(ENTRYPOINT.read_text())
    assert not any("sed" in line and "daemons" in line for line in entrypoint)


def test_entrypoint_refuses_to_start_frr_without_the_adapter_daemons_file() -> None:
    code = _code_lines(ENTRYPOINT.read_text())

    check = next((i for i, line in enumerate(code) if "! -f /etc/frr-config/daemons" in line), None)
    handoff = next(i for i, line in enumerate(code) if "exec /usr/lib/frr/docker-start" in line)
    assert check is not None, "entrypoint must check for the mounted daemons file"
    assert check < handoff
    refusal = code[check : check + 4]
    assert any("ERROR" in line for line in refusal)
    assert any(line.strip() == "exit 1" for line in refusal)
