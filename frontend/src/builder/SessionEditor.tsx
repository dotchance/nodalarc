// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
/** Session editor — the session-level plumbing: identity, time, and the
 *  candidate-math budget.
 *
 *  Time rate is the invariant: step 1s / compression 1 means sim time IS
 *  wall time; anything else is an explicit manipulation the user typed.
 *  The candidate budget is REQUIRED grammar once link rules exist — the
 *  fields live here so the numbers are the user's, never a hidden default.
 */

import type { Workspace } from "./workspace";

interface SessionEditorProps {
  workspace: Workspace;
  onUpdate: (patch: Partial<Workspace>) => void;
}

function NumberField({
  label,
  value,
  min,
  onChange,
  suffix,
}: {
  label: string;
  value: number;
  min: number;
  onChange: (value: number) => void;
  suffix?: string;
}) {
  return (
    <label className="builder-field">
      <span className="builder-field-label">{label}</span>
      <span className="builder-field-input">
        <input
          type="number"
          min={min}
          value={value}
          onChange={(e) => {
            const parsed = Number(e.target.value);
            if (Number.isFinite(parsed) && parsed >= min) onChange(parsed);
          }}
        />
        {suffix && <span className="builder-field-suffix">{suffix}</span>}
      </span>
    </label>
  );
}

export function SessionEditor({ workspace, onUpdate }: SessionEditorProps) {
  return (
    <div className="builder-inspector-stack" data-testid="builder-session-editor">
      <label className="builder-field">
        <span className="builder-field-label">name</span>
        <span className="builder-field-input">
          <input
            type="text"
            value={workspace.name}
            onChange={(e) => onUpdate({ name: e.target.value })}
          />
        </span>
      </label>
      <label className="builder-field">
        <span className="builder-field-label">start time</span>
        <span className="builder-field-input">
          <input
            type="text"
            value={workspace.start_time}
            onChange={(e) => onUpdate({ start_time: e.target.value.trim() })}
          />
        </span>
      </label>
      <div className="builder-card builder-card--open">
        <div className="builder-card-head">
          <span className="builder-card-title">Time rate</span>
          <span className="builder-card-summary">
            {workspace.step_seconds === 1 && workspace.compression === 1
              ? "real time"
              : `step ${workspace.step_seconds}s · ×${workspace.compression}`}
          </span>
        </div>
        <div className="builder-card-body">
          <NumberField
            label="sim step"
            value={workspace.step_seconds}
            min={0.1}
            suffix="s"
            onChange={(step_seconds) => onUpdate({ step_seconds })}
          />
          <NumberField
            label="compression"
            value={workspace.compression}
            min={0.1}
            suffix="× wall clock"
            onChange={(compression) => onUpdate({ compression })}
          />
          <div className="builder-site-derived">
            1s step at ×1 is real time — any other rate is an explicit time
            manipulation carried in the artifact
          </div>
        </div>
      </div>
      <div className="builder-card builder-card--open">
        <div className="builder-card-head">
          <span className="builder-card-title">Candidate budget</span>
          <span className="builder-card-summary">
            {workspace.max_pairs_per_rule} / rule · {workspace.max_pairs_per_tick} / tick
          </span>
        </div>
        <div className="builder-card-body">
          <NumberField
            label="max pairs per rule"
            value={workspace.max_pairs_per_rule}
            min={1}
            onChange={(max_pairs_per_rule) =>
              onUpdate({ max_pairs_per_rule: Math.round(max_pairs_per_rule) })
            }
          />
          <NumberField
            label="max pairs per tick"
            value={workspace.max_pairs_per_tick}
            min={1}
            onChange={(max_pairs_per_tick) =>
              onUpdate({ max_pairs_per_tick: Math.round(max_pairs_per_tick) })
            }
          />
          <div className="builder-site-derived">
            declared with the session once link rules exist — the resolver
            refuses undeclared candidate math
          </div>
        </div>
      </div>
    </div>
  );
}
