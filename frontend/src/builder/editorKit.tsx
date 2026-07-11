// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
/** The editor kit — the only source of editing controls in builder editors.
 *
 *  Interaction-grammar enforcement by construction: every editor
 *  composes these controls, so field anatomy, card anatomy, and control
 *  behavior cannot drift per surface. A raw <input>/<select>/<textarea> in
 *  an editor file fails the static conformance test.
 *
 *  EditorName carries create-focus (the name field is focused when a
 *  create gesture opens the editor; typing renames immediately).
 */

import { useEffect, useRef, useState, type ReactNode } from "react";
import { Button } from "../ui/Button";
import type { CatalogDocumentSummary } from "./generated/builderApi";

/** The name field every editor leads with. autoFocus fires on mount when
 *  the editor was opened by a create gesture. */
export function EditorName({
  value,
  onChange,
  autoFocus = false,
  label = "name",
}: {
  value: string;
  onChange: (value: string) => void;
  autoFocus?: boolean;
  label?: string;
}) {
  const ref = useRef<HTMLInputElement>(null);
  useEffect(() => {
    if (autoFocus) {
      ref.current?.focus();
      ref.current?.select();
    }
  }, [autoFocus]);
  return (
    <label className="builder-field">
      <span className="builder-field-label">{label}</span>
      <span className="builder-field-input">
        <input
          ref={ref}
          type="text"
          value={value}
          onChange={(e) => onChange(e.target.value)}
        />
      </span>
    </label>
  );
}

export function Field({
  label,
  value,
  onChange,
  placeholder,
  suffix,
  stack = false,
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  placeholder?: string;
  suffix?: string;
  stack?: boolean;
}) {
  const input = (
    <input
      type="text"
      placeholder={placeholder}
      value={value}
      onChange={(e) => onChange(e.target.value)}
    />
  );
  if (stack) {
    return (
      <label className="builder-field builder-field--stack">
        <span className="builder-field-label">{label}</span>
        {input}
      </label>
    );
  }
  return (
    <label className="builder-field">
      <span className="builder-field-label">{label}</span>
      <span className="builder-field-input">
        {input}
        {suffix && <span className="builder-field-suffix">{suffix}</span>}
      </span>
    </label>
  );
}

/** A number box that keeps a LOCAL STRING DRAFT so a value can be typed through
 *  interim states the committed value must never pass through — empty, a lone
 *  "-", a below-min figure mid-typing. The input binds the draft, not the value
 *  directly (deferred-clamp contract): `commit` fires only when the raw string
 *  is a parseable, in-range number; empty / "-" / below-min update the visible
 *  draft but commit NOTHING (so nothing reaches the buffer or the canvas
 *  preview). On blur the draft is dropped and the box re-syncs to the committed
 *  value — it never auto-commits 0 or the min. When `value` changes externally
 *  (an Apply/Defaults) and the box is not being edited, the draft is null so the
 *  box shows the new value. */
function useNumberDraft(
  value: number | null,
  commit: (raw: string) => void,
): { shown: string; handleChange: (raw: string) => void; handleBlur: () => void } {
  const [draft, setDraft] = useState<string | null>(null);
  const shown = draft ?? (value === null ? "" : String(value));
  return {
    shown,
    handleChange: (raw: string) => {
      setDraft(raw);
      commit(raw);
    },
    handleBlur: () => setDraft(null),
  };
}

export function NumberField({
  label,
  value,
  onChange,
  min,
  step,
  suffix,
  integer = false,
}: {
  label: string;
  value: number;
  onChange: (value: number) => void;
  min?: number;
  step?: number;
  suffix?: string;
  integer?: boolean;
}) {
  const { shown, handleChange, handleBlur } = useNumberDraft(value, (raw) => {
    if (raw === "") return;
    let parsed = Number(raw);
    if (!Number.isFinite(parsed)) return;
    if (integer) parsed = Math.round(parsed);
    if (min !== undefined && parsed < min) return;
    onChange(parsed);
  });
  return (
    <label className="builder-field">
      <span className="builder-field-label">{label}</span>
      <span className="builder-field-input">
        <input
          type="number"
          value={shown}
          min={min}
          step={step}
          onChange={(e) => handleChange(e.target.value)}
          onBlur={handleBlur}
        />
        {suffix && <span className="builder-field-suffix">{suffix}</span>}
      </span>
    </label>
  );
}

/** A slider with a typeable value box beside it. The track covers the common
 *  range and streams onChange continuously as it drags (the canvas preview
 *  moves with the thumb); it can never produce an out-of-range value. The box
 *  types past the track's MAX (values above the common range are valid), but
 *  min is the hard floor — a below-min figure types but does not commit
 *  (deferred-clamp contract, shared with NumberField). */
export function SliderField({
  label,
  value,
  onChange,
  min,
  max,
  step = 1,
  suffix,
  integer = false,
}: {
  label: string;
  value: number;
  onChange: (value: number) => void;
  min: number;
  max: number;
  step?: number;
  suffix?: string;
  integer?: boolean;
}) {
  const roundIfInt = (raw: string): number | null => {
    let parsed = Number(raw);
    if (!Number.isFinite(parsed)) return null;
    if (integer) parsed = Math.round(parsed);
    return parsed;
  };
  const box = useNumberDraft(value, (raw) => {
    if (raw === "") return;
    const parsed = roundIfInt(raw);
    if (parsed === null || parsed < min) return; // min floor deferred; no max ceiling
    onChange(parsed);
  });
  return (
    <label className="builder-field">
      <span className="builder-field-label">{label}</span>
      <span className="builder-field-input builder-slider-row">
        <input
          type="range"
          min={min}
          max={max}
          step={step}
          value={Math.min(max, Math.max(min, value))}
          aria-label={`${label} slider`}
          onChange={(e) => {
            const parsed = roundIfInt(e.target.value);
            if (parsed !== null) onChange(parsed);
          }}
        />
        <input
          type="number"
          value={box.shown}
          step={step}
          onChange={(e) => box.handleChange(e.target.value)}
          onBlur={box.handleBlur}
        />
        {suffix && <span className="builder-field-suffix">{suffix}</span>}
      </span>
    </label>
  );
}

/** A number that may be unset — empty input means null, shown as the
 *  placeholder ("none", "unlimited"). */
export function NullableNumberField({
  label,
  value,
  onChange,
  placeholder,
  suffix,
  min,
}: {
  label: string;
  value: number | null;
  onChange: (value: number | null) => void;
  placeholder: string;
  suffix?: string;
  min?: number;
}) {
  const { shown, handleChange, handleBlur } = useNumberDraft(value, (raw) => {
    if (raw === "") {
      onChange(null); // empty → null is this field's commit, not a no-op (pinned)
      return;
    }
    const parsed = Number(raw);
    if (!Number.isFinite(parsed)) return;
    if (min !== undefined && parsed < min) return;
    onChange(parsed);
  });
  return (
    <label className="builder-field">
      <span className="builder-field-label">{label}</span>
      <span className="builder-field-input">
        <input
          type="number"
          placeholder={placeholder}
          value={shown}
          onChange={(e) => handleChange(e.target.value)}
          onBlur={handleBlur}
        />
        {suffix && <span className="builder-field-suffix">{suffix}</span>}
      </span>
    </label>
  );
}

export interface SelectOption {
  value: string;
  label: string;
  /** Disabled options stay visible with the reason in their title —
   *  honesty about what cannot be formed, not a hidden restriction. */
  disabled?: boolean;
  title?: string;
}

export function SelectField({
  label,
  ariaLabel,
  value,
  onChange,
  options,
  stack = false,
}: {
  label: string;
  ariaLabel?: string;
  value: string;
  onChange: (value: string) => void;
  options: SelectOption[];
  stack?: boolean;
}) {
  const select = (
    <select
      aria-label={ariaLabel ?? label}
      value={value}
      onChange={(e) => onChange(e.target.value)}
    >
      {options.map((option) => (
        <option
          key={option.value}
          value={option.value}
          disabled={option.disabled}
          title={option.title}
        >
          {option.label}
        </option>
      ))}
    </select>
  );
  if (stack) {
    return (
      <label className="builder-field builder-field--stack">
        <span className="builder-field-label">{label}</span>
        {select}
      </label>
    );
  }
  return (
    <label className="builder-field">
      <span className="builder-field-label">{label}</span>
      <span className="builder-field-input">{select}</span>
    </label>
  );
}

/** The one bodies-picker: a SelectField over the bodies catalog, hardened for a
 *  failed or still-loading catalog. The current value is ALWAYS an option, so the
 *  field never blanks an existing body — before the catalog loads, on error, or
 *  when the value is not (yet) in the catalog. On a catalog error it renders the
 *  verbatim message and a retry wired to the hook's refresh (the same failure
 *  contract uses), never a bare frozen select. */
export function BodySelect({
  label,
  ariaLabel,
  value,
  onChange,
  bodies,
  stack = false,
}: {
  label: string;
  ariaLabel: string;
  value: string;
  onChange: (value: string) => void;
  bodies: {
    entries: CatalogDocumentSummary[];
    error: string | null;
    refresh: () => Promise<void>;
  };
  stack?: boolean;
}) {
  const loaded: SelectOption[] = bodies.entries
    .map((entry) => ({ value: entry.ref, label: entry.display_name }));
  const options: SelectOption[] = loaded.some((option) => option.value === value)
    ? loaded
    : [{ value, label: value }, ...loaded];
  return (
    <>
      <SelectField
        label={label}
        ariaLabel={ariaLabel}
        value={value}
        onChange={onChange}
        options={options}
        stack={stack}
      />
      {bodies.error && (
        <div className="builder-warning" data-testid="bodies-catalog-error">
          bodies catalog unavailable — {bodies.error}{" "}
          <Button onClick={() => void bodies.refresh()}>retry</Button>
        </div>
      )}
    </>
  );
}

export function CheckboxField({
  label,
  checked,
  onChange,
}: {
  label: string;
  checked: boolean;
  onChange: (checked: boolean) => void;
}) {
  return (
    <label className="builder-field">
      <span className="builder-field-label">{label}</span>
      <span className="builder-field-input">
        <input
          type="checkbox"
          checked={checked}
          onChange={(e) => onChange(e.target.checked)}
        />
      </span>
    </label>
  );
}

/** The one card anatomy: title + spec-sheet summary; closed cards
 *  read as the object's spec. Accordion when onToggle is given (the head is a
 *  <button>); static (always open) when not. `actions` — a header control such
 *  as a Remove button — is allowed only on the STATIC card: interactive content
 *  must never nest inside the accordion's <button> head, so the type forbids
 *  actions together with onToggle. */
type EditorCardProps = {
  title: string;
  summary?: ReactNode;
  open: boolean;
  children?: ReactNode;
} & (
  | { onToggle: () => void; actions?: never }
  | { onToggle?: undefined; actions?: ReactNode }
);

export function EditorCard({ title, summary, open, onToggle, actions, children }: EditorCardProps) {
  const head = (
    <>
      <span className="builder-card-title">{title}</span>
      {summary !== undefined && <span className="builder-card-summary">{summary}</span>}
    </>
  );
  return (
    <div className={`builder-card${open ? " builder-card--open" : ""}`}>
      {onToggle ? (
        <button className="builder-card-head" onClick={onToggle}>
          {head}
        </button>
      ) : (
        <div className="builder-card-head">
          {head}
          {actions}
        </div>
      )}
      {open && <div className="builder-card-body">{children}</div>}
    </div>
  );
}

export function InlineSelect({
  ariaLabel,
  title,
  className,
  value,
  onChange,
  options,
}: {
  ariaLabel: string;
  title?: string;
  className?: string;
  value: string;
  onChange: (value: string) => void;
  options: SelectOption[];
}) {
  return (
    <select
      className={className}
      aria-label={ariaLabel}
      title={title}
      value={value}
      onChange={(e) => onChange(e.target.value)}
    >
      {options.map((option) => (
        <option key={option.value} value={option.value}>
          {option.label}
        </option>
      ))}
    </select>
  );
}

/** Bulk paste surface (sites and their kin): mono, resizable, one action. */
export function PasteArea({
  value,
  onChange,
  placeholder,
  rows = 3,
}: {
  value: string;
  onChange: (value: string) => void;
  placeholder?: string;
  rows?: number;
}) {
  return (
    <textarea
      className="builder-paste"
      placeholder={placeholder}
      value={value}
      onChange={(e) => onChange(e.target.value)}
      rows={rows}
    />
  );
}

/** The commit row every buffered editor window ends with. A window edits a
 *  working copy; nothing reaches the session until Apply (or OK, which
 *  applies and closes). Cancel closes without applying — the same outcome as
 *  the title-bar X, but said out loud. Defaults returns the window to its
 *  baseline: the values at window open, advanced to the applied draft on each
 *  Apply. The state label answers the question the buttons exist for: "did my
 *  typing take?" */
export function EditorApplyRow({
  dirty,
  stale = false,
  onApply,
  onOk,
  onDefaults,
  onLoadCurrent,
  onCancel,
}: {
  dirty: boolean;
  stale?: boolean;
  onApply: () => void;
  onOk: () => void;
  onDefaults: () => void;
  onLoadCurrent?: () => void;
  onCancel: () => void;
}) {
  return (
    <>
      {stale && (
        <div className="builder-stale-notice" data-testid="builder-stale-notice">
          <span className="builder-stale-notice-text">
            The saved values changed since you started editing. Apply to keep your
            edits, or load the current values.
          </span>
          {onLoadCurrent && (
            <Button
              onClick={onLoadCurrent}
              title="Discard edits and reload the object's current saved values"
            >
              Load current values
            </Button>
          )}
        </div>
      )}
      <div className="builder-apply-row" data-testid="builder-apply-row">
        <span
          className={`builder-apply-state${
            stale
              ? " builder-apply-state--stale"
              : dirty
                ? " builder-apply-state--dirty"
                : ""
          }`}
        >
          {stale ? "stale" : dirty ? "unapplied changes" : "applied"}
        </span>
        <Button
          onClick={onDefaults}
          disabled={!dirty}
          title="Discard edits and return to the baseline — the values at window open, advanced to the applied draft on each Apply"
        >
          Defaults
        </Button>
        <Button onClick={onCancel} title="Close without applying">
          Cancel
        </Button>
        <Button onClick={onApply} disabled={!dirty} title="Apply edits to the session">
          Apply
        </Button>
        <Button icon="check" onClick={onOk} title="Apply and close">
          OK
        </Button>
      </div>
    </>
  );
}
