// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
/** L2: the full decision funnel — every gate, binding one emphasized. */

import type { LadderGate } from "../types";
import { GateRow } from "./GateRow";

export function DecisionLadder({ ladder, tone }: { ladder: LadderGate[]; tone?: string }) {
  return (
    <div className="decision-ladder">
      {ladder.map((g) => (
        <GateRow key={g.gate} row={g} bindingTone={tone} />
      ))}
    </div>
  );
}
