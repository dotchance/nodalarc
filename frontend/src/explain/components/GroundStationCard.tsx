// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
/**
 * L3: the ground-station explanation card. Composes the family headline,
 * actuation summary, best candidate, effective envelope, and decision ladder
 * from one DecisionFacts object. The card's tone is the family tone, so a
 * restrictive-but-correct station reads calm and only a true fault reads red.
 */

import { FAMILY_TONE } from "../families";
import { deriveFamily, headline } from "../derive";
import type { DecisionFacts, GsDecisionTimelineFacts } from "../types";
import { BestCandidate } from "./BestCandidate";
import { DecisionLadder } from "./DecisionLadder";
import { EffectiveEnvelopePanel } from "./EffectiveEnvelopePanel";
import { FamilyBadge } from "./FamilyBadge";
import { ObservedDiagnosis } from "./ObservedDiagnosis";

function fmtMs(ms: number): string {
  return ms >= 1000 ? `${(ms / 1000).toFixed(1)}s` : `${Math.round(ms)}ms`;
}

export function GroundStationCard({
  facts,
  timeline,
}: {
  facts: DecisionFacts;
  timeline?: GsDecisionTimelineFacts | null;
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

      {facts.actuation ? (
        <div className="gs-card-actuation">
          <div className="detail-row">
            <span className="detail-label">Actuation</span>
            <span className="detail-value">{facts.actuation.state}</span>
          </div>
          {facts.actuation.diverged ? (
            <div className="detail-row">
              <span className="detail-label">Divergence</span>
              <span className="detail-value detail-value--failed">
                OME desired, kernel not up
              </span>
            </div>
          ) : null}
          {act && act.diverged && act.actuation_elapsed_ms !== null && act.fault_after_ms !== null ? (
            <div className="detail-row">
              <span className="detail-label">Convergence</span>
              <span className={`detail-value${family === "faulted" ? " detail-value--failed" : ""}`}>
                {fmtMs(act.actuation_elapsed_ms)} elapsed / fault at {fmtMs(act.fault_after_ms)}
              </span>
            </div>
          ) : null}
        </div>
      ) : null}

      {facts.best_candidate ? (
        <>
          <h3>Best candidate</h3>
          <BestCandidate candidate={facts.best_candidate} gsId={facts.gs_id} />
        </>
      ) : null}

      {facts.envelope ? (
        <>
          <h3>Effective envelope</h3>
          <EffectiveEnvelopePanel envelope={facts.envelope} />
        </>
      ) : null}

      {timeline ? (
        <>
          <h3>Observed diagnosis</h3>
          <ObservedDiagnosis timeline={timeline} />
        </>
      ) : null}

      <h3>Decision ladder</h3>
      <DecisionLadder facts={facts} tone={tone.css} />
    </div>
  );
}
