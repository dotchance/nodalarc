// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
/** Protocol selection + extensions + area strategy panel.
 *
 * Extracted from SessionWizard.tsx steps 4-5 with zero behavior change.
 * Protocol selection and extensions are tightly coupled — extensions
 * depend on the selected protocol, and backend constraints enforce any
 * declared dependencies.
 */

import type {
  Protocol,
  ExtensionRules,
  RoutingTimers,
  WizardExtension,
  WizardRoutingBooleanField,
  AreaStrategy,
  WizardRoutingTimerField,
} from "./wizardTypes";

function timerValue(timers: RoutingTimers, field: WizardRoutingTimerField): number {
  return timers[field];
}

function timerPatch(
  field: WizardRoutingTimerField,
  value: number,
): Partial<RoutingTimers> {
  return { [field]: value } as Partial<RoutingTimers>;
}

function booleanPatch(
  field: WizardRoutingBooleanField,
  value: boolean,
): Partial<RoutingTimers> {
  return { [field]: value } as Partial<RoutingTimers>;
}

// --- Protocol selection ---

interface ProtocolSelectionProps {
  selected: Protocol | null;
  rules: ExtensionRules | null;
  onSelect: (protocol: Protocol) => void;
}

export function ProtocolSelection({ selected, rules, onSelect }: ProtocolSelectionProps) {
  if (!rules) {
    return <div className="wizard-error">Routing choices did not load from VS-API.</div>;
  }
  return (
    <div className="wizard-protocol-list">
      {rules.protocols.map((protocol) => (
        <button
          key={protocol.id}
          className={`wizard-protocol-btn ${selected === protocol.id ? "wizard-protocol-btn--selected" : ""}`}
          onClick={() => onSelect(protocol.id)}
        >
          <div className="wizard-protocol-label">
            {protocol.label}
          </div>
          <div className="wizard-protocol-desc">{protocol.description}</div>
        </button>
      ))}
    </div>
  );
}

// --- Extensions + area strategy ---

interface ExtensionsPanelProps {
  protocol: Protocol | null;
  extensions: WizardExtension[];
  areaStrategy: AreaStrategy | null;
  rules: ExtensionRules | null;
  routingTimers: RoutingTimers | null;
  onToggleExtension: (ext: WizardExtension) => void;
  onSetAreaStrategy: (strategy: AreaStrategy) => void;
  onUpdateTimers: (timers: Partial<RoutingTimers>) => void;
  isExtensionAllowed: (ext: WizardExtension) => boolean;
  isExtensionEnabled: (ext: WizardExtension) => boolean;
}

export function ExtensionsPanel({
  protocol,
  extensions,
  areaStrategy,
  rules,
  routingTimers,
  onToggleExtension,
  onSetAreaStrategy,
  onUpdateTimers,
  isExtensionAllowed,
  isExtensionEnabled,
}: ExtensionsPanelProps) {
  if (!rules || !areaStrategy || !routingTimers) {
    return <div className="wizard-error">Routing authoring facts did not load from VS-API.</div>;
  }
  const protocolFacts = rules.protocols.find((item) => item.id === protocol);
  const bfdEnabled = routingTimers[rules.bfd.enabled_field];
  return (
    <>
      <div className="wizard-section">
        <h3 className="wizard-section-title">Extensions</h3>
        <div className="wizard-ext-list">
          {rules.extensions.map((info) => {
            const allowed = isExtensionAllowed(info.id);
            const enabled = isExtensionEnabled(info.id);
            const checked = extensions.includes(info.id);
            return (
              <label
                key={info.id}
                className={`wizard-ext-item ${!allowed ? "wizard-ext-item--unavailable" : !enabled ? "wizard-ext-item--disabled" : ""}`}
                title={!allowed ? `Not available for ${protocol}` : !enabled ? "Requires missing dependency" : undefined}
              >
                <input
                  type="checkbox"
                  checked={checked}
                  onChange={() => onToggleExtension(info.id)}
                  disabled={!allowed || (!enabled && !checked)}
                />
                <span className="wizard-ext-label">{info.label}</span>
                <span className="wizard-ext-desc">{info.description}</span>
              </label>
            );
          })}
        </div>
      </div>
      <div className="wizard-section">
        <h3 className="wizard-section-title">Area Strategy</h3>
        <select
          className="wizard-select"
          value={areaStrategy}
          onChange={(e) => onSetAreaStrategy(e.target.value as AreaStrategy)}
        >
          {rules?.area_strategies.map((s) => (
            <option key={s} value={s}>{s}</option>
          ))}
        </select>
        {protocolFacts?.non_flat_area_warning && areaStrategy !== "flat" && (
          <div className="wizard-warning" style={{
            marginTop: 8, padding: "8px 12px", background: "rgba(200, 160, 40, 0.15)",
            border: "1px solid rgba(200, 160, 40, 0.4)", borderRadius: 4, fontSize: 12,
            color: "var(--text-dim, #aaa)", lineHeight: 1.4,
          }}>
            {protocolFacts.non_flat_area_warning}
          </div>
        )}
      </div>

      {/* Protocol Timers */}
      {protocolFacts && (
          <>
            <div className="wizard-section">
              <h3 className="wizard-section-title">{protocolFacts.timer_label}</h3>
              <div className="wizard-timer-list">
                {protocolFacts.timer_fields.map((field) => (
                  <TimerField
                    key={field.id}
                    label={field.label}
                    unit={field.unit ?? undefined}
                    value={timerValue(routingTimers, field.id)}
                    onChange={(value) => onUpdateTimers(timerPatch(field.id, value))}
                    min={field.minimum}
                    desc={field.description}
                    range={field.guidance}
                  />
                ))}
              </div>
            </div>

            <div className="wizard-section">
              <h3 className="wizard-section-title">{rules.bfd.heading}</h3>
              <label className="wizard-ext-item">
                <input type="checkbox" checked={bfdEnabled}
                  onChange={() => onUpdateTimers(booleanPatch(
                    rules.bfd.enabled_field,
                    !bfdEnabled,
                  ))} />
                <span className="wizard-ext-label">{rules.bfd.enable_label}</span>
                <span className="wizard-ext-desc">{rules.bfd.enable_description}</span>
              </label>
              {bfdEnabled && (
                <div className="wizard-timer-list" style={{ marginTop: 8 }}>
                  {rules.bfd.timer_fields.map((field) => (
                    <TimerField
                      key={field.id}
                      label={field.label}
                      unit={field.unit ?? undefined}
                      value={timerValue(routingTimers, field.id)}
                      onChange={(value) => onUpdateTimers(timerPatch(field.id, value))}
                      min={field.minimum}
                      desc={field.description}
                      range={field.guidance}
                    />
                  ))}
                </div>
              )}
            </div>
          </>
      )}
    </>
  );
}

// --- Timer field component ---

function TimerField({ label, unit, value, onChange, desc, range, min }: {
  label: string; unit?: string; value: number; onChange: (v: number) => void;
  desc?: string; range?: string; min: number;
}) {
  return (
    <div className="wizard-timer-row">
      <div className="wizard-timer-header">
        <span className="wizard-timer-label">{label}</span>
        <div className="wizard-timer-input-group">
          <input
            type="number"
            className="wizard-input wizard-input--sm"
            value={value}
            min={min}
            onChange={(e) => {
              const v = Number(e.target.value);
              if (Number.isInteger(v)) onChange(v);
            }}
          />
          {unit && <span className="wizard-timer-unit">{unit}</span>}
        </div>
      </div>
      {desc && <div className="wizard-timer-desc">{desc}</div>}
      {range && <div className="wizard-timer-range">{range}</div>}
    </div>
  );
}
