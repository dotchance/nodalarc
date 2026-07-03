// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
/** The Library — one surface for every primitive family, both tiers.
 *
 *  Family tabs,
 *  shipped + your entries together (yours marked ★), every entry showing its
 *  hardware line, an explicit USE action per entry, customize-to-fork, file
 *  import, export, delete for yours. Blank-first: "+ new" leads each tab
 *  where an editor exists. Click a row to inspect the block; Use places it.
 *
 *  Visual language is the world tree's, not its own: rows speak
 *  builder-outline-name + family glyph (orbit/orange for space assets,
 *  satellite-dish/teal for ground, neutral for component hardware), and all
 *  row actions are uniform IconButtons — the primary "use" differs by color
 *  only, never by shape.
 */

import { useState } from "react";
import { Button, IconButton } from "../ui/Button";
import { Icon, type IconName } from "../ui/icons/Icon";
import {
  deleteUserObject,
  exportCatalogObject,
  importUserObjectYaml,
  useBuilderCatalog,
} from "./useBuilderWorld";
import type { BuilderCatalogEntry } from "./builderTypes";

interface FamilyConfig {
  family: string;
  label: string;
  /** Row glyph — the same vocabulary the world tree uses. */
  icon: IconName;
  /** Color slot: space=orange, ground=teal (world-tree hierarchy colors);
   *  component hardware stays neutral — color means world placement. */
  tone: "space" | "ground" | "component";
  /** What USE does for this family; null = no direct session placement. */
  useTitle: string | null;
  /** Whether an authoring editor exists for "+ new" / customize. */
  editor: boolean;
}

const CONSTELLATIONS: FamilyConfig = {
  family: "constellations",
  label: "Constellations",
  icon: "orbit",
  tone: "space",
  useTitle: "Use: place as a space segment",
  editor: true,
};

const FAMILIES: FamilyConfig[] = [
  CONSTELLATIONS,
  {
    family: "site-sets",
    label: "Site sets",
    icon: "satellite-dish",
    tone: "ground",
    useTitle: "Use: place as ground sites",
    editor: true,
  },
  {
    family: "sites",
    label: "Sites",
    icon: "locate-fixed",
    tone: "ground",
    useTitle: "Use: add to a ground segment",
    editor: true,
  },
  {
    family: "nodes",
    label: "Nodes",
    icon: "satellite",
    tone: "component",
    useTitle: "Use: start a constellation with this node",
    editor: true,
  },
  {
    family: "terminals",
    label: "Terminals",
    icon: "radio-tower",
    tone: "component",
    useTitle: null,
    editor: true,
  },
  {
    family: "orbits",
    label: "Orbits",
    icon: "spline",
    tone: "component",
    useTitle: null,
    // orbits are authored inside a constellation's orbit card
    editor: false,
  },
];

interface LibraryPanelProps {
  onUse: (entry: BuilderCatalogEntry) => void;
  onCustomize: (entry: BuilderCatalogEntry) => void;
  onInspect: (entry: BuilderCatalogEntry) => void;
  onNew: (family: string) => void;
}

export function LibraryPanel({ onUse, onCustomize, onInspect, onNew }: LibraryPanelProps) {
  const [config, setConfig] = useState<FamilyConfig>(CONSTELLATIONS);
  const family = config.family;
  const catalog = useBuilderCatalog(family);
  const [importError, setImportError] = useState<string | null>(null);
  const canCustomize = config.editor;
  // Source filter: shipped vs yours. Your saves land at the end of a long
  // shipped list, so without this they read as missing.
  const [source, setSource] = useState<"all" | "nodalarc" | "user">("all");
  const visibleEntries = catalog.entries.filter(
    (entry) =>
      source === "all" ||
      (source === "user") === entry.ref.startsWith("user:"),
  );

  return (
    <div className="builder-outline-group" data-testid="builder-library">
      <div className="builder-outline-kind">Library</div>
      <div className="builder-library-tabs" role="tablist">
        {FAMILIES.map((tab) => (
          <button
            key={tab.family}
            role="tab"
            aria-selected={family === tab.family}
            className={`builder-library-tab${family === tab.family ? " builder-library-tab--active" : ""}`}
            onClick={() => setConfig(tab)}
          >
            {tab.label}
          </button>
        ))}
      </div>
      <div className="builder-preset-row">
        {config.editor && <Button onClick={() => onNew(family)}>+ new</Button>}
        <span className="builder-source-filter" role="radiogroup" aria-label="Library source">
          <Button active={source === "all"} onClick={() => setSource("all")}>
            all
          </Button>
          <Button active={source === "nodalarc"} onClick={() => setSource("nodalarc")}>
            nodalarc
          </Button>
          <Button active={source === "user"} onClick={() => setSource("user")}>
            ★ yours
          </Button>
        </span>
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
        {visibleEntries.length === 0 && source === "user" && (
          <div className="builder-zone-empty">
            nothing of yours in this family yet — + new or save from an editor
          </div>
        )}
        {visibleEntries.map((entry) => {
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
                <span
                  className={`builder-outline-name builder-outline-name--${config.tone} builder-library-entry-name`}
                >
                  <Icon name={config.icon} size={12} />
                  <span className="builder-library-entry-text">
                    {yours ? "★ " : ""}
                    {entry.display_name ?? entry.id}
                  </span>
                </span>
                {entry.summary && (
                  <span className="builder-library-entry-summary">{entry.summary}</span>
                )}
              </button>
              <span className="builder-library-actions">
                {config.useTitle && (
                  <IconButton
                    icon="plus"
                    size={12}
                    label={config.useTitle}
                    className="builder-library-use"
                    onClick={() => onUse(entry)}
                  />
                )}
                {canCustomize && (
                  <IconButton
                    icon="pencil"
                    size={12}
                    label="Customize: fork into an editable draft"
                    onClick={() => onCustomize(entry)}
                  />
                )}
                <IconButton
                  icon="download"
                  size={12}
                  label="Export file"
                  onClick={() => void exportCatalogObject(entry.ref)}
                />
                {yours && (
                  <IconButton
                    icon="x"
                    size={12}
                    label="Delete from your library"
                    onClick={() => void deleteUserObject(entry.ref)}
                  />
                )}
              </span>
            </div>
          );
        })}
      </div>
    </div>
  );
}
