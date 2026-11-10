// Copyright 2024-2026 .chance (dotchance)
// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms under AGPL-3.0 Section 7 are in the NOTICE file.
/**
 * L3: the ground-station explanation card. Composes the family headline,
 * actuation summary, best candidate, effective envelope, and decision ladder
 * from one DecisionExplanationFacts object. The card's tone is the family tone, so a
 * restrictive-but-correct station reads calm and only a true fault reads red.
 */

import { FAMILY_TONE } from "../families";
import { reasonLabel } from "../reasons";
import { deriveFamily, headline } from "../derive";
import { BestCandidate } from "./BestCandidate";
import { DecisionLadder } from "./DecisionLadder";
import { EffectiveEnvelopePanel } from "./EffectiveEnvelopePanel";
import { FamilyBadge } from "./FamilyBadge";
import { ObservedDiagnosis } from "./ObservedDiagnosis";
import type { DecisionExplanationFacts, GsDecisionTimelineFacts } from "../../generated/vsApi";

function fmtMs(ms: number): string {
  return ms >= 1000 ? `${(ms / 1000).toFixed(1)}s` : `${Math.round(ms)}ms`;
}

export function GroundStationCard({
  facts,
  timeline,
  timelineLimit,
  onTimelineLimitChange,
  onInspectSat,
}: {
  facts: DecisionExplanationFacts;
  timeline?: GsDecisionTimelineFacts | null;
  timelineLimit?: number;
  onTimelineLimitChange?: (limit: number) => void;
  onInspectSat?: (satId: string) => void;
}) {
  const family = deriveFamily(facts);
  const tone = FAMILY_TONE[family];
  const act = facts.actuation;

  return (
    <div className="gs-card" style={{ borderLeft: `3px solid ${tone.css}` }}>
      <div className="gs-card-head">
        <FamilyBadge family={family} />
      </div>
      <div className="gs-card-headline">{headline(facts)}</div>

      <div className="gs-card-actuation">
        <div className="detail-row">
          <span className="detail-label">Actuation</span>
          <span className="detail-value">{reasonLabel(act.state)}</span>
        </div>
        {act.diverged ? (
          <div className="detail-row">
            <span className="detail-label">Divergence</span>
            <span className="detail-value detail-value--failed">
              OME desired, kernel not up
            </span>
          </div>
        ) : null}
        {act.diverged && act.actuation_elapsed_ms !== null && act.fault_after_ms !== null ? (
          <div className="detail-row">
            <span className="detail-label">Convergence</span>
            <span className={`detail-value${family === "faulted" ? " detail-value--failed" : ""}`}>
              {fmtMs(act.actuation_elapsed_ms)} elapsed / fault at {fmtMs(act.fault_after_ms)}
            </span>
          </div>
        ) : null}
      </div>

      <h3>Best candidate</h3>
      <BestCandidate candidate={facts.best_candidate} gsId={facts.gs_id} />

      <h3>Effective envelope</h3>
      <EffectiveEnvelopePanel envelope={facts.envelope} />

      {timeline ? (
        <>
          <h3>Observed diagnosis</h3>
          <ObservedDiagnosis
            timeline={timeline}
            selectedLimit={timelineLimit}
            onLimitChange={onTimelineLimitChange}
            onSelectSat={onInspectSat}
          />
        </>
      ) : null}

      <h3>Decision ladder</h3>
      <DecisionLadder facts={facts} tone={tone.css} />
    </div>
  );
}
