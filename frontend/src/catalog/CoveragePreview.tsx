// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Elastic License 2.0 (ELv2). See LICENSE file.
/** Coverage preview results display.
 *
 * ISL feasibility shown in green when healthy. Insights colored by
 * severity: info (neutral), note (blue), warning (amber), error (red).
 */

import type { CoveragePreviewResult } from "./wizardTypes";

interface CoveragePreviewProps {
  result: CoveragePreviewResult;
  onContinue: () => void;
  onBack: () => void;
}

/** Color for ISL feasibility percentage. */
function feasibilityColor(pct: number): string {
  if (pct >= 95) return "var(--accent-green, #44cc66)";
  if (pct >= 70) return "var(--accent-teal, #00ccbb)";
  if (pct >= 40) return "var(--text-primary, #ddd)";
  return "var(--accent-red, #ff4444)";
}

/** Severity → CSS class mapping. */
function severityClass(severity: string): string {
  switch (severity) {
    case "info": return "wizard-insight--info";
    case "note": return "wizard-insight--note";
    case "warning": return "wizard-insight--warning";
    case "error": return "wizard-insight--error";
    default: return "wizard-insight--note";
  }
}

/** Severity → label. */
function severityLabel(severity: string): string {
  switch (severity) {
    case "info": return "\u2139\ufe0f";
    case "note": return "\u2139\ufe0f";
    case "warning": return "\u26a0\ufe0f";
    case "error": return "\u274c";
    default: return "";
  }
}

/** Render a single insight (supports both old string format and new typed format). */
function Insight({ item }: { item: { severity: string; message: string } | string }) {
  if (typeof item === "string") {
    return <div className="wizard-insight wizard-insight--note">{item}</div>;
  }
  return (
    <div className={`wizard-insight ${severityClass(item.severity)}`}>
      <span className="wizard-insight-icon">{severityLabel(item.severity)}</span>
      <span>{item.message}</span>
    </div>
  );
}

export function CoveragePreview({ result, onContinue, onBack }: CoveragePreviewProps) {
  const { isl, ground_stations: gs } = result;
  const fColor = feasibilityColor(isl.feasibility_pct);

  // Split insights by severity for ordering: errors first, then warnings, then notes/info
  const errors = result.warnings.filter((w) => typeof w !== "string" && w.severity === "error");
  const warnings = result.warnings.filter((w) => typeof w !== "string" && w.severity === "warning");
  const notes = result.warnings.filter(
    (w) => typeof w === "string" || w.severity === "info" || w.severity === "note",
  );

  return (
    <div className="wizard-panel">
      <h2 className="wizard-panel-title">Coverage Preview</h2>
      <p className="wizard-preview-note">
        Computed at {result.preview_step_s}s resolution over one orbital period ({Math.round(result.orbital_period_s / 60)} min).
        This is a fast approximation — the full 1s timeline runs after deployment.
      </p>

      {/* ISL section */}
      <div className="wizard-preview-section">
        <h3 className="wizard-section-title">ISL Links</h3>
        <div className="wizard-preview-stats">
          <div className="wizard-preview-stat">
            <span className="wizard-preview-stat-value" style={{ color: fColor }}>
              {isl.feasibility_pct.toFixed(0)}%
            </span>
            <span className="wizard-preview-stat-label">
              {isl.formed_at_least_once} of {isl.total_possible} ISLs form
            </span>
          </div>
          <div className="wizard-preview-stat">
            <span className="wizard-preview-stat-value">{isl.min_active}</span>
            <span className="wizard-preview-stat-label">min active ISL links</span>
          </div>
          <div className="wizard-preview-stat">
            <span className="wizard-preview-stat-value">{isl.max_active}</span>
            <span className="wizard-preview-stat-label">max active ISL links</span>
          </div>
        </div>
      </div>

      {/* GS section */}
      <div className="wizard-preview-section">
        <h3 className="wizard-section-title">Ground Station Coverage</h3>
        <div className="wizard-preview-stats">
          <div className="wizard-preview-stat">
            <span className="wizard-preview-stat-value">{gs.simultaneous_min}</span>
            <span className="wizard-preview-stat-label">min simultaneous GS</span>
          </div>
          <div className="wizard-preview-stat">
            <span className="wizard-preview-stat-value">{gs.simultaneous_max}</span>
            <span className="wizard-preview-stat-label">max simultaneous GS</span>
          </div>
          <div className="wizard-preview-stat">
            <span className="wizard-preview-stat-value">{gs.simultaneous_mean.toFixed(1)}</span>
            <span className="wizard-preview-stat-label">mean simultaneous GS</span>
          </div>
        </div>

        <div className="wizard-preview-gs-table">
          {Object.entries(gs.per_station).map(([name, stats]) => (
            <div key={name} className={`wizard-preview-gs-row ${stats.coverage_pct < 10 ? "wizard-preview-gs-row--warn" : ""}`}>
              <span className="wizard-preview-gs-name">{name}</span>
              <span className="wizard-preview-gs-pct">{stats.coverage_pct.toFixed(1)}%</span>
              <span className="wizard-preview-gs-gap">
                {stats.longest_gap_s > 0 ? `gap: ${Math.round(stats.longest_gap_s)}s` : "continuous"}
              </span>
              {stats.reason && (
                <div className="wizard-preview-gs-reason">{stats.reason}</div>
              )}
            </div>
          ))}
        </div>
      </div>

      {/* Insights — errors first, then warnings, then notes/info */}
      {result.warnings.length > 0 && (
        <div className="wizard-preview-insights">
          {errors.map((w, i) => <Insight key={`e${i}`} item={w} />)}
          {warnings.map((w, i) => <Insight key={`w${i}`} item={w} />)}
          {notes.map((w, i) => <Insight key={`n${i}`} item={w} />)}
        </div>
      )}

      <div className="wizard-nav">
        <button className="wizard-nav-btn" onClick={onBack}>
          Back to Configuration
        </button>
        <button className="wizard-nav-btn wizard-nav-btn--primary" onClick={onContinue}>
          Continue to Protocol
        </button>
      </div>
    </div>
  );
}
