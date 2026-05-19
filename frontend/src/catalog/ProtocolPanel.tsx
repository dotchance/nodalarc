// Copyright 2024-2026 .chance (dotchance)
// Licensed under the NodalArc Source Available License 1.0. See LICENSE file.
/** Protocol selection + extensions + area strategy panel.
 *
 * Extracted from SessionWizard.tsx steps 4-5 with zero behavior change.
 * Protocol selection and extensions are tightly coupled — extensions
 * depend on the selected protocol, constraints enforce dependencies
 * (e.g., MPLS requires TE).
 */

import type { Protocol, ExtensionRules, RoutingTimers } from "./wizardTypes";

const PROTOCOL_INFO: Record<string, { label: string; description: string; disabled?: boolean; disabledReason?: string }> = {
  ospf: { label: "OSPF", description: "Open Shortest Path First. Distributed link-state routing." },
  isis: { label: "IS-IS", description: "Intermediate System to Intermediate System. Native CLNS routing." },
  bgp: { label: "BGP", description: "Border Gateway Protocol.", disabled: true, disabledReason: "Coming Soon" },
  nodalpath: { label: "NodalPath", description: "External path computation engine.", disabled: true, disabledReason: "Separate Package" },
};

const EXTENSION_INFO: Record<string, { label: string; description: string }> = {
  te: { label: "Traffic Engineering", description: "MPLS-TE extensions. Advertises bandwidth and delay." },
  mpls: { label: "MPLS / LDP", description: "Label Distribution Protocol for MPLS forwarding plane." },
  sr: { label: "Segment Routing", description: "Source-routed MPLS with SRGB label blocks." },
};

// --- Protocol selection ---

interface ProtocolSelectionProps {
  selected: Protocol | null;
  onSelect: (protocol: Protocol) => void;
}

export function ProtocolSelection({ selected, onSelect }: ProtocolSelectionProps) {
  return (
    <div className="wizard-protocol-list">
      {Object.entries(PROTOCOL_INFO).map(([key, info]) => (
        <button
          key={key}
          className={`wizard-protocol-btn ${selected === key ? "wizard-protocol-btn--selected" : ""} ${info.disabled ? "wizard-protocol-btn--disabled" : ""}`}
          onClick={() => !info.disabled && onSelect(key as Protocol)}
          disabled={info.disabled}
          title={info.disabled ? info.disabledReason : undefined}
        >
          <div className="wizard-protocol-label">
            {info.label}
            {info.disabled && <span className="wizard-badge-soon">{info.disabledReason}</span>}
          </div>
          <div className="wizard-protocol-desc">{info.description}</div>
        </button>
      ))}
    </div>
  );
}

// --- Extensions + area strategy ---

interface ExtensionsPanelProps {
  protocol: Protocol | null;
  extensions: string[];
  areaStrategy: string;
  rules: ExtensionRules | null;
  routingTimers: RoutingTimers;
  onToggleExtension: (ext: string) => void;
  onSetAreaStrategy: (strategy: string) => void;
  onUpdateTimers: (timers: Partial<RoutingTimers>) => void;
  isExtensionAllowed: (ext: string) => boolean;
  isExtensionEnabled: (ext: string) => boolean;
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
  return (
    <>
      <div className="wizard-section">
        <h3 className="wizard-section-title">Extensions</h3>
        <div className="wizard-ext-list">
          {Object.entries(EXTENSION_INFO).map(([key, info]) => {
            const allowed = isExtensionAllowed(key);
            const enabled = isExtensionEnabled(key);
            const checked = extensions.includes(key);
            return (
              <label
                key={key}
                className={`wizard-ext-item ${!allowed ? "wizard-ext-item--unavailable" : !enabled ? "wizard-ext-item--disabled" : ""}`}
                title={!allowed ? `Not available for ${protocol}` : !enabled ? "Requires missing dependency" : undefined}
              >
                <input
                  type="checkbox"
                  checked={checked}
                  onChange={() => onToggleExtension(key)}
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
          onChange={(e) => onSetAreaStrategy(e.target.value)}
        >
          {rules?.area_strategies.map((s) => (
            <option key={s} value={s}>{s}</option>
          ))}
        </select>
        {protocol === "ospf" && areaStrategy !== "flat" && (
          <div className="wizard-warning" style={{
            marginTop: 8, padding: "8px 12px", background: "rgba(200, 160, 40, 0.15)",
            border: "1px solid rgba(200, 160, 40, 0.4)", borderRadius: 4, fontSize: 12,
            color: "var(--text-dim, #aaa)", lineHeight: 1.4,
          }}>
            OSPF multi-area with dynamic constellation topologies may experience
            backbone (area 0) non-contiguity when cross-plane ISLs drop at polar
            latitudes. This can cause inter-area routing failures. IS-IS does not
            have this limitation. Use flat area strategy for reliable OSPF connectivity.
          </div>
        )}
      </div>

      {/* Protocol Timers */}
      {protocol && protocol !== "nodalpath" && (() => {
        const errors = validateTimers(protocol, routingTimers);
        return (
          <>
            <div className="wizard-section">
              <h3 className="wizard-section-title">
                {protocol === "isis" ? "IS-IS" : "OSPF"} Timers
              </h3>
              <div className="wizard-timer-list">
                {protocol === "isis" ? (
                  <>
                    <TimerField label="Hello Interval" unit="s" value={routingTimers.isis_hello_interval}
                      onChange={(v) => onUpdateTimers({ isis_hello_interval: v })} min={1} max={60}
                      desc="Time between IS-IS hello packets. Lower = faster adjacency detection."
                      range="LEO: 1s. Terrestrial: 3-10s." />
                    <TimerField label="Hello Multiplier" value={routingTimers.isis_hello_multiplier}
                      onChange={(v) => onUpdateTimers({ isis_hello_multiplier: v })} min={2} max={50}
                      desc="Missed hellos before declaring neighbor down. Dead time = interval × multiplier."
                      range="Typical: 3 (3s dead time at 1s hello)." />
                    <TimerField label="SPF Init Delay" unit="ms" value={routingTimers.spf_init_delay}
                      onChange={(v) => onUpdateTimers({ spf_init_delay: v })} min={1} max={60000}
                      desc="Delay before first SPF computation after a topology change."
                      range="Aggressive: 50ms. Conservative: 1000ms." />
                    <TimerField label="SPF Short Delay" unit="ms" value={routingTimers.spf_short_delay}
                      onChange={(v) => onUpdateTimers({ spf_short_delay: v })} min={1} max={60000}
                      desc="SPF delay for subsequent events within the learning window."
                      range="LEO: 200ms. Stable networks: 1000-5000ms."
                      error={errors.spf_short_delay} />
                    <TimerField label="SPF Long Delay" unit="ms" value={routingTimers.spf_long_delay}
                      onChange={(v) => onUpdateTimers({ spf_long_delay: v })} min={1} max={120000}
                      desc="Maximum SPF delay during sustained topology churn."
                      range="LEO: 1000ms. Must not exceed handover interval."
                      error={errors.spf_long_delay} />
                    <TimerField label="SPF Holddown" unit="ms" value={routingTimers.spf_holddown}
                      onChange={(v) => onUpdateTimers({ spf_holddown: v })} min={1} max={600000}
                      desc="Time without events before returning to init delay. Resets the backoff."
                      range="LEO: 2000ms. Terrestrial: 10000-30000ms."
                      error={errors.spf_holddown} />
                  </>
                ) : (
                  <>
                    <TimerField label="Hello Interval" unit="s" value={routingTimers.ospf_hello_interval}
                      onChange={(v) => onUpdateTimers({ ospf_hello_interval: v })} min={1} max={65535}
                      desc="Time between OSPF hello packets on each interface."
                      range="LEO: 1s. Terrestrial: 10s (default)." />
                    <TimerField label="Dead Interval" unit="s" value={routingTimers.ospf_dead_interval}
                      onChange={(v) => onUpdateTimers({ ospf_dead_interval: v })} min={1} max={65535}
                      desc="Time without hellos before declaring neighbor dead."
                      range="Typically 3-4× hello interval."
                      error={errors.ospf_dead_interval} />
                    <TimerField label="SPF Delay" unit="ms" value={routingTimers.ospf_spf_delay}
                      onChange={(v) => onUpdateTimers({ ospf_spf_delay: v })} min={1} max={600000}
                      desc="Initial delay before SPF computation after a topology change."
                      range="Aggressive: 50ms. Conservative: 1000ms." />
                    <TimerField label="SPF Initial Hold" unit="ms" value={routingTimers.ospf_spf_initial_hold}
                      onChange={(v) => onUpdateTimers({ ospf_spf_initial_hold: v })} min={1} max={600000}
                      desc="Minimum time between consecutive SPF runs (doubles on each run)."
                      range="LEO: 200ms. Stable networks: 1000-5000ms."
                      error={errors.ospf_spf_initial_hold} />
                    <TimerField label="SPF Max Hold" unit="ms" value={routingTimers.ospf_spf_max_hold}
                      onChange={(v) => onUpdateTimers({ ospf_spf_max_hold: v })} min={1} max={600000}
                      desc="Maximum delay between SPF runs during sustained churn."
                      range="LEO: 1000ms. Must not exceed handover interval."
                      error={errors.ospf_spf_max_hold} />
                  </>
                )}
              </div>
            </div>

            <div className="wizard-section">
              <h3 className="wizard-section-title">BFD (Bidirectional Forwarding Detection)</h3>
              <label className="wizard-ext-item">
                <input type="checkbox" checked={routingTimers.bfd}
                  onChange={() => onUpdateTimers({ bfd: !routingTimers.bfd })} />
                <span className="wizard-ext-label">Enable BFD</span>
                <span className="wizard-ext-desc">Sub-second link failure detection independent of routing protocol hellos.</span>
              </label>
              {routingTimers.bfd && (
                <div className="wizard-timer-list" style={{ marginTop: 8 }}>
                  <TimerField label="Detect Multiplier" value={routingTimers.bfd_detect_multiplier}
                    onChange={(v) => onUpdateTimers({ bfd_detect_multiplier: v })} min={2} max={255}
                    desc="Missed BFD packets before declaring failure. Detection time = multiplier × interval."
                    range="Typical: 3 (900ms detection at 300ms interval)."
                    error={errors.bfd_detect_multiplier} />
                  <TimerField label="RX Interval" unit="ms" value={routingTimers.bfd_rx_interval}
                    onChange={(v) => onUpdateTimers({ bfd_rx_interval: v })} min={50} max={60000}
                    desc="Minimum interval for receiving BFD control packets."
                    range="Aggressive: 100ms. Typical: 300ms." />
                  <TimerField label="TX Interval" unit="ms" value={routingTimers.bfd_tx_interval}
                    onChange={(v) => onUpdateTimers({ bfd_tx_interval: v })} min={50} max={60000}
                    desc="Minimum interval for transmitting BFD control packets."
                    range="Aggressive: 100ms. Typical: 300ms." />
                </div>
              )}
            </div>
          </>
        );
      })()}
    </>
  );
}

// --- Timer field component ---

function TimerField({ label, unit, value, onChange, desc, range, min, max, error }: {
  label: string; unit?: string; value: number; onChange: (v: number) => void;
  desc?: string; range?: string; min?: number; max?: number; error?: string;
}) {
  return (
    <div className={`wizard-timer-row ${error ? "wizard-timer-row--error" : ""}`}>
      <div className="wizard-timer-header">
        <span className="wizard-timer-label">{label}</span>
        <div className="wizard-timer-input-group">
          <input
            type="number"
            className="wizard-input wizard-input--sm"
            value={value}
            min={min ?? 1}
            max={max ?? 999999}
            onChange={(e) => {
              const v = parseInt(e.target.value, 10);
              if (!isNaN(v) && v >= (min ?? 1)) onChange(v);
            }}
          />
          {unit && <span className="wizard-timer-unit">{unit}</span>}
        </div>
      </div>
      {desc && <div className="wizard-timer-desc">{desc}</div>}
      {range && <div className="wizard-timer-range">{range}</div>}
      {error && <div className="wizard-timer-error">{error}</div>}
    </div>
  );
}

// --- Validation ---

function validateTimers(protocol: string, t: RoutingTimers): Record<string, string> {
  const errors: Record<string, string> = {};
  if (protocol === "isis") {
    if (t.spf_short_delay < t.spf_init_delay)
      errors.spf_short_delay = "Must be ≥ init delay";
    if (t.spf_long_delay < t.spf_short_delay)
      errors.spf_long_delay = "Must be ≥ short delay";
    if (t.spf_holddown < t.spf_long_delay)
      errors.spf_holddown = "Must be ≥ long delay";
  } else if (protocol === "ospf") {
    if (t.ospf_dead_interval <= t.ospf_hello_interval)
      errors.ospf_dead_interval = "Must be > hello interval";
    if (t.ospf_spf_initial_hold < t.ospf_spf_delay)
      errors.ospf_spf_initial_hold = "Must be ≥ SPF delay";
    if (t.ospf_spf_max_hold < t.ospf_spf_initial_hold)
      errors.ospf_spf_max_hold = "Must be ≥ initial hold";
  }
  if (t.bfd) {
    const bfd_detect_ms = t.bfd_detect_multiplier * t.bfd_rx_interval;
    const dead_ms = protocol === "isis"
      ? t.isis_hello_interval * t.isis_hello_multiplier * 1000
      : t.ospf_dead_interval * 1000;
    if (bfd_detect_ms >= dead_ms)
      errors.bfd_detect_multiplier = `BFD detection (${bfd_detect_ms}ms) should be faster than ${protocol === "isis" ? "IS-IS" : "OSPF"} dead time (${dead_ms}ms)`;
  }
  return errors;
}
