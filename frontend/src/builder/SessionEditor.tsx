// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
/** Session editor — the session-level plumbing: identity, time, and the
 *  candidate-math budget.
 *
 *  Time rate is the invariant: step 1s / compression 1 means sim time is
 *  wall time; anything else is an explicit manipulation the user typed.
 *  The candidate budget is required grammar once link rules exist — the
 *  fields live here so the numbers are the user's, never a hidden default.
 */

import { EditorCard, EditorName, Field, NumberField } from "./editorKit";
import type { Workspace } from "./workspace";

interface SessionEditorProps {
  workspace: Workspace;
  onUpdate: (patch: Partial<Workspace>) => void;
}

export function SessionEditor({ workspace, onUpdate }: SessionEditorProps) {
  return (
    <div className="builder-inspector-stack" data-testid="builder-session-editor">
      <EditorName value={workspace.name} onChange={(name) => onUpdate({ name })} />
      <Field
        label="start time"
        value={workspace.start_time}
        onChange={(start_time) => onUpdate({ start_time: start_time.trim() })}
      />
      <EditorCard
        title="Time rate"
        open
        summary={
          workspace.step_seconds === 1 && workspace.compression === 1
            ? "real time"
            : `step ${workspace.step_seconds}s · ×${workspace.compression}`
        }
      >
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
            manipulation carried in the session document
          </div>
      </EditorCard>
      <EditorCard
        title="Candidate budget"
        open
        summary={
          <>
            {workspace.max_pairs_per_rule} / rule · {workspace.max_pairs_per_tick} / tick
          </>
        }
      >
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
      </EditorCard>
    </div>
  );
}
