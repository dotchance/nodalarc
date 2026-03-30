/** Constellation selection panel — library presets + fallback session list.
 *
 * Extracted from SessionWizard.tsx with zero behavior change.
 */

import type { ConstellationPreset } from "./wizardTypes";
import type { SessionInfo } from "../types";

interface ConstellationPanelProps {
  presets: ConstellationPreset[];
  selected: ConstellationPreset | null;
  onSelect: (preset: ConstellationPreset) => void;
  /** Fallback: show VS-API sessions when presets fail to load. */
  fallbackSessions: SessionInfo[];
  deploying: boolean;
  onFallbackDeploy: (id: string) => void;
}

export function ConstellationPanel({
  presets,
  selected,
  onSelect,
  fallbackSessions,
  deploying,
  onFallbackDeploy,
}: ConstellationPanelProps) {
  if (presets.length === 0) {
    return (
      <div className="wizard-loading">
        {fallbackSessions.length > 0 ? (
          <>
            <p className="catalog-fallback-warning">
              Could not load wizard presets. Showing sessions from VS-API.
            </p>
            <div className="catalog-grid">
              {fallbackSessions.map((s) => (
                <div key={s.file} className={`catalog-card ${s.active ? "catalog-card--active" : ""}`}>
                  <div className="catalog-card-header"><h3>{s.name}</h3></div>
                  <div className="catalog-card-stats">
                    <span>{s.constellation}</span>
                    <span>{s.routing_stack}</span>
                  </div>
                  <button
                    className={`catalog-deploy-btn ${s.active ? "catalog-deploy-btn--running" : deploying ? "catalog-deploy-btn--deploying" : ""}`}
                    onClick={() => onFallbackDeploy(s.file.replace("configs/sessions/", "").replace(".yaml", ""))}
                    disabled={s.active || deploying}
                  >
                    {s.active ? "Running" : deploying ? "Deploying..." : "Deploy"}
                  </button>
                </div>
              ))}
            </div>
          </>
        ) : (
          <p>Loading presets...</p>
        )}
      </div>
    );
  }

  return (
    <div className="wizard-grid">
      {presets.map((p) => (
        <button
          key={p.name}
          className={`wizard-card ${selected?.name === p.name ? "wizard-card--selected" : ""}`}
          onClick={() => onSelect(p)}
        >
          <div className="wizard-card-title">{p.name}</div>
          <div className="wizard-card-stat">{p.satellite_count} satellites</div>
          <div className="wizard-card-desc">{p.description}</div>
        </button>
      ))}
    </div>
  );
}
