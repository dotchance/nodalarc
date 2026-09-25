# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""A node its adapter cannot render fails the session's preparation with the reason."""

from __future__ import annotations

from pathlib import Path

import nodalarc_operator.workloads.preparation as preparation
import pytest
from nodalarc.resolve_session import load_session_resolution_from_file
from nodalarc.workloads.adapter import AdapterRenderRefusal
from nodalarc_operator.workloads.preparation import (
    WorkloadPreparationError,
    prepare_session_workloads,
)

from tests.catalog_session_fixtures import shipped_read_view

ROOT = Path(__file__).resolve().parents[2]


class _FailingAdapter:
    def __init__(self, failure: Exception) -> None:
        self._failure = failure

    def render_node(self, resolved_node, session_context):
        raise self._failure


def _prepare_with(monkeypatch: pytest.MonkeyPatch, failure: Exception) -> None:
    monkeypatch.setattr(preparation, "adapter_named", lambda _name: _FailingAdapter(failure))
    resolution = load_session_resolution_from_file(
        ROOT / "catalog/nodalarc/sessions/earth-leo-simple.yaml", catalog=shipped_read_view()
    )
    prepare_session_workloads(resolution, namespace="nodalarc", owner_ref={"uid": "test-uid"})


def test_an_adapter_refusal_fails_preparation_with_its_reason(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    refusal = AdapterRenderRefusal("no metric for isl0")

    with pytest.raises(
        WorkloadPreparationError, match=r"^adapter 'frr' cannot render '[a-z0-9-]+': no metric"
    ) as failed:
        _prepare_with(monkeypatch, refusal)

    assert failed.value.__cause__ is refusal


def test_any_other_render_failure_fails_preparation_with_its_type_and_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    failure = KeyError("isl9")

    with pytest.raises(
        WorkloadPreparationError,
        match=r"^adapter 'frr' failed to render '[a-z0-9-]+': KeyError: 'isl9'$",
    ) as failed:
        _prepare_with(monkeypatch, failure)

    assert failed.value.__cause__ is failure
