// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
/**
 * L3: the Per-Pair Inspector — the most precise view, for one GS<->sat pair. It
 * renders the canonical decision funnel (reusing the GroundStationCard) plus the
 * raw realization truths (OME desired / kernel actual) and snapshot provenance, so
 * a user can answer "why this exact pair?" Reusable: opened from a candidate row,
 * and (later) a beam or timeline segment.
 */

import { GroundStationCard } from "./GroundStationCard";
import type { DecisionExplanationFacts } from "../../generated/vsApi";

export function PairInspector({
  gsId,
  satId,
  facts,
  onBack,
}: {
  gsId: string;
  satId: string;
  facts: DecisionExplanationFacts | null;
  onBack: () => void;
}) {
  return (
    <div className="pair-inspector">
      <div className="pair-inspector-head">
        <button className="pair-inspector-back" onClick={onBack} title="Back to ground station">
          ← Back
        </button>
        <span className="pair-inspector-pair">
          {gsId} ↔ {satId}
        </span>
      </div>

      {facts ? (
        <>
          <GroundStationCard facts={facts} />
          <div className="pair-inspector-truth">
            <div className="detail-row">
              <span className="detail-label">OME desired</span>
              <span className="detail-value">{facts.actuation.ome_desired ? "yes" : "no"}</span>
            </div>
            <div className="detail-row">
              <span className="detail-label">Kernel actual</span>
              <span className="detail-value">{facts.actuation.kernel_up ? "up" : "down"}</span>
            </div>
          </div>
          <div className="pair-inspector-provenance">
            sim {facts.sim_time} · seq {facts.snapshot_seq} · epoch {facts.epoch_id}
          </div>
        </>
      ) : (
        <div className="pair-inspector-empty">
          No decision for this pair in the latest snapshot.
        </div>
      )}
    </div>
  );
}
