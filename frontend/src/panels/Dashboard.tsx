// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
// Dashboard — session health at a glance: large metrics, convergence state,
// and live fault rows. Values are neutral; tone appears only where a fact is
// healthy/degraded/faulted (color-slot law).

import { useMemo } from "react";
import { schedulerOpsLabel } from "../explain/reasons";
import type { StateSnapshot } from "../types";
import { isGroundLinkState } from "../networkIdentity";

interface DashboardProps {
  snapshot: StateSnapshot | null;
}

export function Dashboard({ snapshot }: DashboardProps) {
  const stats = useMemo(() => {
    if (!snapshot) return null;

    const satCount = snapshot.nodes.filter((n) => n.node_type === "satellite").length;
    const gsCount = snapshot.nodes.filter((n) => n.node_type === "ground_station").length;
    const gsLinks = snapshot.links.filter((l) => isGroundLinkState(l)).length;
    const islLinks = snapshot.links.length - gsLinks;
    const activeLinks = snapshot.links.filter((l) => l.state === "active").length;

    let simTimeStr = "";
    try {
      const d = new Date(snapshot.sim_time);
      simTimeStr = d.toISOString().replace("T", " ").substring(0, 19) + " UTC";
    } catch {
      simTimeStr = snapshot.sim_time;
    }

    return {
      satCount,
      gsCount,
      islLinks,
      gsLinks,
      activeLinks,
      totalLinks: snapshot.links.length,
      simTime: simTimeStr,
      constellation: snapshot.constellation_name ?? "—",
    };
  }, [snapshot]);

  if (!snapshot || !stats) {
    return (
      <div className="dashboard">
        <div className="dashboard-empty">No active session</div>
      </div>
    );
  }

  const health = snapshot.network_health;
  const healthTone =
    health.status === "converged" ? "ok" : health.status === "converging" ? "warn" : "fail";
  const notices = snapshot.actuation_notices ?? [];

  return (
    <div className="dashboard">
      <div className="dashboard-head">
        <div>
          <h2 className="dashboard-title">{stats.constellation}</h2>
          <div className="dashboard-time">{stats.simTime}</div>
        </div>
        <span className={`dashboard-state dashboard-state--${healthTone}`}>{health.status}</span>
      </div>

      <div className="dashboard-grid">
        <DashCard label="Satellites" value={stats.satCount} />
        <DashCard label="Ground Nodes" value={stats.gsCount} />
        <DashCard label="ISL Links" value={stats.islLinks} />
        <DashCard label="Ground Links" value={stats.gsLinks} />
        <DashCard label="Active Links" value={stats.activeLinks} sub={`/ ${stats.totalLinks}`} />
        <DashCard
          label="Actuation Faults"
          value={notices.length}
          tone={notices.length > 0 ? "fail" : "ok"}
        />
      </div>

      <div className="dashboard-feed">
        {health.status === "degraded" && (health.unreachable_flows ?? 0) > 0 && (
          <div className="dashboard-alert dashboard-alert--fail">
            {health.unreachable_flows} flows unreachable
          </div>
        )}
        {notices.map((n) => (
          <div
            key={`${n.gs_id}:${n.reason_code}`}
            className={`dashboard-alert dashboard-alert--${n.actuation_state === "kernel_dirty" ? "fail" : "warn"}`}
          >
            {n.gs_id}: {schedulerOpsLabel(n.reason_code)}
          </div>
        ))}
        {notices.length === 0 && health.status === "converged" && (
          <div className="dashboard-alert dashboard-alert--ok">All actuation clean; routing converged</div>
        )}
      </div>
    </div>
  );
}

function DashCard({ label, value, sub, tone }: {
  label: string;
  value: number;
  sub?: string;
  tone?: "ok" | "fail";
}) {
  return (
    <div className="dashboard-card">
      <div className={`dashboard-card-value${tone ? ` dashboard-card-value--${tone}` : ""}`}>
        {value}{sub && <span className="dashboard-card-sub">{sub}</span>}
      </div>
      <div className="dashboard-card-label">{label}</div>
    </div>
  );
}
