// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
/**
 * L2: the combined envelope a user cannot infer by eye — configured vs effective
 * floor and the dead-knob callout (e.g. a 25 deg mask made non-binding by a
 * FoR-derived 30 deg floor) — plus BOTH terminals' raw pointing/range constraints
 * with the binding terminal emphasized, so the "what to change" lever points at the
 * terminal that actually limits the link (the satellite can be the binding one).
 */

import { effectiveEnvelopeBindingLabel } from "../reasons";
import type { EffectiveEnvelopeFacts, EnvelopeEndpoint } from "../types";

function deg(v: number | null): string {
  return v == null ? "—" : `${Math.round(v * 10) / 10} deg`;
}

function TerminalEnvelope({ ep, binds }: { ep: EnvelopeEndpoint; binds: boolean }) {
  const label = ep.node_role === "ground" ? "Ground terminal" : "Satellite terminal";
  return (
    <div className={`env-terminal${binds ? " env-terminal--binding" : ""}`}>
      <div className="env-terminal-head">
        {label}
        {binds ? <span className="env-terminal-binds"> — binding</span> : null}
      </div>
      <div className="detail-row">
        <span className="detail-label">Boresight</span>
        <span className="detail-value">{ep.boresight_mode ?? "—"}</span>
      </div>
      <div className="detail-row">
        <span className="detail-label">Field of regard</span>
        <span className="detail-value">{deg(ep.field_of_regard_deg)}</span>
      </div>
      <div className="detail-row">
        <span className="detail-label">Tracking rate</span>
        <span className="detail-value">
          {ep.max_tracking_rate_deg_s == null
            ? "—"
            : `${Math.round(ep.max_tracking_rate_deg_s * 10) / 10} deg/s`}
        </span>
      </div>
      <div className="detail-row">
        <span className="detail-label">Max range</span>
        <span className="detail-value">
          {ep.max_range_km == null ? "—" : `${Math.round(ep.max_range_km)} km`}
        </span>
      </div>
    </div>
  );
}

export function EffectiveEnvelopePanel({ envelope }: { envelope: EffectiveEnvelopeFacts }) {
  const maskDead = envelope.dead_knobs.includes("min_elevation_deg");
  const be = envelope.binding_endpoint;
  const bindingLabel = effectiveEnvelopeBindingLabel(envelope.binding_source);
  return (
    <div className="env-panel">
      <div className="detail-row">
        <span className="detail-label">Configured min elevation</span>
        <span className={`detail-value${maskDead ? " detail-value--dead" : ""}`}>
          {deg(envelope.configured_min_elevation_deg)}
        </span>
      </div>
      <div className="detail-row">
        <span className="detail-label">Effective floor</span>
        <span className="detail-value">
          {deg(envelope.effective_min_elevation_deg)}
          {bindingLabel ? ` (${bindingLabel})` : ""}
        </span>
      </div>
      <div className="detail-row">
        <span className="detail-label">Max range (effective)</span>
        <span className="detail-value">
          {envelope.max_range_km == null ? "—" : `${Math.round(envelope.max_range_km)} km`}
        </span>
      </div>
      <TerminalEnvelope ep={envelope.ground} binds={be === "ground" || be === "both"} />
      <TerminalEnvelope ep={envelope.satellite} binds={be === "satellite" || be === "both"} />
      {maskDead ? (
        <div className="env-deadknob">
          The {deg(envelope.configured_min_elevation_deg)} mask has no effect —{" "}
          {bindingLabel ?? "A binding constraint"} sets the {deg(envelope.effective_min_elevation_deg)} floor.
        </div>
      ) : null}
    </div>
  );
}
