// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
/**
 * L1: one decision-funnel gate row. Family-agnostic — takes a `bindingTone`
 * (CSS color) so the binding gate is emphasized in the family's tone (slate for
 * expected, red only for faulted). A non-binding fail is neutral; red is never
 * used here for an expected no-link.
 */

import { GATE_LABELS, PRODUCER_LABELS } from "../derive";
import type { LadderGate } from "../types";
import { MarginPill } from "./MarginPill";

const MARK: Record<LadderGate["state"], string> = {
  pass: "✓",
  fail: "✗",
  not_evaluated: "·",
  not_applicable: "–",
};

export function GateRow({
  row,
  label,
  bindingTone,
}: {
  row: LadderGate;
  label?: string;
  bindingTone?: string;
}) {
  const markColor =
    row.state === "pass"
      ? "var(--accent-green)"
      : row.is_binding
        ? bindingTone ?? "var(--text-secondary)"
        : "var(--text-dim)";
  const nameStyle = row.is_binding ? { color: bindingTone ?? "var(--text-primary)" } : undefined;

  return (
    <div className={`gate-row${row.is_binding ? " gate-row--binding" : ""}`}>
      <span className="gate-mark" style={{ color: markColor }}>
        {MARK[row.state]}
      </span>
      <span className="gate-name" style={nameStyle}>
        {label ?? GATE_LABELS[row.gate]}
      </span>
      <span className="gate-detail">
        <MarginPill gate={row} />
        {row.rejecting_endpoint && row.rejecting_endpoint !== "none" ? (
          <span className="gate-endpoint"> · {row.rejecting_endpoint}</span>
        ) : null}
        {/* Provenance: which component owns this gate's verdict (spec Per-Pair Inspector). */}
        <span className="gate-producer" title="Producer of this verdict">
          {" "}
          · {PRODUCER_LABELS[row.producer]}
        </span>
      </span>
    </div>
  );
}
