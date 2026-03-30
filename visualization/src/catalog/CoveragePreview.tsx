/** Coverage preview results display.
 *
 * Shows ISL feasibility with failure breakdown, per-GS coverage
 * with diagnostic reasons, and plain-English warnings.
 * Always allows continuing to deploy.
 */

import type { CoveragePreviewResult } from "./wizardTypes";

interface CoveragePreviewProps {
  result: CoveragePreviewResult;
  onContinue: () => void;
  onBack: () => void;
}

/** Render the ISL failure reason breakdown as a stacked bar description. */
function IslFailureBreakdown({ reasons }: { reasons: NonNullable<CoveragePreviewResult["isl"]["failure_reasons"]> }) {
  const total =
    reasons.range_exceeded +
    reasons.tracking_exceeded +
    reasons.los_blocked +
    reasons.field_of_regard +
    reasons.polar_seam +
    reasons.terminal_exhausted;

  if (total === 0) return null;

  const items: { label: string; count: number; explanation: string }[] = [];

  if (reasons.range_exceeded > 0) {
    items.push({
      label: "Range exceeded",
      count: reasons.range_exceeded,
      explanation: "Satellite pairs are further apart than the terminal's maximum range",
    });
  }
  if (reasons.tracking_exceeded > 0) {
    items.push({
      label: "Tracking rate exceeded",
      count: reasons.tracking_exceeded,
      explanation: "Cross-plane angular velocity exceeds the terminal's slew rate limit (typically at high latitudes)",
    });
  }
  if (reasons.los_blocked > 0) {
    items.push({
      label: "Earth occlusion",
      count: reasons.los_blocked,
      explanation: "Satellites on opposite sides of Earth — line of sight blocked by the planet",
    });
  }
  if (reasons.field_of_regard > 0) {
    items.push({
      label: "Field of regard",
      count: reasons.field_of_regard,
      explanation: "Peer satellite is outside the terminal's pointing cone",
    });
  }
  if (reasons.polar_seam > 0) {
    items.push({
      label: "Polar seam cutoff",
      count: reasons.polar_seam,
      explanation: "Hard latitude cutoff disables cross-plane ISLs at polar latitudes",
    });
  }
  if (reasons.terminal_exhausted > 0) {
    items.push({
      label: "Terminals exhausted",
      count: reasons.terminal_exhausted,
      explanation: "All ISL terminals allocated to higher-priority peers",
    });
  }

  return (
    <div className="wizard-preview-failure-breakdown">
      <h4 className="wizard-preview-breakdown-title">Why ISLs fail to form:</h4>
      {items.map(({ label, count, explanation }) => {
        const pct = ((count / total) * 100).toFixed(0);
        return (
          <div key={label} className="wizard-preview-failure-row">
            <div className="wizard-preview-failure-bar-bg">
              <div
                className="wizard-preview-failure-bar"
                style={{ width: `${(count / total) * 100}%` }}
              />
            </div>
            <span className="wizard-preview-failure-pct">{pct}%</span>
            <span className="wizard-preview-failure-label">{label}</span>
            <span className="wizard-preview-failure-explain">{explanation}</span>
          </div>
        );
      })}
    </div>
  );
}

export function CoveragePreview({ result, onContinue, onBack }: CoveragePreviewProps) {
  const { isl, ground_stations: gs } = result;

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
            <span className="wizard-preview-stat-value">{isl.formed_at_least_once}</span>
            <span className="wizard-preview-stat-label">of {isl.total_possible} ISLs form ({isl.feasibility_pct.toFixed(0)}%)</span>
          </div>
          <div className="wizard-preview-stat">
            <span className="wizard-preview-stat-value">{isl.min_active}&ndash;{isl.max_active}</span>
            <span className="wizard-preview-stat-label">active simultaneously</span>
          </div>
        </div>

        {/* ISL failure reason breakdown — the "WHY" */}
        {isl.failure_reasons && <IslFailureBreakdown reasons={isl.failure_reasons} />}
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

      {/* General warnings */}
      {result.warnings.length > 0 && (
        <div className="wizard-preview-warnings">
          {result.warnings.map((w, i) => (
            <div key={i} className="wizard-preview-warning">{w}</div>
          ))}
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
