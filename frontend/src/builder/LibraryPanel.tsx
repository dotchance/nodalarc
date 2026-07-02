// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
/** The Library — one surface for every primitive family, both tiers.
 *
 *  The validated design (carried from the discovery arc): family tabs,
 *  shipped + your entries together (yours marked ★), every entry showing its
 *  hardware line, an explicit USE action per entry, customize-to-fork, file
 *  import, export, delete for yours. Blank-first: "+ new" leads each tab
 *  where an editor exists. Click a row to inspect the block; Use places it.
 */

import { useState } from "react";
import { Button } from "../ui/Button";
import {
  deleteUserObject,
  exportCatalogObject,
  importUserObjectYaml,
  useBuilderCatalog,
} from "./useBuilderWorld";
import type { BuilderCatalogEntry } from "./builderTypes";

const FAMILY_TABS: { family: string; label: string }[] = [
  { family: "constellations", label: "Constellations" },
  { family: "site-sets", label: "Site sets" },
  { family: "nodes", label: "Nodes" },
  { family: "terminals", label: "Terminals" },
  { family: "orbits", label: "Orbits" },
];

/** Per-family Use semantics; families without a direct session placement
 *  offer customize/export instead of a dead button. */
const USE_LABEL: Record<string, string | null> = {
  constellations: "use",
  "site-sets": "use",
  nodes: "use",
  terminals: null,
  orbits: null,
};

const NEW_ENABLED: Record<string, boolean> = {
  constellations: true,
  "site-sets": false, // ground authoring lands with S4's editor
  nodes: true,
  terminals: true,
  orbits: false, // orbits are authored inside a constellation's orbit card
};

interface LibraryPanelProps {
  onUse: (entry: BuilderCatalogEntry) => void;
  onCustomize: (entry: BuilderCatalogEntry) => void;
  onInspect: (entry: BuilderCatalogEntry) => void;
  onNew: (family: string) => void;
}

export function LibraryPanel({ onUse, onCustomize, onInspect, onNew }: LibraryPanelProps) {
  const [family, setFamily] = useState("constellations");
  const catalog = useBuilderCatalog(family);
  const [importError, setImportError] = useState<string | null>(null);
  const canCustomize = family === "constellations" || family === "nodes" || family === "terminals";

  return (
    <div className="builder-outline-group" data-testid="builder-library">
      <div className="builder-outline-kind">Library</div>
      <div className="builder-library-tabs" role="tablist">
        {FAMILY_TABS.map((tab) => (
          <button
            key={tab.family}
            role="tab"
            aria-selected={family === tab.family}
            className={`builder-library-tab${family === tab.family ? " builder-library-tab--active" : ""}`}
            onClick={() => setFamily(tab.family)}
          >
            {tab.label}
          </button>
        ))}
      </div>
      <div className="builder-preset-row">
        {NEW_ENABLED[family] && (
          <Button onClick={() => onNew(family)}>+ new</Button>
        )}
        <label className="builder-import-label">
          import file…
          <input
            type="file"
            accept=".yaml,.yml"
            style={{ display: "none" }}
            onChange={(e) => {
              const file = e.target.files?.[0];
              if (!file) return;
              void file.text().then(async (text) => {
                try {
                  await importUserObjectYaml(text);
                  setImportError(null);
                } catch (err) {
                  setImportError(err instanceof Error ? err.message : String(err));
                }
              });
              e.target.value = "";
            }}
          />
        </label>
      </div>
      {importError && <div className="builder-warning">{importError}</div>}
      {catalog.error && <div className="builder-warning">{catalog.error}</div>}
      <div className="builder-library-list">
        {catalog.entries.map((entry) => {
          const yours = entry.ref.startsWith("user:");
          if (entry.error) {
            return (
              <div className="builder-library-entry" key={entry.ref}>
                <div className="builder-library-entry-name builder-status-item--error">
                  {entry.ref} — {entry.error}
                </div>
              </div>
            );
          }
          return (
            <div className="builder-library-entry" key={entry.ref}>
              <button
                className="builder-library-entry-main"
                title={`Inspect ${entry.ref}`}
                onClick={() => onInspect(entry)}
              >
                <span className="builder-library-entry-name">
                  {yours ? "★ " : ""}
                  {entry.display_name ?? entry.id}
                </span>
                {entry.summary && (
                  <span className="builder-library-entry-summary">{entry.summary}</span>
                )}
              </button>
              <span className="builder-library-actions">
                {USE_LABEL[family] && (
                  <button
                    className="builder-library-action builder-library-action--use"
                    title="Use this block in the session"
                    onClick={() => onUse(entry)}
                  >
                    {USE_LABEL[family]}
                  </button>
                )}
                {canCustomize && (
                  <button
                    className="builder-library-action"
                    title="Fork into an editable draft"
                    onClick={() => onCustomize(entry)}
                  >
                    edit
                  </button>
                )}
                <button
                  className="builder-library-action"
                  title="Export file"
                  onClick={() => void exportCatalogObject(entry.ref)}
                >
                  ⤓
                </button>
                {yours && (
                  <button
                    className="builder-library-action"
                    title="Delete from your library"
                    onClick={() => void deleteUserObject(entry.ref)}
                  >
                    ✕
                  </button>
                )}
              </span>
            </div>
          );
        })}
      </div>
    </div>
  );
}
