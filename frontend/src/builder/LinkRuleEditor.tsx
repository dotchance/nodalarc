// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
/** Link rule editor — comms intent between placed segments.
 *
 *  A rule declares who MAY link; OME computes feasibility from geometry,
 *  terminal limits, and runtime state. Role defaults seed the rule when two
 *  segments are connected (isl for a fabric, crosslink space↔space, access
 *  ground↔space); every value is then owned by the user. Tag scopes are how
 *  one ground segment serves multiple constellations differently. The LOS
 *  candidate preview on the canvas and the dark-rule notes come from the
 *  resolver's expansion of exactly what this editor emits.
 */

import { Button } from "../ui/Button";
import {
  linkWarnings,
  placedSegments,
  type DraftLinkEndpoint,
  type DraftLinkRule,
  type Workspace,
} from "./workspace";

interface LinkRuleEditorProps {
  workspace: Workspace;
  rule: DraftLinkRule;
  onUpdate: (patch: Partial<DraftLinkRule>) => void;
  onUpdateEndpoint: (side: "a" | "b", patch: Partial<DraftLinkEndpoint>) => void;
  onRemove: () => void;
}

function EndpointCard({
  title,
  endpoint,
  workspace,
  onUpdate,
}: {
  title: string;
  endpoint: DraftLinkEndpoint;
  workspace: Workspace;
  onUpdate: (patch: Partial<DraftLinkEndpoint>) => void;
}) {
  const placed = placedSegments(workspace);
  return (
    <div className="builder-card builder-card--open">
      <div className="builder-card-head">
        <span className="builder-card-title">{title}</span>
        <span className="builder-card-summary">
          {endpoint.role} · {endpoint.medium}
          {endpoint.tag ? ` · tag ${endpoint.tag}` : ""}
        </span>
      </div>
      <div className="builder-card-body">
        <label className="builder-field builder-field--stack">
          <span className="builder-field-label">segment</span>
          <select
            aria-label={`${title} segment`}
            value={endpoint.segment_id}
            onChange={(e) => onUpdate({ segment_id: e.target.value })}
          >
            {placed.map((segment) => (
              <option key={segment.segment_id} value={segment.segment_id}>
                {segment.label} ({segment.kind})
              </option>
            ))}
            {!placed.some((s) => s.segment_id === endpoint.segment_id) && (
              <option value={endpoint.segment_id}>
                {endpoint.segment_id} (removed)
              </option>
            )}
          </select>
        </label>
        <label className="builder-field">
          <span className="builder-field-label">scope to tag</span>
          <span className="builder-field-input">
            <input
              type="text"
              placeholder="every node in the segment"
              value={endpoint.tag ?? ""}
              onChange={(e) => onUpdate({ tag: e.target.value.trim() || null })}
            />
          </span>
        </label>
        <label className="builder-field">
          <span className="builder-field-label">terminal role</span>
          <span className="builder-field-input">
            <select
              aria-label={`${title} terminal role`}
              value={endpoint.role}
              onChange={(e) =>
                onUpdate({ role: e.target.value as DraftLinkEndpoint["role"] })
              }
            >
              <option value="access">access</option>
              <option value="isl">isl</option>
              <option value="crosslink">crosslink</option>
            </select>
          </span>
        </label>
        <label className="builder-field">
          <span className="builder-field-label">medium</span>
          <span className="builder-field-input">
            <select
              aria-label={`${title} medium`}
              value={endpoint.medium}
              onChange={(e) =>
                onUpdate({ medium: e.target.value as DraftLinkEndpoint["medium"] })
              }
            >
              <option value="rf">rf</option>
              <option value="optical">optical</option>
            </select>
          </span>
        </label>
        <label className="builder-field">
          <span className="builder-field-label">min elevation</span>
          <span className="builder-field-input">
            <input
              type="number"
              placeholder="none"
              value={endpoint.min_elevation_deg ?? ""}
              onChange={(e) => {
                const value = e.target.value;
                if (value === "") {
                  onUpdate({ min_elevation_deg: null });
                } else {
                  const parsed = Number(value);
                  if (Number.isFinite(parsed)) onUpdate({ min_elevation_deg: parsed });
                }
              }}
            />
            <span className="builder-field-suffix">deg</span>
          </span>
        </label>
      </div>
    </div>
  );
}

export function LinkRuleEditor({
  workspace,
  rule,
  onUpdate,
  onUpdateEndpoint,
  onRemove,
}: LinkRuleEditorProps) {
  const warnings = linkWarnings(workspace);
  return (
    <div className="builder-inspector-stack" data-testid="builder-link-editor">
      <label className="builder-field">
        <span className="builder-field-label">name</span>
        <span className="builder-field-input">
          <input
            type="text"
            value={rule.label}
            onChange={(e) => onUpdate({ label: e.target.value })}
          />
        </span>
      </label>

      <EndpointCard
        title="Endpoint A"
        endpoint={rule.a}
        workspace={workspace}
        onUpdate={(patch) => onUpdateEndpoint("a", patch)}
      />
      <EndpointCard
        title="Endpoint B"
        endpoint={rule.b}
        workspace={workspace}
        onUpdate={(patch) => onUpdateEndpoint("b", patch)}
      />

      <div className="builder-preset-row" role="radiogroup" aria-label="Topology">
        <Button
          active={rule.topology_mode === "visible_candidates"}
          onClick={() => onUpdate({ topology_mode: "visible_candidates" })}
        >
          all visible pairs
        </Button>
        <Button
          active={rule.topology_mode === "nearest_n"}
          onClick={() => onUpdate({ topology_mode: "nearest_n" })}
        >
          nearest N
        </Button>
      </div>
      {rule.topology_mode === "nearest_n" && (
        <label className="builder-field">
          <span className="builder-field-label">N</span>
          <span className="builder-field-input">
            <input
              type="number"
              min={1}
              value={rule.topology_n}
              onChange={(e) => {
                const parsed = Math.max(1, Math.round(Number(e.target.value)));
                if (Number.isFinite(parsed)) onUpdate({ topology_n: parsed });
              }}
            />
            <span className="builder-field-suffix">neighbors</span>
          </span>
        </label>
      )}
      <label className="builder-field">
        <span className="builder-field-label">max range</span>
        <span className="builder-field-input">
          <input
            type="number"
            placeholder="unlimited"
            value={rule.max_range_km ?? ""}
            onChange={(e) => {
              const value = e.target.value;
              if (value === "") {
                onUpdate({ max_range_km: null });
              } else {
                const parsed = Number(value);
                if (Number.isFinite(parsed) && parsed > 0) {
                  onUpdate({ max_range_km: parsed });
                }
              }
            }}
          />
          <span className="builder-field-suffix">km</span>
        </span>
      </label>
      <div className="builder-preset-row">
        <Button active={rule.enabled} onClick={() => onUpdate({ enabled: !rule.enabled })}>
          {rule.enabled ? "enabled" : "disabled"}
        </Button>
        <Button variant="danger" onClick={onRemove}>
          Remove rule
        </Button>
      </div>

      {warnings.map((warning) => (
        <div className="builder-warning" key={warning}>
          {warning}
        </div>
      ))}
    </div>
  );
}
