# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""Cross-language contract: the frontend must mirror the REAL backend wire shape
and reason vocabularies, not a hand-copied snapshot of itself.

Two drift surfaces are guarded:
  1. Reason vocabularies — the frontend registry covering its OWN union arrays
     (registry.test.ts) is tautological if those arrays drift from Python. This
     extracts the authoritative Python reason codes (Literal / StrEnum members) and
     the literal arrays in frontend/src/explain/reasons.ts and asserts they match
     in BOTH directions.
  2. Wire-shape field names — the TS interfaces in frontend/src/explain/types.ts
     are a no-mapping mirror of the backend DecisionExplanationFacts models. A
     snake_case field rename on either side would silently make the frontend read
     `undefined`; this asserts the field-name sets are identical per model.
Add a backend field or reason code and forget the frontend, and this fails.
"""

from __future__ import annotations

import re
from enum import StrEnum
from pathlib import Path
from typing import get_args

from nodalarc.explain import ACTUATION_EXPLANATION_REASONS
from nodalarc.models.decision_explanation import (
    ActuationFacts,
    CandidateFacts,
    DecisionExplanationFacts,
    EffectiveEnvelopeFacts,
    EnvelopeEndpoint,
    ExplanationProducer,
    FunnelGate,
    GateState,
    GsDecisionReasonCount,
    GsDecisionTimelineFacts,
    GsDecisionTimelineSample,
    LadderGate,
)
from nodalarc.models.link_decisions import (
    GroundAllocationEventCategory,
    GroundUnscheduledReason,
    GroundVisibilityRejectingEndpoint,
    GroundVisibilityRejectReason,
)
from nodalarc.models.link_events import LINK_EVENT_REASONS
from nodalarc.models.scheduler_ops import ActuationFailureClass, ActuationState, SchedulerOpsCode

_REASONS_TS = Path(__file__).resolve().parents[2] / "frontend/src/explain/reasons.ts"
_FAMILIES_TS = Path(__file__).resolve().parents[2] / "frontend/src/explain/families.ts"
_TYPES_TS = Path(__file__).resolve().parents[2] / "frontend/src/explain/types.ts"
_LINK_EVENTS_TS = Path(__file__).resolve().parents[2] / "frontend/src/explain/linkEvents.ts"

# Each backend model and the TS interface that mirrors its wire shape.
_WIRE_MODELS = [
    (DecisionExplanationFacts, "DecisionFacts"),
    (LadderGate, "LadderGate"),
    (EffectiveEnvelopeFacts, "EffectiveEnvelopeFacts"),
    (EnvelopeEndpoint, "EnvelopeEndpoint"),
    (CandidateFacts, "CandidateFacts"),
    (ActuationFacts, "ActuationFacts"),
    (GsDecisionTimelineSample, "GsDecisionTimelineSample"),
    (GsDecisionReasonCount, "GsDecisionReasonCount"),
    (GsDecisionTimelineFacts, "GsDecisionTimelineFacts"),
]


def _ts_interface_fields(name: str) -> set[str]:
    """Field names declared in an exported TS interface (skipping comments)."""
    text = _TYPES_TS.read_text(encoding="utf-8")
    match = re.search(rf"export interface {name} \{{\n(.*?)\n\}}", text, re.DOTALL)
    assert match, f"interface {name} not found in {_TYPES_TS}"
    fields: set[str] = set()
    for raw in match.group(1).splitlines():
        line = raw.strip()
        if not line or line.startswith(("//", "*", "/")):
            continue
        field = re.match(r"(\w+)\??\s*:", line)
        if field:
            fields.add(field.group(1))
    return fields


def test_wire_shape_field_names_match_backend():
    for model, ts_name in _WIRE_MODELS:
        py_fields = set(model.model_fields)
        ts_fields = _ts_interface_fields(ts_name)
        assert py_fields == ts_fields, (
            f"{model.__name__} <-> TS {ts_name} field drift: "
            f"py-only={py_fields - ts_fields}, ts-only={ts_fields - py_fields}"
        )


def _frontend_array(const_name: str, path: Path = _REASONS_TS) -> set[str]:
    """Extract the string members of an exported readonly array from a TS module."""
    text = path.read_text(encoding="utf-8")
    match = re.search(rf"export const {const_name}\b[^=]*=\s*\[(.*?)\]", text, re.DOTALL)
    assert match, f"{const_name} not found as an exported array in {path}"
    return set(re.findall(r'"([^"]+)"', match.group(1)))


def _literal_values(literal_type: object) -> set[str]:
    if isinstance(literal_type, type) and issubclass(literal_type, StrEnum):
        return {m.value for m in literal_type}
    return set(get_args(literal_type))


def test_reasons_ts_exists():
    assert _REASONS_TS.is_file(), f"frontend reason registry missing at {_REASONS_TS}"


def _link_event_codes_ts() -> set[str]:
    """The `code: "..."` values of the frontend LINK_EVENT_REGISTRY records."""
    text = _LINK_EVENTS_TS.read_text(encoding="utf-8")
    return set(re.findall(r'code:\s*"([^"]+)"', text))


def test_link_event_reasons_match_backend():
    """The link-lifecycle vocabulary cannot drift between backend and the frontend registry."""
    assert _LINK_EVENTS_TS.is_file(), f"frontend link-event registry missing at {_LINK_EVENTS_TS}"
    assert _link_event_codes_ts() == set(LINK_EVENT_REASONS)


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


def test_scheduler_ops_codes_match_backend():
    assert _frontend_array("SCHEDULER_OPS_CODES") == {m.value for m in SchedulerOpsCode}


def test_funnel_gates_match_backend():
    assert _frontend_array("FUNNEL_GATES", _FAMILIES_TS) == _literal_values(FunnelGate)


def test_gate_states_match_backend():
    assert _frontend_array("GATE_STATES", _FAMILIES_TS) == _literal_values(GateState)


def test_explanation_producers_match_backend():
    assert _frontend_array("PRODUCERS", _FAMILIES_TS) == _literal_values(ExplanationProducer)


def test_rejecting_endpoints_match_backend():
    assert _frontend_array("REJECTING_ENDPOINTS", _FAMILIES_TS) == _literal_values(
        GroundVisibilityRejectingEndpoint
    )


def test_actuation_explanation_reasons_match_backend():
    # Composer-synthesized actuation binding reasons (e.g. "actuation_diverged") must
    # match the frontend list, or a backend rename silently drops the headline to
    # "State unknown" while the registry record orphans.
    assert _frontend_array("ACTUATION_EXPLANATION_REASONS") == set(ACTUATION_EXPLANATION_REASONS)
