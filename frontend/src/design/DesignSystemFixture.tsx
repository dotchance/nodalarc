// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
/**
 * Design-system fixture — the review surface for tokens, typography, taxonomy,
 * and (as they land) component primitives. Dev-only: served at ?fixture=1 by
 * main.tsx when import.meta.env.DEV; excluded from production bundles.
 *
 * Rules this page exists to prove:
 *  - color slots stay separate: regime = object class, relation = link family,
 *    medium = secondary accent, state = truth (pattern or override);
 *  - the family color law holds in both themes (faulted is the only red;
 *    expected no-link reads calm);
 *  - `degraded`/`unsupported` link states are FORWARD vocabulary — no runtime
 *    fact produces them yet, so they appear here and nowhere else.
 */

import { useState, type CSSProperties } from "react";
import { tokens, THEMES, THEME_STORAGE_KEY, activeThemeName, type ThemeName } from "../styles/tokens";
import { FAMILIES, FAMILY_TONE } from "../explain/families";
import { Icon } from "../ui/icons/Icon";
import { ICON_BODIES, type IconName } from "../ui/icons/lucide";
import { Button, IconButton } from "../ui/Button";
import { Badge, StatusDot } from "../ui/Badge";
import { KeyValueRow, DetailSection } from "../ui/KeyValueRow";
import { Tabs } from "../ui/Tabs";
import { FloatingWindow } from "../ui/FloatingWindow";
import { DataTable, type SortState, type TableColumn } from "../ui/DataTable";
import "../ui/ui.css";
import "./fixture.css";

function Swatch({ name, value, label }: { name: string; value: string; label?: string }) {
  return (
    <div className="fx-swatch">
      <span className="fx-swatch-chip" style={{ background: value }} />
      <span className="fx-swatch-name">{name}</span>
      <code className="fx-swatch-value">{value}</code>
      {label && <span className="fx-swatch-label">{label}</span>}
    </div>
  );
}

function hexToCss(hex: number): string {
  return "#" + hex.toString(16).padStart(6, "0");
}

const LINK_RELATIONS = [
  { key: "isl", label: "ISL", color: hexToCss(tokens.colorLinkIsl) },
  { key: "access", label: "Access", color: hexToCss(tokens.colorLinkGround) },
  { key: "interbody", label: "Inter-body", color: hexToCss(tokens.colorLinkInterbody) },
  { key: "terrestrial", label: "Terrestrial", color: tokens.mediumTerrestrial },
] as const;

const LINK_MEDIUMS = [
  { key: "rf", label: "RF", color: tokens.mediumRf },
  { key: "optical", label: "Optical", color: tokens.mediumOptical },
  { key: "terrestrial", label: "Terrestrial", color: tokens.mediumTerrestrial },
] as const;

const LINK_STATES = ["active", "candidate", "degraded", "faulted", "unsupported"] as const;

function LinkSample({ relation, medium, state }: { relation: string; medium: string; state: string }) {
  const rel = LINK_RELATIONS.find((r) => r.key === relation)!;
  const med = LINK_MEDIUMS.find((m) => m.key === medium)!;
  return (
    <span
      className={`fx-link state-${state}`}
      style={{ "--relation-color": rel.color, "--medium-color": med.color } as CSSProperties}
    />
  );
}

function setTheme(name: ThemeName) {
  localStorage.setItem(THEME_STORAGE_KEY, name);
  location.reload();
}

const DEMO_COLUMNS: TableColumn[] = [
  { key: "time", label: "Time", width: 90, sortable: true },
  { key: "source", label: "Source", width: 90, sortable: true },
  { key: "level", label: "Level", width: 70, sortable: true },
  { key: "message", label: "Message", mono: false },
];

const DEMO_ROWS = [
  { id: "1", time: "06:11:04.188", source: "scheduler", level: "warning", message: "earth_luna_bridge export waiting for kernel proof before route injection" },
  { id: "2", time: "06:11:04.944", source: "ome", level: "info", message: "Selected RF access pair santiago-gw1 to leo-sat-p00s12 elevation=42.8" },
  { id: "3", time: "06:11:06.275", source: "node_agent", level: "error", message: "Kernel state mismatch after LinkUp: expected netem delay missing" },
];

function PrimitivesSection() {
  const [windowOpen, setWindowOpen] = useState(false);
  const [tab, setTab] = useState("object");
  const [columns, setColumns] = useState(DEMO_COLUMNS);
  const [sort, setSort] = useState<SortState | null>(null);

  return (
    <section className="fx-section">
      <h2>Primitives</h2>

      <h3 className="fx-sub">Buttons</h3>
      <div className="fx-row">
        <Button>Default</Button>
        <Button variant="primary">Primary</Button>
        <Button variant="danger">Danger</Button>
        <Button variant="ghost">Ghost</Button>
        <Button active>Active</Button>
        <Button disabled>Disabled</Button>
        <Button icon="download">With icon</Button>
        <IconButton icon="search" label="Search" />
        <IconButton icon="x" label="Close" />
        <IconButton icon="plus" label="Larger" active />
      </div>

      <h3 className="fx-sub">Status dots + badges</h3>
      <div className="fx-row">
        <StatusDot tone="ok" title="ok" />
        <StatusDot tone="warn" title="warn" />
        <StatusDot tone="fail" title="fail" />
        <StatusDot tone="neutral" title="neutral" />
        <Badge>neutral</Badge>
        <Badge tone="ok">clean</Badge>
        <Badge tone="warn">degraded</Badge>
        <Badge tone="fail">faulted</Badge>
        <Badge tone="accent">NodalArc</Badge>
      </div>

      <h3 className="fx-sub">Key-value rows</h3>
      <div style={{ maxWidth: 420 }}>
        <DetailSection title="Identity">
          <KeyValueRow label="segment">leo_access_a</KeyValueRow>
          <KeyValueRow label="loopback">10.255.0.104/32</KeyValueRow>
          <KeyValueRow label="adjacency" state="ok">UP</KeyValueRow>
          <KeyValueRow label="kernel state" state="failed">netem delay missing</KeyValueRow>
          <KeyValueRow label="max_range_km" state="dead" title="dead knob">40000</KeyValueRow>
          <KeyValueRow label="notes" state="dim" mono={false}>narrowed by site install</KeyValueRow>
        </DetailSection>
      </div>

      <h3 className="fx-sub">Tabs</h3>
      <Tabs
        label="Demo tabs"
        tabs={[
          { key: "object", label: "Object" },
          { key: "links", label: "Links" },
          { key: "routing", label: "Routing" },
          { key: "cli", label: "leo-a-sat-p00s12", closable: true },
        ]}
        active={tab}
        onSelect={setTab}
        onClose={() => undefined}
      />

      <h3 className="fx-sub">Data table (sort, drag-reorder, resize)</h3>
      <div style={{ height: 140, display: "flex" }}>
        <DataTable
          label="Demo table"
          columns={columns}
          onColumnsChange={setColumns}
          rows={DEMO_ROWS}
          rowKey={(r) => r.id}
          renderCell={(r, key) => r[key as keyof typeof r]}
          sort={sort}
          onSortChange={setSort}
        />
      </div>

      <h3 className="fx-sub">Floating window</h3>
      <div className="fx-row">
        <Button icon="scroll-text" onClick={() => setWindowOpen(true)}>
          Open demo window
        </Button>
      </div>
      {windowOpen && (
        <FloatingWindow
          title="Demo window"
          onClose={() => setWindowOpen(false)}
          initial={{ x: 120, y: 120, w: 520, h: 240 }}
          headerExtras={
            <>
              <IconButton icon="minus" label="Smaller text" />
              <IconButton icon="plus" label="Larger text" />
            </>
          }
        >
          <div style={{ padding: "var(--space-6)", color: "var(--text-secondary)" }}>
            Drag the title bar; resize from any edge or corner; Escape closes when focused.
          </div>
        </FloatingWindow>
      )}
    </section>
  );
}

export function DesignSystemFixture() {
  const active = activeThemeName();
  return (
    <div className="fx-root">
      <header className="fx-header">
        <div>
          <h1>NodalArc Design System</h1>
          <p className="fx-sub">
            Fixture review surface · theme: <code>{active}</code> · dev-only
          </p>
        </div>
        <div className="fx-theme-buttons">
          {(Object.keys(THEMES) as ThemeName[]).map((name) => (
            <button
              key={name}
              className={`fx-btn${name === active ? " fx-btn--active" : ""}`}
              onClick={() => setTheme(name)}
            >
              {name === "mission-light" ? "Mission Light" : "NOC Dark"}
            </button>
          ))}
        </div>
      </header>

      <section className="fx-section">
        <h2>Surfaces</h2>
        <div className="fx-grid">
          <Swatch name="bgMain" value={tokens.bgMain} label="app shell" />
          <Swatch name="bgBar" value={tokens.bgBar} label="top/bottom bars" />
          <Swatch name="bgPanel" value={tokens.bgPanel} label="primary panel" />
          <Swatch name="bgPanelHover" value={tokens.bgPanelHover} label="nested / hover" />
          <Swatch name="border" value={tokens.border} label="quiet separator" />
          <Swatch name="borderStrong" value={tokens.borderStrong} label="strong separator" />
        </div>
      </section>

      <section className="fx-section">
        <h2>Text + accents</h2>
        <div className="fx-grid">
          <Swatch name="textPrimary" value={tokens.textPrimary} />
          <Swatch name="textSecondary" value={tokens.textSecondary} />
          <Swatch name="textDim" value={tokens.textDim} />
          <Swatch name="accentBlue" value={tokens.accentBlue} label="focus / primary action" />
          <Swatch name="accentTeal" value={tokens.accentTeal} />
          <Swatch name="accentOrange" value={tokens.accentOrange} />
          <Swatch name="accentGreen" value={tokens.accentGreen} />
          <Swatch name="accentAmber" value={tokens.accentAmber} />
          <Swatch name="accentRed" value={tokens.accentRed} />
        </div>
      </section>

      <section className="fx-section">
        <h2>Status slots</h2>
        <p className="fx-note">
          Generic good/warn/bad for transport health, convergence, and severity fills. Status colors
          and taxonomy colors must appear in different visual slots.
        </p>
        <div className="fx-grid">
          <Swatch name="statusOk" value={tokens.statusOk} />
          <Swatch name="statusWarn" value={tokens.statusWarn} />
          <Swatch name="statusFail" value={tokens.statusFail} />
        </div>
      </section>

      <section className="fx-section">
        <h2>Decision families (the color law)</h2>
        <p className="fx-note">
          <strong>faulted is the ONLY red.</strong> A restrictive or intermittent model must read as
          calm, never as an error. Enforced per-theme by <code>registry.test.ts</code>.
        </p>
        <div className="fx-grid">
          {FAMILIES.map((fam) => (
            <Swatch key={fam} name={fam} value={FAMILY_TONE[fam].css} label={FAMILY_TONE[fam].label} />
          ))}
        </div>
      </section>

      <section className="fx-section">
        <h2>Taxonomy — regime / medium / relation</h2>
        <p className="fx-note">
          Regime identifies object class; relation identifies link family; medium is a secondary
          accent. Theme-invariant identity colors.
        </p>
        <div className="fx-grid">
          <Swatch name="regimeLeo" value={tokens.regimeLeo} label="LEO" />
          <Swatch name="regimeMeo" value={tokens.regimeMeo} label="MEO" />
          <Swatch name="regimeGeo" value={tokens.regimeGeo} label="GEO" />
          <Swatch name="regimeHeo" value={tokens.regimeHeo} label="HEO" />
          <Swatch name="regimeLuna" value={tokens.regimeLuna} label="Luna" />
        </div>
        <div className="fx-grid">
          {LINK_MEDIUMS.map((m) => (
            <Swatch key={m.key} name={`medium ${m.label}`} value={m.color} />
          ))}
        </div>
        <div className="fx-grid">
          {LINK_RELATIONS.map((r) => (
            <Swatch key={r.key} name={`relation ${r.label}`} value={r.color} />
          ))}
        </div>
      </section>

      <section className="fx-section">
        <h2>Link language — relation × medium × state</h2>
        <p className="fx-note">
          State draws the line (solid / dashed / amber halo / red interrupted / gray dotted);
          relation is the line identity; medium is the endpoint dots.{" "}
          <strong>degraded and unsupported are forward vocabulary</strong> — no runtime fact
          produces them yet; they render here only.
        </p>
        <div className="fx-link-matrix">
          {([
            ["isl", "rf", "RF ISL"],
            ["isl", "optical", "Optical ISL"],
            ["access", "rf", "RF access"],
            ["access", "optical", "Optical access"],
            ["interbody", "optical", "Optical inter-body"],
            ["terrestrial", "terrestrial", "Terrestrial"],
          ] as const).map(([rel, med, label]) => (
            <div className="fx-link-row" key={`${rel}-${med}`}>
              <span className="fx-link-row-label">{label}</span>
              {LINK_STATES.map((state) => (
                <div className="fx-link-cell" key={state}>
                  <LinkSample relation={rel} medium={med} state={state} />
                  <small>{state}</small>
                </div>
              ))}
            </div>
          ))}
        </div>
      </section>

      <section className="fx-section">
        <h2>Typography</h2>
        <div className="fx-type-specimen">
          <div style={{ fontFamily: tokens.fontFamilyUi }}>
            <span className="fx-spec-tag">UI · Inter</span>
            <strong style={{ fontWeight: 700 }}>Earth–Luna gateway route planner</strong>
            <span style={{ fontWeight: 600 }}> Validation explains why a link can or cannot exist.</span>
            <span style={{ fontWeight: 400 }}> Panels, labels, copy, and controls use this face.</span>
          </div>
          <div style={{ fontFamily: tokens.fontFamilyCli }}>
            <span className="fx-spec-tag">Data · IBM Plex Mono</span>
            <code>leo-a-sat-p00s12 · 10.255.0.104/32 · 43.7 ms · IS-IS L2</code>
          </div>
        </div>
        <div className="fx-type-scale">
          {([
            ["fontSizeXxs", tokens.fontSizeXxs],
            ["fontSizeXs", tokens.fontSizeXs],
            ["fontSizeSm", tokens.fontSizeSm],
            ["fontSizeMd", tokens.fontSizeMd],
            ["fontSizeLg", tokens.fontSizeLg],
            ["fontSizeXl", tokens.fontSizeXl],
            ["fontSizeXxl", tokens.fontSizeXxl],
          ] as const).map(([name, size]) => (
            <div key={name} className="fx-type-row">
              <code>{name} {size}</code>
              <span style={{ fontSize: size }}>Routing adjacency proven on term2 — 38 links active</span>
            </div>
          ))}
        </div>
      </section>

      <PrimitivesSection />

      <section className="fx-section">
        <h2>Icons</h2>
        <p className="fx-note">
          Vendored Lucide artwork (ISC), <code>stroke=currentColor</code> — icons inherit the
          surrounding text color slot.
        </p>
        <div className="fx-icon-grid">
          {(Object.keys(ICON_BODIES) as IconName[]).map((name) => (
            <div key={name} className="fx-icon-cell">
              <Icon name={name} size={18} />
              <code>{name}</code>
            </div>
          ))}
        </div>
      </section>

      <section className="fx-section">
        <h2>Z ladder</h2>
        <div className="fx-zladder">
          {([
            ["zPanel", tokens.zPanel],
            ["zCliDrawer", tokens.zCliDrawer],
            ["zPopover", tokens.zPopover],
            ["zCatalog", tokens.zCatalog],
            ["zOverlay", tokens.zOverlay],
            ["zScrim", tokens.zScrim],
            ["zTooltip", tokens.zTooltip],
            ["zWindow", tokens.zWindow],
          ] as const).map(([name, z]) => (
            <div key={name} className="fx-z-row">
              <code>{name}</code>
              <span>{z}</span>
            </div>
          ))}
        </div>
      </section>
    </div>
  );
}
