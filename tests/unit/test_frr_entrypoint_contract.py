# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""FRR entrypoint config-lifecycle contract.

The session pod owns its config lifecycle: the entrypoint's watcher must
converge the RUNNING daemons to the ConfigMap's intended config, and the
readiness sentinel may only move when that convergence actually happened.
Three failure classes are pinned here:

1. Additive reload theater — ``vtysh -f`` only sources commands, so config
   REMOVED in a new version silently survives in the daemons (a reused pod
   keeps ``isis passive`` after the render drops it). Reload must be the
   declarative ``frr-reload.py`` diff, which applies removals.
2. A lying sentinel — copying ``.config_version`` unconditionally makes the
   readiness probe report "converged" for a pod whose daemons still run the
   old config. The sentinel copy must be gated on reload success.
3. An image that overrides the adapter — the FRR adapter's ``daemons`` file
   is the only daemon selection. The image ships no daemons file of its own,
   edits none, and refuses to start FRR when the mounted one is missing.
"""

from __future__ import annotations

import re
from pathlib import Path

IMAGE_DIR = Path(__file__).resolve().parents[2] / "images" / "frr"
ENTRYPOINT = IMAGE_DIR / "entrypoint.sh"


def _watcher_code_lines() -> list[str]:
    """The watcher function body as code lines, comments stripped."""
    text = ENTRYPOINT.read_text()
    match = re.search(r"_watch_config\(\)\s*\{(.*?)\n\}", text, re.DOTALL)
    assert match, "entrypoint must define the _watch_config watcher"
    return [
        line
        for line in match.group(1).splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]


def test_watcher_reload_is_declarative_not_additive() -> None:
    code = _watcher_code_lines()
    assert any("frr-reload.py" in line for line in code), (
        "config watcher must use FRR's declarative reload (frr-reload.py), "
        "which applies removals as well as additions"
    )
    assert not any("vtysh -f" in line for line in code), (
        "vtysh -f is additive-only: config removed from the new version "
        "silently survives in the running daemons"
    )
    assert not any("|| true" in line for line in code), (
        "the watcher must not swallow reload failures; a pod that cannot "
        "converge must be visibly unready, not silently healthy"
    )


def test_sentinel_only_moves_on_reload_success() -> None:
    code = _watcher_code_lines()

    reload_if = next(
        (i for i, line in enumerate(code) if re.search(r"\bif\b.*frr-reload\.py", line)),
        None,
    )
    assert reload_if is not None, "reload must be an if-gated command, not fire-and-forget"

    sentinel_writes = [
        i for i, line in enumerate(code) if re.search(r"cp\b.*\.config_version", line)
    ]
    assert sentinel_writes, "the watcher must maintain the readiness sentinel"

    else_line = next(
        (i for i, line in enumerate(code) if i > reload_if and re.match(r"\s*else\b", line)),
        None,
    )
    assert else_line is not None, (
        "a failed reload must take an explicit failure branch (stale "
        "sentinel, loud log), not fall through"
    )
    assert all(reload_if < i < else_line for i in sentinel_writes), (
        "readiness sentinel writes must live in the reload success branch "
        "only; on failure the sentinel stays stale so the probe reports the "
        "pod as not converged"
    )


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
