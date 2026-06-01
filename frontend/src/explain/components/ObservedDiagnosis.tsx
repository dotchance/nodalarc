// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
/** L2: observed GS decision window and reason roll-up. */

import { FAMILY_TONE, type Family } from "../families";
import type { DecisionSampleState, GsDecisionTimelineFacts } from "../types";
import { ReasonText } from "./ReasonText";

const STATE_FAMILY: Record<DecisionSampleState, Family> = {
  scheduled: "connected",
  eligible_unselected: "eligible_unselected",
  expected_no_link: "expected_no_link",
};

const STATE_LABEL: Record<DecisionSampleState, string> = {
  scheduled: "Scheduled",
  eligible_unselected: "Eligible, not selected",
  expected_no_link: "Expected no link",
};

function peerOf(pair: [string, string] | null, gsId: string): string | null {
  if (!pair) return null;
  return pair[0] === gsId ? pair[1] : pair[0];
}

function fmtWindow(timeline: GsDecisionTimelineFacts): string | null {
  if (!timeline.window_started_sim_time || !timeline.window_ended_sim_time) return null;
  const start = new Date(timeline.window_started_sim_time);
  const end = new Date(timeline.window_ended_sim_time);
  if (Number.isNaN(start.getTime()) || Number.isNaN(end.getTime())) return null;
  const seconds = Math.max(0, Math.round((end.getTime() - start.getTime()) / 1000));
  return seconds > 0 ? `${seconds}s sim window` : "current sim tick";
}

export function ObservedDiagnosis({ timeline }: { timeline: GsDecisionTimelineFacts }) {
  const latest = timeline.samples.length ? timeline.samples[timeline.samples.length - 1] : null;
  const top = timeline.reason_counts[0] ?? null;
  const windowLabel = fmtWindow(timeline);

  return (
    <div className="observed-diagnosis">
      <div className="observed-diagnosis__head">
        <span className="detail-label">Observed window</span>
        <span className="detail-value">
          {timeline.sample_count} samples{windowLabel ? ` · ${windowLabel}` : ""}
        </span>
      </div>

      {latest ? (
        <div className="detail-row">
          <span className="detail-label">Latest</span>
          <span className="detail-value">
            {STATE_LABEL[latest.state]}
            {peerOf(latest.pair, timeline.gs_id) ? ` · ${peerOf(latest.pair, timeline.gs_id)}` : ""}
          </span>
        </div>
      ) : null}

      {top ? (
        <div className="detail-row">
          <span className="detail-label">Dominant</span>
          <span className="detail-value">
            {top.reason_code ? <ReasonText code={top.reason_code} /> : STATE_LABEL[top.state]}
            {` · ${top.count}/${timeline.sample_count}`}
          </span>
        </div>
      ) : null}

      <div className="observed-diagnosis__strip" aria-label="Observed decision timeline">
        {timeline.samples.map((sample) => {
          const family = STATE_FAMILY[sample.state];
          return (
            <span
              key={`${sample.epoch_id}:${sample.snapshot_seq}`}
              className="observed-diagnosis__sample"
              style={{ backgroundColor: FAMILY_TONE[family].css }}
              title={sample.reason_code ?? STATE_LABEL[sample.state]}
            />
          );
        })}
      </div>
    </div>
  );
}
