// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
/** Read-only spec-sheet inspector for a selected builder-world node.
 *
 *  Progressive disclosure: summary cards, one open at a time. A closed card's
 *  summary line reads as a spec sheet; controls and detail rows exist only
 *  inside the open card. Facts come from the resolved world (wire models
 *  verbatim) and the session ephemeris; derived readouts (altitude, period)
 *  are presentation only and computed from ephemeris physical facts.
 */

import { useState } from "react";
import { KeyValueRow } from "../ui/KeyValueRow";
import { EditorCard } from "./editorKit";
import type { EphemerisNode, SessionEphemeris } from "../sim/ephemeris";
import type { BuilderWorldNode } from "./builderTypes";

interface BuilderInspectorProps {
  node: BuilderWorldNode;
  ephemeris: SessionEphemeris;
}

function orbitSummary(entry: EphemerisNode, meanRadiusKm: number): string {
  if (entry.type !== "keplerian") return "—";
  const alt = entry.semi_major_axis_km - meanRadiusKm;
  const shape = entry.eccentricity < 0.01 ? "circular" : `e=${entry.eccentricity.toFixed(3)}`;
  return `${Math.round(alt)} km ${shape} · ${entry.inclination_deg.toFixed(1)}°`;
}

function periodMinutes(semiMajorAxisKm: number, muKm3S2: number): number {
  return (2 * Math.PI * Math.sqrt(semiMajorAxisKm ** 3 / muKm3S2)) / 60;
}

export function BuilderInspector({ node, ephemeris }: BuilderInspectorProps) {
  const [openCard, setOpenCard] = useState<string | null>(null);
  const toggle = (id: string) => setOpenCard((prev) => (prev === id ? null : id));

  const entry = ephemeris.nodes[node.node_id];
  const frame =
    entry && entry.reference_body ? ephemeris.body_frames[entry.reference_body] : undefined;

  const terminalCount = node.terminal_inventory.reduce((sum, b) => sum + b.count, 0);
  const hardwareSummary = node.terminal_inventory.length
    ? node.terminal_inventory.map((b) => `${b.endpoint_role} ×${b.count}`).join(" · ")
    : "no terminals";
  const lo0 = node.interfaces?.lo0;
  const networkSummary = [node.forwarding ?? undefined, lo0?.ipv4 ?? lo0?.ipv6 ?? undefined]
    .filter(Boolean)
    .join(" · ");

  return (
    <div className="builder-inspector-stack" data-testid="builder-inspector-node">
      <div className="builder-inspector-name">{node.node_id}</div>
      <div className="builder-inspector-sub">
        {node.kind} · {node.segment_id}
        {node.tags.length > 0 && ` · ${node.tags.join(", ")}`}
      </div>

      {node.kind === "satellite" && entry?.type === "keplerian" && frame && (
        <EditorCard
          title="Orbit"
          summary={orbitSummary(entry, frame.mean_radius_km)}
          open={openCard === "orbit"}
          onToggle={() => toggle("orbit")}
        >
          <KeyValueRow label="semi-major axis">
            {entry.semi_major_axis_km.toFixed(1)} km
          </KeyValueRow>
          <KeyValueRow label="eccentricity">{entry.eccentricity.toFixed(4)}</KeyValueRow>
          <KeyValueRow label="inclination">{entry.inclination_deg.toFixed(2)}°</KeyValueRow>
          <KeyValueRow label="RAAN">{entry.raan_deg.toFixed(2)}°</KeyValueRow>
          <KeyValueRow label="arg of perigee">
            {entry.argument_of_perigee_deg.toFixed(2)}°
          </KeyValueRow>
          <KeyValueRow label="mean anomaly">{entry.mean_anomaly_deg.toFixed(2)}°</KeyValueRow>
          <KeyValueRow label="period (derived)">
            {periodMinutes(
              entry.semi_major_axis_km,
              frame.gravitational_parameter_km3_s2,
            ).toFixed(1)}{" "}
            min
          </KeyValueRow>
          <KeyValueRow label="propagator">{entry.propagator}</KeyValueRow>
          {node.plane !== null && (
            <KeyValueRow label="plane / slot">
              {node.plane} / {node.slot}
            </KeyValueRow>
          )}
        </EditorCard>
      )}

      {node.surface_position && (
        <EditorCard
          title="Position"
          summary={`${node.surface_position.lat_deg.toFixed(2)}, ${node.surface_position.lon_deg.toFixed(2)} · ${node.surface_position.body}`}
          open={openCard === "position"}
          onToggle={() => toggle("position")}
        >
          <KeyValueRow label="latitude">{node.surface_position.lat_deg.toFixed(4)}°</KeyValueRow>
          <KeyValueRow label="longitude">{node.surface_position.lon_deg.toFixed(4)}°</KeyValueRow>
          <KeyValueRow label="altitude">{node.surface_position.alt_m.toFixed(0)} m</KeyValueRow>
          <KeyValueRow label="body">{node.surface_position.body}</KeyValueRow>
        </EditorCard>
      )}

      <EditorCard
        title="Hardware"
        summary={`${terminalCount} terminal${terminalCount === 1 ? "" : "s"} · ${hardwareSummary}`}
        open={openCard === "hardware"}
        onToggle={() => toggle("hardware")}
      >
        {node.terminal_inventory.map((block) => (
          <div className="builder-terminal-block" key={block.terminal_id}>
            <div className="builder-terminal-head">
              {block.terminal_id} · {block.endpoint_role} · {block.medium} ×{block.count}
            </div>
            {block.max_range_km !== null && (
              <KeyValueRow label="max range">{block.max_range_km.toFixed(0)} km</KeyValueRow>
            )}
            {block.bandwidth_mbps !== null && (
              <KeyValueRow label="bandwidth">{block.bandwidth_mbps.toFixed(0)} Mbps</KeyValueRow>
            )}
            {block.min_elevation_deg !== null && (
              <KeyValueRow label="min elevation">{block.min_elevation_deg.toFixed(1)}°</KeyValueRow>
            )}
            {block.tracking_capacity !== null && (
              <KeyValueRow label="tracking capacity">{block.tracking_capacity}</KeyValueRow>
            )}
          </div>
        ))}
      </EditorCard>

      {(node.forwarding || node.interfaces || node.originated_prefixes) && (
        <EditorCard
          title="Network"
          summary={networkSummary || "—"}
          open={openCard === "network"}
          onToggle={() => toggle("network")}
        >
          {node.forwarding && <KeyValueRow label="forwarding">{node.forwarding}</KeyValueRow>}
          {lo0?.ipv4 && <KeyValueRow label="lo0 ipv4">{lo0.ipv4}</KeyValueRow>}
          {lo0?.ipv6 && <KeyValueRow label="lo0 ipv6">{lo0.ipv6}</KeyValueRow>}
          {node.interfaces?.terr0?.ipv4 && (
            <KeyValueRow label="terr0 ipv4">{node.interfaces.terr0.ipv4}</KeyValueRow>
          )}
          {node.interfaces?.terr0?.ipv6 && (
            <KeyValueRow label="terr0 ipv6">{node.interfaces.terr0.ipv6}</KeyValueRow>
          )}
          {node.originated_prefixes?.ipv4?.map((prefix) => (
            <KeyValueRow label="originates" key={prefix}>
              {prefix}
            </KeyValueRow>
          ))}
          {node.originated_prefixes?.ipv6?.map((prefix) => (
            <KeyValueRow label="originates" key={prefix}>
              {prefix}
            </KeyValueRow>
          ))}
        </EditorCard>
      )}
    </div>
  );
}
