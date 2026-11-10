// Copyright 2024-2026 .chance (dotchance)
// SPDX-License-Identifier: AGPL-3.0-only
/** L2: the full decision funnel — every gate, binding one emphasized. */

import { displayLadder } from "../derive";
import { GateRow } from "./GateRow";
import type { DecisionExplanationFacts } from "../../generated/vsApi";

export function DecisionLadder({ facts, tone }: { facts: DecisionExplanationFacts; tone?: string }) {
  return (
    <div className="decision-ladder">
      {displayLadder(facts).map(({ row, label }) => (
        <GateRow key={row.gate} row={row} label={label} bindingTone={tone} />
      ))}
    </div>
  );
}
