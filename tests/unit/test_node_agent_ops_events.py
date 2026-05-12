# Copyright 2024-2026 .chance (dotchance)
# Licensed under the NodalArc Source Available License 1.0. See LICENSE file.
"""Tests for Node Agent OpsEvent spooling."""

from __future__ import annotations

import asyncio
import json

import pytest
from node_agent import ops_events


def setup_function() -> None:
    ops_events._reset_for_tests()


def test_spool_failure_writes_valid_jsonl(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    spool = tmp_path / "ops-events.jsonl"
    monkeypatch.setenv("NODE_AGENT_OPS_SPOOL", str(spool))

    ops_events.spool_failure(
        code="STARTUP_NATS_FAILED",
        message="cannot connect to NATS",
        details={"nats_url": "nats://nodalarc-nats:4222"},
    )

    payload = json.loads(spool.read_text().strip())
    assert payload["source"] == "node_agent"
    assert payload["level"] == "critical"
    assert payload["code"] == "STARTUP_NATS_FAILED"
    assert payload["details"]["nats_url"] == "nats://nodalarc-nats:4222"


def test_drain_spool_publishes_and_truncates(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    spool = tmp_path / "ops-events.jsonl"
    monkeypatch.setenv("NODE_AGENT_OPS_SPOOL", str(spool))
    ops_events.spool_failure(code="MANIFEST_VALIDATION_FAILED", message="bad manifest")

    class _Js:
        def __init__(self) -> None:
            self.published: list[tuple[str, bytes]] = []

        async def publish(self, subject: str, payload: bytes) -> None:
            self.published.append((subject, payload))

    js = _Js()
    count = asyncio.run(ops_events.drain_spool(js))

    assert count == 1
    assert spool.read_text() == ""
    assert js.published[0][0] == "nodalarc.ops._infra.node_agent.manifest_validation_failed"
