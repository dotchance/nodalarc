// Copyright 2024-2026 .chance (dotchance)
// SPDX-License-Identifier: AGPL-3.0-only
/** Shared editing controls for Builder editors.
 *
 *  Every editor composes these controls so field, card, and control behavior
 *  remains consistent. A raw <input>/<select>/<textarea> in an editor file
 *  fails the static conformance test.
 */

import { useState, type ReactNode } from "react";
import { Button } from "../ui/Button";
import type { CatalogDocumentSummary } from "./generated/builderApi";

export function Field({
  label,
  value,
  onChange,
  placeholder,
  suffix,
  stack = false,
  onBlur,
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  placeholder?: string;
  suffix?: string;
  stack?: boolean;
  onBlur?: () => void;
}) {
  const input = (
    <input
      type="text"
      placeholder={placeholder}
      value={value}
      onChange={(e) => onChange(e.target.value)}
      onBlur={onBlur}
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

/** Keep an input string while a number is incomplete or out of range.
 *  Commit only parseable in-range values, and restore the committed value on
 *  blur. External value changes appear whenever the field is not being edited. */
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
  value: number | null;
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

/** A number that may be unset — empty input means null, shown as the
 *  placeholder ("none", "unlimited"). */
export function NullableNumberField({
  label,
  value,
  onChange,
  placeholder,
  suffix,
  min,
  integer = false,
}: {
  label: string;
  value: number | null;
  onChange: (value: number | null) => void;
  placeholder: string;
  suffix?: string;
  min?: number;
  /** A whole number only: a fraction is not committed, as a value under min is not. */
  integer?: boolean;
}) {
  const { shown, handleChange, handleBlur } = useNumberDraft(value, (raw) => {
    if (raw === "") {
      onChange(null); // empty → null is this field's commit, not a no-op (pinned)
      return;
    }
    const parsed = Number(raw);
    if (!Number.isFinite(parsed)) return;
    if (min !== undefined && parsed < min) return;
    if (integer && !Number.isInteger(parsed)) return;
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
  /** Disabled options stay visible with the reason in their title. */
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
  value: string | null;
  onChange: (value: string) => void;
  options: SelectOption[];
  stack?: boolean;
}) {
  const selectedValue = value ?? "";
  const needsEmptyPlaceholder =
    selectedValue === "" && !options.some((option) => option.value === "");
  const select = (
    <select
      aria-label={ariaLabel ?? label}
      value={selectedValue}
      onChange={(e) => onChange(e.target.value)}
    >
      {needsEmptyPlaceholder && (
        <option value="" disabled>
          Select {label}
        </option>
      )}
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

/** Body picker backed by the catalog.
 *  The current value remains available while the catalog loads or fails, and a
 *  catalog error includes the backend message and a retry action. */
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
  value: string | null;
  onChange: (value: string) => void;
  bodies: {
    entries: CatalogDocumentSummary[];
    error: string | null;
    refresh: () => Promise<void>;
  };
  stack?: boolean;
}) {
  const loaded: SelectOption[] = bodies.entries.map((entry) => ({
    value: entry.ref,
    label: entry.display_name,
  }));
  const selected = value ?? "";
  const options: SelectOption[] = loaded.some((option) => option.value === selected)
    ? loaded
    : [
        {
          value: selected,
          label: selected || "Select a body",
          disabled: selected === "",
        },
        ...loaded,
      ];
  return (
    <>
      <SelectField
        label={label}
        ariaLabel={ariaLabel}
        value={selected}
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

/** Shared editor card with an optional accordion header.
 *  Header actions are available only on static cards so interactive controls
 *  are never nested inside an accordion button. */
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
  ariaLabel,
  disabled = false,
  className,
}: {
  value: string;
  onChange: (value: string) => void;
  placeholder?: string;
  rows?: number;
  ariaLabel?: string;
  disabled?: boolean;
  className?: string;
}) {
  return (
    <textarea
      aria-label={ariaLabel}
      className={`builder-paste${className ? ` ${className}` : ""}`}
      placeholder={placeholder}
      value={value}
      disabled={disabled}
      onChange={(e) => onChange(e.target.value)}
      rows={rows}
    />
  );
}

