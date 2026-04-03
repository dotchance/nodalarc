// Copyright 2024-2026 .chance (dotchance)
// Licensed under the NodalArc Source Available License 1.0. See LICENSE file.
/** Protocol selection + extensions + area strategy panel.
 *
 * Extracted from SessionWizard.tsx steps 4-5 with zero behavior change.
 * Protocol selection and extensions are tightly coupled — extensions
 * depend on the selected protocol, constraints enforce dependencies
 * (e.g., MPLS requires TE).
 */

import type { Protocol, ExtensionRules } from "./wizardTypes";

const PROTOCOL_INFO: Record<string, { label: string; description: string; disabled?: boolean; disabledReason?: string }> = {
  ospf: { label: "OSPF", description: "Open Shortest Path First. Distributed link-state routing." },
  isis: { label: "IS-IS", description: "Intermediate System to Intermediate System. Native CLNS routing." },
  bgp: { label: "BGP", description: "Border Gateway Protocol.", disabled: true, disabledReason: "Coming Soon" },
  nodalpath: { label: "NodalPath", description: "Centralized MPLS path computation (NEBULA model). No FRR routing daemon." },
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
  onToggleExtension: (ext: string) => void;
  onSetAreaStrategy: (strategy: string) => void;
  isExtensionAllowed: (ext: string) => boolean;
  isExtensionEnabled: (ext: string) => boolean;
}

export function ExtensionsPanel({
  protocol,
  extensions,
  areaStrategy,
  rules,
  onToggleExtension,
  onSetAreaStrategy,
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
    </>
  );
}
