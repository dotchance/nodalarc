// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
/**
 * L2: the combined envelope a user cannot infer by eye — configured vs effective
 * floor, and the dead-knob callout (e.g. a 25 deg mask made non-binding by a
 * FoR-derived 30 deg floor).
 */

import type { EffectiveEnvelopeFacts } from "../types";

function deg(v: number | null): string {
  return v == null ? "—" : `${Math.round(v * 10) / 10} deg`;
}

export function EffectiveEnvelopePanel({ envelope }: { envelope: EffectiveEnvelopeFacts }) {
  const maskDead = envelope.dead_knobs.includes("min_elevation_deg");
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
          {envelope.binding_source ? ` (${envelope.binding_source})` : ""}
        </span>
      </div>
      <div className="detail-row">
        <span className="detail-label">Max range</span>
        <span className="detail-value">
          {envelope.max_range_km == null ? "—" : `${Math.round(envelope.max_range_km)} km`}
        </span>
      </div>
      <div className="detail-row">
        <span className="detail-label">Field of regard</span>
        <span className="detail-value">
          {deg(envelope.field_of_regard_deg)}
          {envelope.boresight_mode ? ` (${envelope.boresight_mode})` : ""}
        </span>
      </div>
      <div className="detail-row">
        <span className="detail-label">Tracking rate</span>
        <span className="detail-value">
          {envelope.tracking_rate_deg_s == null
            ? "—"
            : `${Math.round(envelope.tracking_rate_deg_s * 10) / 10} deg/s`}
        </span>
      </div>
      {maskDead ? (
        <div className="env-deadknob">
          The {deg(envelope.configured_min_elevation_deg)} mask has no effect —{" "}
          {envelope.binding_source} sets the {deg(envelope.effective_min_elevation_deg)} floor.
        </div>
      ) : null}
    </div>
  );
}
