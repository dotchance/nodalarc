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

import { useEffect, useMemo, useState } from "react";
import { Button, IconButton } from "../ui/Button";
import { Icon, type IconName } from "../ui/icons/Icon";
import { Field } from "./editorKit";
import {
  claimLibraryReveal,
  deleteUserObject,
  exportCatalogObject,
  useBuilderBootstrap,
  useBuilderCatalog,
  useLibraryReveal,
} from "./useBuilderWorld";
import type { CatalogDocumentSummary, CatalogFamily } from "./generated/builderApi";

interface FamilyConfig {
  family: CatalogFamily;
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

const FAMILY_PRESENTATION: Partial<Record<CatalogFamily, FamilyConfig>> = {
  constellations: CONSTELLATIONS,
  "site-sets": {
    family: "site-sets",
    label: "Site sets",
    icon: "satellite-dish",
    tone: "ground",
    useTitle: "Use: place as ground sites",
    editor: true,
  },
  sites: {
    family: "sites",
    label: "Sites",
    icon: "locate-fixed",
    tone: "ground",
    useTitle: "Use: add to a ground segment",
    editor: true,
  },
  nodes: {
    family: "nodes",
    label: "Nodes",
    icon: "satellite",
    tone: "component",
    useTitle: "Use: start a constellation with this node",
    editor: true,
  },
  terminals: {
    family: "terminals",
    label: "Terminals",
    icon: "radio-tower",
    tone: "component",
    useTitle: null,
    editor: true,
  },
  orbits: {
    family: "orbits",
    label: "Orbits",
    icon: "spline",
    tone: "component",
    useTitle: null,
    editor: true,
  },
  bodies: {
    family: "bodies",
    label: "Bodies",
    icon: "earth",
    tone: "component",
    useTitle: null,
    editor: true,
  },
  payloads: {
    family: "payloads",
    label: "Payloads",
    icon: "layers",
    tone: "component",
    useTitle: null,
    editor: true,
  },
  "space-node-sets": {
    family: "space-node-sets",
    label: "Space node sets",
    icon: "satellite",
    tone: "space",
    useTitle: "Use: place as a space segment",
    editor: true,
  },
};

function fallbackFamilyPresentation(family: CatalogFamily): FamilyConfig {
  const words = family.replace(/-/g, " ");
  return {
    family,
    label: words.charAt(0).toUpperCase() + words.slice(1),
    icon: "layers",
    tone: "component",
    useTitle: null,
    editor: false,
  };
}

export function presentationForFamily(family: CatalogFamily): FamilyConfig {
  return FAMILY_PRESENTATION[family] ?? fallbackFamilyPresentation(family);
}

interface LibraryPanelProps {
  onUse: (entry: CatalogDocumentSummary) => void;
  onCustomize: (entry: CatalogDocumentSummary, targetRef: string) => Promise<void>;
  onInspect: (entry: CatalogDocumentSummary) => void;
  onNew: (family: string, objectId: string) => Promise<void>;
}

function objectId(entry: CatalogDocumentSummary): string {
  return (entry.ref.split("/").pop() ?? entry.ref).replace(/\.ya?ml$/, "");
}

export function LibraryPanel({ onUse, onCustomize, onInspect, onNew }: LibraryPanelProps) {
  const [config, setConfig] = useState<FamilyConfig>(CONSTELLATIONS);
  const bootstrap = useBuilderBootstrap();
  const families = useMemo(
    () =>
      bootstrap.bootstrap?.families
        .map((metadata) => presentationForFamily(metadata.family)) ?? [],
    [bootstrap.bootstrap],
  );
  const familyMetadata = bootstrap.bootstrap?.families.find(
    (metadata) => metadata.family === config.family,
  );
  const family = config.family;
  const catalog = useBuilderCatalog(family);
  const [actionError, setActionError] = useState<string | null>(null);
  // The backend decides whether a family can be forked. Every component family
  // has the full-document JSON fallback even when it has no specialized form.
  const canCustomize = familyMetadata?.component_fork === true;
  const [forkDraft, setForkDraft] = useState<{
    entry: CatalogDocumentSummary;
    id: string;
    saving: boolean;
  } | null>(null);
  const [newDraft, setNewDraft] = useState<{
    id: string;
    saving: boolean;
  } | null>(null);
  // Source filter: shipped vs yours. Your saves land at the end of a long
  // shipped list, so without this they read as missing.
  const [source, setSource] = useState<"all" | "nodalarc" | "user">("all");
  // A save is never a dead end: the panel lands on the saved asset — its
  // family tab, a filter that shows it, scrolled into view, highlighted.
  // Claimed via the module-level retired-nonce registry so a remounted
  // panel never replays the last save while a late-mounting panel with an
  // unseen save still lands on it.
  const reveal = useLibraryReveal();
  const [flashRef, setFlashRef] = useState<string | null>(null);
  useEffect(() => {
    if (families.length > 0 && !families.some((entry) => entry.family === config.family)) {
      setConfig(families[0]!);
    }
  }, [config.family, families]);
  useEffect(() => {
    const claimed = claimLibraryReveal("lander", reveal);
    if (!claimed) return;
    const target = families.find((f) => f.family === claimed.entry.family);
    if (target) setConfig(target);
    if (source === "nodalarc" && claimed.entry.namespace === "user") setSource("all");
    setFlashRef(claimed.entry.ref);
    const timer = setTimeout(() => setFlashRef(null), 2600);
    return () => clearTimeout(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [reveal, families]);
  useEffect(() => {
    if (!flashRef) return;
    const escaped =
      typeof CSS !== "undefined" && CSS.escape ? CSS.escape(flashRef) : flashRef;
    document
      .querySelector(`[data-library-ref="${escaped}"]`)
      ?.scrollIntoView?.({ block: "center" });
    // Re-run only on a new reveal (a fresh flashRef) or after the family list
    // refreshes — the target row may not exist on the render that set flashRef.
    // Without deps this fired every render and fought the user's own scroll.
  }, [flashRef, catalog.entries]);
  const visibleEntries = catalog.entries.filter(
    (entry) =>
      source === "all" ||
      (source === "user") === (entry.namespace === "user"),
  );

  const renderEntry = (entry: CatalogDocumentSummary) => {
    const yours = entry.namespace === "user";
    return (
      <div
        className={`builder-library-entry${flashRef === entry.ref ? " builder-library-entry--saved" : ""}`}
        data-library-ref={entry.ref}
        key={entry.ref}
      >
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
              {entry.display_name}
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
              label="Customize: fork into your library"
              onClick={() => {
                if (yours) {
                  void onCustomize(entry, entry.ref).catch((error) =>
                    setActionError(error instanceof Error ? error.message : String(error)),
                  );
                  return;
                }
                setForkDraft({
                  entry,
                  id: `${objectId(entry)}-custom`,
                  saving: false,
                });
              }}
            />
          )}
          <IconButton
            icon="download"
            size={12}
            label="Export file"
            onClick={() => {
              void exportCatalogObject(entry.ref).then(
                () => setActionError(null),
                (error) => setActionError(error instanceof Error ? error.message : String(error)),
              );
            }}
          />
          {yours && (
            <IconButton
              icon="x"
              size={12}
              label="Delete from your library"
              onClick={() => {
                void deleteUserObject(entry.ref).then(
                  () => setActionError(null),
                  (error) => setActionError(error instanceof Error ? error.message : String(error)),
                );
              }}
            />
          )}
        </span>
      </div>
    );
  };

  return (
    <div className="builder-outline-group" data-testid="builder-library">
      <div className="builder-outline-kind">Library</div>
      {bootstrap.error && (
        <div className="builder-warning" data-testid="builder-bootstrap-error">
          {bootstrap.error} <Button onClick={() => void bootstrap.refresh()}>retry</Button>
        </div>
      )}
      {!bootstrap.bootstrap && !bootstrap.error && (
        <div className="builder-zone-empty">loading catalog capabilities…</div>
      )}
      <div className="builder-library-tabs" role="tablist">
        {families.map((tab) => (
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
        {config.editor && familyMetadata?.direct_user_write === true && (
          <Button
            onClick={() =>
              setNewDraft({
                id: familyMetadata.suggested_object_id ?? "",
                saving: false,
              })
            }
          >
            + new
          </Button>
        )}
        <span role="radiogroup" aria-label="Library source">
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
      </div>
      {forkDraft && (
        <div className="builder-inspector-stack" data-testid="builder-fork-draft">
          <Field
            label="new id"
            value={forkDraft.id}
            onChange={(id) => setForkDraft((current) => current && { ...current, id })}
            suffix={`user:${config.family}/….yaml`}
          />
          <div className="builder-zone-empty">
            target: user:{config.family}/{forkDraft.id.trim() || "invalid-id"}.yaml
          </div>
          <div className="builder-preset-row">
            <Button
              variant="primary"
              disabled={forkDraft.saving || forkDraft.id.trim().length === 0}
              onClick={() => {
                const id = forkDraft.id.trim();
                if (!id) return;
                setForkDraft((current) => current && { ...current, saving: true });
                void onCustomize(
                  forkDraft.entry,
                  `user:${forkDraft.entry.family}/${id}.yaml`,
                ).then(
                  () => {
                    setActionError(null);
                    setForkDraft(null);
                  },
                  (error) => {
                    setActionError(error instanceof Error ? error.message : String(error));
                    setForkDraft((current) => current && { ...current, saving: false });
                  },
                );
              }}
            >
              {forkDraft.saving ? "Forking…" : "Fork"}
            </Button>
            <Button disabled={forkDraft.saving} onClick={() => setForkDraft(null)}>
              Cancel
            </Button>
          </div>
        </div>
      )}
      {newDraft && (
        <div className="builder-inspector-stack" data-testid="builder-new-catalog-draft">
          <Field
            label="new id"
            value={newDraft.id}
            onChange={(id) => setNewDraft((current) => current && { ...current, id })}
            suffix={`user:${config.family}/….yaml`}
          />
          <div className="builder-zone-empty">
            target: user:{config.family}/{newDraft.id.trim() || "invalid-id"}.yaml
          </div>
          <div className="builder-preset-row">
            <Button
              variant="primary"
              disabled={newDraft.saving || newDraft.id.trim().length === 0}
              onClick={() => {
                const id = newDraft.id.trim();
                if (!id) return;
                setNewDraft((current) => current && { ...current, saving: true });
                void onNew(config.family, id).then(
                  () => {
                    setActionError(null);
                    setNewDraft(null);
                  },
                  (error) => {
                    setActionError(error instanceof Error ? error.message : String(error));
                    setNewDraft((current) => current && { ...current, saving: false });
                  },
                );
              }}
            >
              {newDraft.saving ? "Creating…" : "Create draft"}
            </Button>
            <Button disabled={newDraft.saving} onClick={() => setNewDraft(null)}>
              Cancel
            </Button>
          </div>
        </div>
      )}
      {actionError && <div className="builder-warning">{actionError}</div>}
      {catalog.error && (
        <div className="builder-warning" data-testid="library-catalog-error">
          {catalog.error}{" "}
          <Button onClick={() => void catalog.refresh()}>retry</Button>
        </div>
      )}
      <div className="builder-library-list">
        {visibleEntries.length === 0 && source === "user" && (
          <div className="builder-zone-empty">
            nothing of yours in this family yet — + new or save from an editor
          </div>
        )}
        {visibleEntries.map((entry, index) => {
          // Tier seam: yours lead, and the boundary to the shipped set is
          // labeled — an invisible sort still read as "only nodalarc here".
          const startsShipped =
            entry.namespace === "nodalarc" &&
            (index === 0 || visibleEntries[index - 1]!.namespace === "user");
          const tierLabel =
            source === "all" && startsShipped && visibleEntries.some((e) => e.namespace === "user") ? (
              <div className="builder-library-tier" key={`tier-${entry.ref}`}>
                nodalarc library
              </div>
            ) : null;
          return [tierLabel, renderEntry(entry)];
        })}
      </div>
    </div>
  );
}
