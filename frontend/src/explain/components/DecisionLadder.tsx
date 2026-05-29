// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
/** L2: the full decision funnel — every gate, binding one emphasized. */

import { displayLadder } from "../derive";
import type { DecisionFacts } from "../types";
import { GateRow } from "./GateRow";

export function DecisionLadder({ facts, tone }: { facts: DecisionFacts; tone?: string }) {
  return (
    <div className="decision-ladder">
      {displayLadder(facts).map(({ row, label }) => (
        <GateRow key={row.gate} row={row} label={label} bindingTone={tone} />
      ))}
    </div>
  );
}
