// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
/**
 * L2: the focal candidate. A viable-but-withheld pair (connectivity available,
 * policy/capacity withheld it) is called out prominently — categorically
 * different from a physics near-miss.
 */

import { GATE_LABELS } from "../derive";
import type { CandidateFacts } from "../types";
import { ReasonText } from "./ReasonText";

export function BestCandidate({ candidate, gsId }: { candidate: CandidateFacts; gsId: string }) {
  const sat = candidate.pair.find((n) => n !== gsId) ?? candidate.pair[1];
  return (
    <div className="best-candidate">
      <div className="detail-row">
        <span className="detail-label">Best candidate</span>
        <span className="detail-value">{sat}</span>
      </div>
      {candidate.viable_withheld ? (
        <div className="bc-withheld">Connectivity was available — withheld by policy/capacity.</div>
      ) : null}
      {candidate.binding_gate ? (
        <div className="detail-row">
          <span className="detail-label">Stopped at</span>
          <span className="detail-value">
            {GATE_LABELS[candidate.binding_gate]}
            {candidate.binding_reason_code ? (
              <>
                {" — "}
                <ReasonText code={candidate.binding_reason_code} />
              </>
            ) : null}
          </span>
        </div>
      ) : null}
    </div>
  );
}
