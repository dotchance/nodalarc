/** Session card for catalog grid. */

import { useState } from "react";
import type { ManifestSession, ManifestScenario } from "./catalogTypes";

interface SessionCardProps {
  session: ManifestSession;
  scenarios: ManifestScenario[];
  isActive: boolean;
  deploying: boolean;
  onDeploy: (id: string) => void;
}

function stackBadgeClass(stack: string): string {
  if (stack.includes("isis")) return "catalog-stack-badge catalog-stack-badge--isis";
  if (stack.includes("ospf")) return "catalog-stack-badge catalog-stack-badge--ospf";
  if (stack.includes("static")) return "catalog-stack-badge catalog-stack-badge--static-sr";
  return "catalog-stack-badge";
}

function stackLabel(stack: string): string {
  if (stack.includes("isis")) return "IS-IS";
  if (stack.includes("ospf")) return "OSPF";
  if (stack.includes("static")) return "Static SR";
  return stack;
}

export function SessionCard({ session, scenarios, isActive, deploying, onDeploy }: SessionCardProps) {
  const [scenariosOpen, setScenariosOpen] = useState(false);

  const compatibleScenarios = scenarios.filter(
    (sc) => sc.compatible_sessions.includes("all") || sc.compatible_sessions.includes(session.id)
  );

  let btnClass = "catalog-deploy-btn";
  let btnText = "Deploy";
  if (isActive) {
    btnClass += " catalog-deploy-btn--running";
    btnText = "Running";
  } else if (deploying) {
    btnClass += " catalog-deploy-btn--deploying";
    btnText = "Deploying...";
  }

  return (
    <div className={`catalog-card ${isActive ? "catalog-card--active" : ""}`}>
      <div className="catalog-card-header">
        <h3>{session.name}</h3>
        <span className={stackBadgeClass(session.routing_stack)}>
          {stackLabel(session.routing_stack)}
        </span>
      </div>

      <div className="catalog-card-stats">
        <span className="stat-satellites">{session.satellite_count} sats</span>
        <span>{session.constellation}</span>
        <span>{session.ground_station_set}</span>
      </div>

      <div className="catalog-card-description">{session.description}</div>

      <div className="catalog-card-tags">
        {session.tags.map((tag) => (
          <span key={tag}>{tag}</span>
        ))}
      </div>

      <button
        className={btnClass}
        onClick={() => onDeploy(session.id)}
        disabled={deploying}
      >
        {btnText}
      </button>

      {compatibleScenarios.length > 0 && (
        <>
          <button
            className="catalog-scenario-toggle"
            onClick={() => setScenariosOpen((v) => !v)}
          >
            <span style={{ display: "inline-block", transform: scenariosOpen ? "rotate(90deg)" : "none", transition: "transform 0.15s" }}>
              &#9654;
            </span>
            {compatibleScenarios.length} compatible scenario{compatibleScenarios.length !== 1 ? "s" : ""}
          </button>

          {scenariosOpen && (
            <div className="catalog-scenario-list">
              {compatibleScenarios.map((sc) => (
                <div key={sc.id} className="catalog-scenario-item">
                  <span className="scenario-name">{sc.name}</span>
                  <span className="scenario-duration">{sc.duration_minutes}m</span>
                  <span className="scenario-tags">
                    {sc.tags.map((t) => (
                      <span key={t}>{t}</span>
                    ))}
                  </span>
                  <span className="scenario-desc">{sc.description}</span>
                </div>
              ))}
            </div>
          )}
        </>
      )}
    </div>
  );
}
