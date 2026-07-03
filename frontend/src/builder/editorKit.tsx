// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
/** The editor kit — the ONLY source of editing controls in builder editors.
 *
 *  Interaction-grammar enforcement by construction (IG-5): every editor
 *  composes these controls, so field anatomy, card anatomy, and control
 *  behavior cannot drift per surface. A raw <input>/<select>/<textarea> in
 *  an editor file fails the static conformance test.
 *
 *  IG-2: EditorName carries create-focus (the name field is focused when a
 *  create gesture opens the editor; typing renames immediately).
 */

import { useEffect, useRef, type ReactNode } from "react";
import { Button } from "../ui/Button";

/** The name field every editor leads with. autoFocus fires on mount when
 *  the editor was opened by a create gesture (IG-2). */
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
  return (
    <label className="builder-field">
      <span className="builder-field-label">{label}</span>
      <span className="builder-field-input">
        <input
          type="number"
          value={value}
          min={min}
          step={step}
          onChange={(e) => {
            let parsed = Number(e.target.value);
            if (!Number.isFinite(parsed)) return;
            if (integer) parsed = Math.round(parsed);
            if (min !== undefined) parsed = Math.max(min, parsed);
            onChange(parsed);
          }}
        />
        {suffix && <span className="builder-field-suffix">{suffix}</span>}
      </span>
    </label>
  );
}

/** A slider with a typeable value box beside it. The track covers the
 *  common range; the box accepts numbers beyond it (type past the track).
 *  Dragging streams onChange continuously so the canvas preview moves with
 *  the thumb. */
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
  const parse = (raw: string): number | null => {
    let parsed = Number(raw);
    if (!Number.isFinite(parsed)) return null;
    if (integer) parsed = Math.round(parsed);
    return parsed;
  };
  return (
    <label className="builder-field builder-field--slider">
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
            const parsed = parse(e.target.value);
            if (parsed !== null) onChange(parsed);
          }}
        />
        <input
          type="number"
          value={value}
          step={step}
          onChange={(e) => {
            const parsed = parse(e.target.value);
            if (parsed !== null) onChange(parsed);
          }}
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
  return (
    <label className="builder-field">
      <span className="builder-field-label">{label}</span>
      <span className="builder-field-input">
        <input
          type="number"
          placeholder={placeholder}
          value={value ?? ""}
          onChange={(e) => {
            if (e.target.value === "") {
              onChange(null);
              return;
            }
            const parsed = Number(e.target.value);
            if (!Number.isFinite(parsed)) return;
            if (min !== undefined && parsed < min) return;
            onChange(parsed);
          }}
        />
        {suffix && <span className="builder-field-suffix">{suffix}</span>}
      </span>
    </label>
  );
}

export interface SelectOption {
  value: string;
  label: string;
  /** Disabled options stay VISIBLE with the reason in their title —
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

/** The one card anatomy (IG-5): title + spec-sheet summary; closed cards
 *  read as the object's spec. Accordion when onToggle is given; static
 *  (always open) when not. */
export function EditorCard({
  title,
  summary,
  open,
  onToggle,
  actions,
  children,
}: {
  title: string;
  summary?: ReactNode;
  open: boolean;
  onToggle?: () => void;
  actions?: ReactNode;
  children?: ReactNode;
}) {
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

/** Compact controls for member/list rows (site rows and their kin). */
export function InlineNumber({
  ariaLabel,
  value,
  onChange,
  step,
}: {
  ariaLabel: string;
  value: number;
  onChange: (value: number) => void;
  step?: number;
}) {
  return (
    <input
      type="number"
      aria-label={ariaLabel}
      value={value}
      step={step}
      onChange={(e) => {
        const parsed = Number(e.target.value);
        if (Number.isFinite(parsed)) onChange(parsed);
      }}
    />
  );
}

export function InlineText({
  ariaLabel,
  value,
  onChange,
  placeholder,
}: {
  ariaLabel: string;
  value: string;
  onChange: (value: string) => void;
  placeholder?: string;
}) {
  return (
    <input
      type="text"
      aria-label={ariaLabel}
      placeholder={placeholder}
      value={value}
      onChange={(e) => onChange(e.target.value)}
    />
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
 *  the title-bar X, but said out loud. Defaults returns the window to the
 *  values it opened with. The state label answers the question the buttons
 *  exist for: "did my typing take?" */
export function EditorApplyRow({
  dirty,
  onApply,
  onOk,
  onDefaults,
  onCancel,
}: {
  dirty: boolean;
  onApply: () => void;
  onOk: () => void;
  onDefaults: () => void;
  onCancel: () => void;
}) {
  return (
    <div className="builder-apply-row" data-testid="builder-apply-row">
      <span
        className={`builder-apply-state${dirty ? " builder-apply-state--dirty" : ""}`}
      >
        {dirty ? "unapplied changes" : "applied"}
      </span>
      <Button
        onClick={onDefaults}
        disabled={!dirty}
        title="Discard edits and return to the values this window opened with"
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
  );
}
