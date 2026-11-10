// Copyright 2024-2026 .chance (dotchance)
// SPDX-License-Identifier: AGPL-3.0-only
/** Session editor — the session-level plumbing: identity, time, and the
 *  candidate-math budget.
 *
 *  Each card edits one section of the session document (`/session`, `/time`,
 *  `/simulation`) as that section's pending edit; Apply accepts them. The
 *  session's name is its identity, which only Save under a new name changes.
 *
 *  Time rate is the invariant: step 1s / compression 1 means sim time is
 *  wall time; anything else is an explicit manipulation the user typed.
 *  The candidate budget is required grammar once link rules exist — the
 *  fields live here so the numbers are the user's, never a hidden default.
 */
import { Button } from "../ui/Button";
import { EditorCard, Field, NumberField } from "./editorKit";
import type { JsonValue } from "./generated/builderApi";

type JsonObject = Readonly<Record<string, JsonValue>>;
export type SessionSectionPointer = "/session" | "/time" | "/simulation";

interface SessionEditorProps {
  /** The session's identity; Save under a new name changes it. */
  name: string;
  /** Each section as its pending edit holds it, or as the session holds it. */
  meta: JsonObject;
  time: JsonObject;
  simulation: JsonObject | null;
  /** Whether any of the three sections holds a pending edit. */
  pending: boolean;
  onStage: (pointer: SessionSectionPointer, working: JsonObject) => void;
  onApply: () => void;
  onDiscard: () => void;
}

function numberOf(value: JsonValue | undefined): number | null {
  return typeof value === "number" ? value : null;
}

function textOf(value: JsonValue | undefined): string {
  return typeof value === "string" ? value : "";
}

function objectOf(value: JsonValue | undefined): JsonObject {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    ? (value as JsonObject)
    : {};
}

export function timeRateSummary(
  stepSeconds: number | null,
  compression: number | null,
): string {
  if (stepSeconds === null || compression === null) return "time rate incomplete";
  if (stepSeconds === 1 && compression === 1) return "real time";
  return `step ${stepSeconds}s · ×${compression}`;
}

export function SessionEditor({
  name,
  meta,
  time,
  simulation,
  pending,
  onStage,
  onApply,
  onDiscard,
}: SessionEditorProps) {
  const stepSeconds = numberOf(time.step_seconds);
  const compression = numberOf(time.compression);
  const limits = objectOf(simulation?.candidate_limits);
  const perRule = numberOf(limits.max_pairs_per_rule);
  const perTick = numberOf(limits.max_pairs_per_tick);
  const stageLimits = (patch: Record<string, JsonValue>) =>
    onStage("/simulation", { ...(simulation ?? {}), candidate_limits: { ...limits, ...patch } });
  return (
    <div className="builder-inspector-stack" data-testid="builder-session-editor">
      <div className="builder-site-derived">
        name {name} — Save under a new name renames the session
      </div>
      <Field
        label="display name"
        value={textOf(meta.display_name)}
        onChange={(display_name) => onStage("/session", { ...meta, display_name })}
      />
      <Field
        label="description"
        value={textOf(meta.description)}
        onChange={(description) => onStage("/session", { ...meta, description })}
      />
      <Field
        label="start time"
        value={textOf(time.start_time)}
        onChange={(start_time) => onStage("/time", { ...time, start_time: start_time.trim() })}
      />
      <EditorCard title="Time rate" open summary={timeRateSummary(stepSeconds, compression)}>
        <NumberField
          label="sim step"
          value={stepSeconds}
          min={0.1}
          suffix="s"
          onChange={(step_seconds) => onStage("/time", { ...time, step_seconds })}
        />
        <NumberField
          label="compression"
          value={compression}
          min={0.1}
          suffix="× wall clock"
          onChange={(value) => onStage("/time", { ...time, compression: value })}
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
          perRule === null || perTick === null
            ? "candidate budget incomplete"
            : `${perRule} / rule · ${perTick} / tick`
        }
      >
        <NumberField
          label="max pairs per rule"
          value={perRule}
          min={1}
          onChange={(value) => stageLimits({ max_pairs_per_rule: Math.round(value) })}
        />
        <NumberField
          label="max pairs per tick"
          value={perTick}
          min={1}
          onChange={(value) => stageLimits({ max_pairs_per_tick: Math.round(value) })}
        />
        <div className="builder-site-derived">
          declared with the session once link rules exist — the resolver
          refuses undeclared candidate math
        </div>
      </EditorCard>
      {pending && (
        <div className="builder-preset-row">
          <Button variant="primary" onClick={onApply}>Apply</Button>
          <Button onClick={onDiscard}>Discard</Button>
        </div>
      )}
    </div>
  );
}
