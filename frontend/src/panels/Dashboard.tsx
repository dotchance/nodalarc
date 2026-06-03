// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
// Dashboard — aggregate health metrics at a glance.
//
// Summary cards: total links (ISL/GS), satellite count, GS count,
// active adjacencies, current sim_time.

import { useMemo } from "react";
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
      recentEvents: snapshot.recent_events?.length ?? 0,
      simTime: simTimeStr,
      constellation: snapshot.constellation_name ?? "—",
    };
  }, [snapshot]);

  if (!stats) {
    return (
      <div className="dashboard">
        <div className="dashboard-empty">No active session</div>
      </div>
    );
  }

  return (
    <div className="dashboard">
      <h2 className="dashboard-title">{stats.constellation}</h2>
      <div className="dashboard-time">{stats.simTime}</div>

      <div className="dashboard-grid">
        <DashCard label="Satellites" value={stats.satCount} color="var(--accent-teal)" />
        <DashCard label="Ground Stations" value={stats.gsCount} color="var(--accent-teal)" />
        <DashCard label="ISL Links" value={stats.islLinks} color="var(--accent-green)" />
        <DashCard label="Ground Links" value={stats.gsLinks} color="var(--accent-blue)" />
        <DashCard label="Active Links" value={stats.activeLinks} sub={`/ ${stats.totalLinks}`} color="var(--accent-green)" />
        <DashCard label="Recent Events" value={stats.recentEvents} color="var(--text-secondary)" />
      </div>
    </div>
  );
}

function DashCard({ label, value, sub, color }: {
  label: string;
  value: number;
  sub?: string;
  color: string;
}) {
  return (
    <div className="dashboard-card">
      <div className="dashboard-card-value" style={{ color }}>
        {value}{sub && <span className="dashboard-card-sub">{sub}</span>}
      </div>
      <div className="dashboard-card-label">{label}</div>
    </div>
  );
}
