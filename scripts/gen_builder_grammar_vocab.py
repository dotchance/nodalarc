#!/usr/bin/env python3
"""Generate the frontend builder's grammar vocabulary from the authoritative
Python Literal grammar enums.

The emitted file (frontend/src/builder/generated/grammarVocab.ts) is GENERATED
and must not be hand-edited. This aligns shared enums/vocabulary between the
frontend and the Python grammar. It does NOT make the browser parser/serializer
authoritative and does NOT prove full grammar parity.

Usage:
  uv run python scripts/gen_builder_grammar_vocab.py           # write the file
  uv run python scripts/gen_builder_grammar_vocab.py --check   # fail if stale
"""

from __future__ import annotations

import sys
import typing as t
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))

from nodalarc.models.catalog import (  # noqa: E402
    BoresightMode,
    ForwardingClass,
    LagrangePoint,
    PhasingMode,
    Propagator,
    TerminalMedium,
)
from nodalarc.models.ground_policy import (  # noqa: E402
    CrossTenantDisplacementPolicy,
    HandoverPolicyName,
    MbbPreemptionPolicy,
    RankingComponent,
    SelectionPolicyName,
    SuccessorAbortPolicy,
)
from nodalarc.models.link_decisions import GroundHandoverModeName  # noqa: E402
from nodalarc.models.link_rules import (  # noqa: E402
    ExplicitPairsTopology,
    LinkLabel,
    LinkMedium,
    LinkRelation,
    MountRole,
    NearestNTopology,
    NearestVisibleTopology,
    VisibleCandidatesTopology,
)
from nodalarc.models.segments import GroundScheduling  # noqa: E402

OUT = ROOT / "frontend/src/builder/generated/grammarVocab.ts"


def _field_literal(model: type, name: str) -> tuple[str, ...]:
    """String values of a model field whose annotation is Literal[...] | None."""
    out: list[str] = []
    for arg in t.get_args(model.model_fields[name].annotation):
        out.extend(v for v in t.get_args(arg) if isinstance(v, str))
    return tuple(out)


_topology_modes = tuple(
    t.get_args(cls.model_fields["mode"].annotation)[0]
    for cls in (
        VisibleCandidatesTopology,
        NearestVisibleTopology,
        NearestNTopology,
        ExplicitPairsTopology,
    )
)

# Ordered so output is deterministic. (TS const, TS type, source note, values)
SPEC: list[tuple[str, str, str, tuple[str, ...]]] = [
    ("TERMINAL_MEDIUM", "TerminalMedium", "catalog.TerminalMedium", t.get_args(TerminalMedium)),
    ("MOUNT_ROLE", "MountRole", "link_rules.MountRole", t.get_args(MountRole)),
    ("FORWARDING_CLASS", "ForwardingClass", "catalog.ForwardingClass", t.get_args(ForwardingClass)),
    ("PROPAGATOR", "Propagator", "catalog.Propagator", t.get_args(Propagator)),
    ("PHASING_MODE", "PhasingMode", "catalog.PhasingMode", t.get_args(PhasingMode)),
    ("BORESIGHT_MODE", "BoresightMode", "catalog.BoresightMode", t.get_args(BoresightMode)),
    ("LAGRANGE_POINT", "LagrangePoint", "catalog.LagrangePoint", t.get_args(LagrangePoint)),
    ("LINK_MEDIUM", "LinkMedium", "link_rules.LinkMedium", t.get_args(LinkMedium)),
    ("LINK_LABEL", "LinkLabel", "link_rules.LinkLabel", t.get_args(LinkLabel)),
    ("LINK_RELATION", "LinkRelation", "link_rules.LinkRelation", t.get_args(LinkRelation)),
    ("TOPOLOGY_MODE", "TopologyMode", "link_rules topology variants .mode", _topology_modes),
    (
        "SELECTION_POLICY_NAME",
        "SelectionPolicyName",
        "ground_policy.SelectionPolicyName",
        t.get_args(SelectionPolicyName),
    ),
    (
        "HANDOVER_POLICY_NAME",
        "HandoverPolicyName",
        "ground_policy.HandoverPolicyName",
        t.get_args(HandoverPolicyName),
    ),
    (
        "HANDOVER_MODE",
        "HandoverMode",
        "segments.GroundScheduling.handover_mode",
        _field_literal(GroundScheduling, "handover_mode"),
    ),
    (
        "HANDOVER_CONCURRENCY",
        "HandoverConcurrency",
        "segments.GroundScheduling.handover_concurrency",
        _field_literal(GroundScheduling, "handover_concurrency"),
    ),
    (
        "RANKING_COMPONENT",
        "RankingComponent",
        "ground_policy.RankingComponent",
        t.get_args(RankingComponent),
    ),
    (
        "MBB_PREEMPTION_POLICY",
        "MbbPreemptionPolicy",
        "ground_policy.MbbPreemptionPolicy",
        t.get_args(MbbPreemptionPolicy),
    ),
    (
        "SUCCESSOR_ABORT_POLICY",
        "SuccessorAbortPolicy",
        "ground_policy.SuccessorAbortPolicy",
        t.get_args(SuccessorAbortPolicy),
    ),
    (
        "CROSS_TENANT_DISPLACEMENT_POLICY",
        "CrossTenantDisplacementPolicy",
        "ground_policy.CrossTenantDisplacementPolicy",
        t.get_args(CrossTenantDisplacementPolicy),
    ),
    (
        "GROUND_HANDOVER_MODE",
        "GroundHandoverMode",
        "link_decisions.GroundHandoverModeName",
        t.get_args(GroundHandoverModeName),
    ),
]


def render() -> str:
    lines = [
        "// GENERATED FILE — DO NOT EDIT BY HAND.",
        "// Source of truth: lib/nodalarc/models/*.py Literal grammar enums.",
        "// Regenerate: uv run python scripts/gen_builder_grammar_vocab.py",
        "//",
        "// This aligns shared enums/vocabulary between the frontend and the Python",
        "// grammar. It does NOT make the browser parser/serializer authoritative and",
        "// does NOT prove full grammar parity.",
        "",
    ]
    for const, tstype, src, vals in SPEC:
        arr = ", ".join(f'"{v}"' for v in vals)
        lines.append(f"/** {src} */")
        lines.append(f"export const {const} = [{arr}] as const;")
        lines.append(f"export type {tstype} = (typeof {const})[number];")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def main() -> None:
    text = render()
    if "--check" in sys.argv:
        current = OUT.read_text(encoding="utf-8") if OUT.exists() else ""
        if current != text:
            print(
                "STALE: frontend/src/builder/generated/grammarVocab.ts differs from the "
                "Python grammar enums. Regenerate: uv run python scripts/gen_builder_grammar_vocab.py",
                file=sys.stderr,
            )
            sys.exit(1)
        print("grammarVocab.ts is up to date.")
        return
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(text, encoding="utf-8")
    print(f"wrote {OUT.relative_to(ROOT)} ({len(SPEC)} vocabularies)")


if __name__ == "__main__":
    main()
