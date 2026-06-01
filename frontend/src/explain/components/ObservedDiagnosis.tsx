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

const WINDOW_LIMITS = [30, 120, 720] as const;

export function ObservedDiagnosis({
  timeline,
  selectedLimit = 120,
  onLimitChange,
  onSelectSat,
}: {
  timeline: GsDecisionTimelineFacts;
  selectedLimit?: number;
  onLimitChange?: (limit: number) => void;
  onSelectSat?: (satId: string) => void;
}) {
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

      {onLimitChange ? (
        <div className="observed-diagnosis__windows" role="group" aria-label="Observed sample window">
          {WINDOW_LIMITS.map((limit) => (
            <button
              key={limit}
              type="button"
              className={`observed-diagnosis__window${selectedLimit === limit ? " observed-diagnosis__window--active" : ""}`}
              onClick={() => onLimitChange(limit)}
              aria-pressed={selectedLimit === limit}
            >
              {limit}
            </button>
          ))}
        </div>
      ) : null}

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
          const peer = peerOf(sample.pair, timeline.gs_id);
          const title = `${STATE_LABEL[sample.state]}${peer ? ` · ${peer}` : ""}${sample.reason_code ? ` · ${sample.reason_code}` : ""}`;
          return (
            <button
              key={`${sample.epoch_id}:${sample.snapshot_seq}`}
              type="button"
              className="observed-diagnosis__sample"
              style={{ backgroundColor: FAMILY_TONE[family].css }}
              title={title}
              disabled={!peer || !onSelectSat}
              onClick={() => {
                if (peer) onSelectSat?.(peer);
              }}
            />
          );
        })}
      </div>
    </div>
  );
}
