# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""Cross-language contract: the frontend reason registry must mirror the REAL
backend reason vocabularies, not a hand-copied snapshot of itself.

The frontend's registry-completeness test (registry.test.ts) only proves the
registry covers the frontend's OWN union arrays — tautological if those arrays
drift from Python. This test closes the loop from the other side: it extracts
the authoritative Python reason codes (Literal members / StrEnum values) and the
literal arrays declared in frontend/src/explain/reasons.ts, and asserts they are
identical in BOTH directions. Add a backend reason code and forget the frontend,
and this fails — which is the whole point.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import get_args

from nodalarc.models.link_decisions import (
    GroundAllocationEventCategory,
    GroundUnscheduledReason,
    GroundVisibilityRejectReason,
)
from nodalarc.models.scheduler_ops import ActuationFailureClass, ActuationState

_REASONS_TS = Path(__file__).resolve().parents[2] / "frontend/src/explain/reasons.ts"


def _frontend_array(const_name: str) -> set[str]:
    """Extract the string members of an exported readonly array from reasons.ts."""
    text = _REASONS_TS.read_text(encoding="utf-8")
    match = re.search(rf"export const {const_name}\b[^=]*=\s*\[(.*?)\]", text, re.DOTALL)
    assert match, f"{const_name} not found as an exported array in {_REASONS_TS}"
    return set(re.findall(r'"([^"]+)"', match.group(1)))


def _literal_values(literal_type: object) -> set[str]:
    return set(get_args(literal_type))


def test_reasons_ts_exists():
    assert _REASONS_TS.is_file(), f"frontend reason registry missing at {_REASONS_TS}"


def test_visibility_reject_reasons_match_backend():
    assert _frontend_array("GROUND_VISIBILITY_REJECT_REASONS") == _literal_values(
        GroundVisibilityRejectReason
    )


def test_unscheduled_reasons_match_backend():
    assert _frontend_array("GROUND_UNSCHEDULED_REASONS") == _literal_values(GroundUnscheduledReason)


def test_allocation_event_categories_match_backend():
    assert _frontend_array("GROUND_ALLOCATION_EVENT_CATEGORIES") == _literal_values(
        GroundAllocationEventCategory
    )


def test_actuation_states_match_backend():
    assert _frontend_array("ACTUATION_STATES") == _literal_values(ActuationState)


def test_actuation_failure_classes_match_backend():
    assert _frontend_array("ACTUATION_FAILURE_CLASSES") == {m.value for m in ActuationFailureClass}
