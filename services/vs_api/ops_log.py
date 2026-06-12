# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""Process-wide arrival sequence for operational events.

Every ops event — system-side and session-side — is stamped with a
monotonic ``seq`` at arrival so the websocket feed can ship the log
INCREMENTALLY: each connection tracks a cursor and receives only events
newer than it, and the frontend merges by seq into its own scrollback.

Why: re-serializing the whole 500-entry log into every 1 Hz frame for
every client measured 2.4 MB of a 2.5 MB frame (96%) on a 10-node
session — the feed's dominant cost was redundant resends of an
append-only history, not per-tick state.

``OPS_LOG_TOKEN`` identifies this process's sequence space. It rides in
every snapshot; a client that observes the token change (VS-API
restarted, seqs restarted from 1) replaces its scrollback instead of
merging, so colliding seqs from different processes never dedupe away
fresh events.
"""

from __future__ import annotations

import itertools
import uuid

OPS_LOG_TOKEN = uuid.uuid4().hex

_counter = itertools.count(1)


def stamp_ops_event(event: dict) -> dict:
    """Stamp an arrival seq onto an ops event. CPython-atomic."""
    event["seq"] = next(_counter)
    return event


def is_operator_visible_ops_event(event: dict) -> bool:
    """False for routine successful telemetry that needs no UI attention.

    Applied at APPEND time, not just at serve time: the log window holds
    500 entries, and routine telemetry admitted into it evicts the real
    operational history an operator scrolls back for (measured live: 198
    of 223 KB of the window was periodic pacing/substrate/lifecycle
    telemetry). The NODALARC_OPS stream remains the unfiltered archive —
    this gates only the operator-facing log panel's window.
    """
    level = str(event.get("level") or "").lower()
    code = event.get("code")
    message = str(event.get("message") or "")

    if level == "debug":
        return False

    if code == "COMMAND_APPLIED" and level in {"", "info"}:
        return False

    # Periodic engine pacing statistics — ops telemetry, not events.
    if code == "PACING_TELEMETRY" and level in {"", "info"}:
        return False

    # Routine substrate measurement writes; failures arrive at warning+.
    if code == "SUBSTRATE_MONITOR" and level in {"", "info"}:
        return False

    return not (
        code == "DISPATCH_ACTUATOR"
        and level in {"", "info"}
        and message.startswith("Actuation latency op=")
        and " failed=0" in message
    )


def operator_visible_ops_events(events: list[dict]) -> list[dict]:
    return [event for event in events if is_operator_visible_ops_event(event)]
