// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
/** Site editor — a SITE is a first-class primitive: the terminals, nodes,
 *  networks, and parameters that make up a location, not just a lat/lon.
 *
 *  Authors the grammar's Site object: identity, surface location, LAN,
 *  tags, and 1..N installed nodes (model + installed mounts + lo0/terr0).
 *  Used standalone from the Library (Sites → + new / customize, saves to
 *  user:sites/) and embedded in the ground editor for a segment's authored
 *  members. Findings warn, never block; the resolver stays the validator.
 */

import { useState } from "react";
import { Button, IconButton } from "../ui/Button";
import { readCatalogObject, saveUserObject, useBuilderCatalog } from "./useBuilderWorld";
import {
  identifier,
  siteObjectFromDraft,
  type DraftSiteNode,
  type DraftSiteObject,
} from "./workspace";

interface SiteEditorProps {
  site: DraftSiteObject;
  onUpdate: (patch: Partial<DraftSiteObject>) => void;
  /** Standalone (Library) mode shows save-to-library + close. */
  onClose?: () => void;
}

function Field({
  label,
  value,
  onChange,
  suffix,
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  suffix?: string;
}) {
  return (
    <label className="builder-field">
      <span className="builder-field-label">{label}</span>
      <span className="builder-field-input">
        <input type="text" value={value} onChange={(e) => onChange(e.target.value)} />
        {suffix && <span className="builder-field-suffix">{suffix}</span>}
      </span>
    </label>
  );
}

function NumberField({
  label,
  value,
  onChange,
  step = 0.1,
  suffix,
}: {
  label: string;
  value: number;
  onChange: (value: number) => void;
  step?: number;
  suffix?: string;
}) {
  return (
    <label className="builder-field">
      <span className="builder-field-label">{label}</span>
      <span className="builder-field-input">
        <input
          type="number"
          value={value}
          step={step}
          onChange={(e) => {
            const parsed = Number(e.target.value);
            if (Number.isFinite(parsed)) onChange(parsed);
          }}
        />
        {suffix && <span className="builder-field-suffix">{suffix}</span>}
      </span>
    </label>
  );
}

export function SiteEditor({ site, onUpdate, onClose }: SiteEditorProps) {
  const nodes = useBuilderCatalog("nodes");
  const sites = useBuilderCatalog("sites");
  const [editorError, setEditorError] = useState<string | null>(null);
  const [librarySave, setLibrarySave] = useState<
    | { kind: "idle" }
    | { kind: "saving" }
    | { kind: "conflict" }
    | { kind: "saved"; ref: string }
    | { kind: "failed"; message: string }
  >({ kind: "idle" });

  const updateNode = (index: number, patch: Partial<DraftSiteNode>) => {
    onUpdate({
      nodes: site.nodes.map((node, i) => (i === index ? { ...node, ...patch } : node)),
    });
  };

  // Switching a node's model re-seeds its installed mounts from the model's
  // faceplate — an explicit act, so the reset is expected.
  const setNodeModel = async (index: number, ref: string) => {
    setEditorError(null);
    try {
      const { document } = await readCatalogObject(ref);
      const node = (document as { node?: Record<string, unknown> }).node;
      const mounts = ((node?.terminals as Record<string, unknown>[] | undefined) ?? []).map(
        (mount) => [String(mount.id), Number(mount.count ?? 1)] as const,
      );
      updateNode(index, { model_ref: ref, installed: Object.fromEntries(mounts) });
    } catch (e) {
      setEditorError(e instanceof Error ? e.message : String(e));
    }
  };

  const addNode = () => {
    const first = site.nodes[0];
    const n = site.nodes.length + 1;
    onUpdate({
      nodes: [
        ...site.nodes,
        {
          node_id: `gw${n}`,
          model_ref: first?.model_ref ?? "",
          installed: first ? { ...first.installed } : {},
          lo0_ipv4: "",
          terr0_ipv4: "",
        },
      ],
    });
  };

  const saveToLibrary = async () => {
    setLibrarySave({ kind: "saving" });
    try {
      const entry = await saveUserObject(
        "sites",
        { site: siteObjectFromDraft(site) },
        { overwrite: librarySave.kind === "conflict" },
      );
      setLibrarySave({ kind: "saved", ref: entry.ref });
      void sites.refresh();
    } catch (e) {
      const status = (e as Error & { status?: number }).status;
      if (status === 409 && librarySave.kind !== "conflict") {
        setLibrarySave({ kind: "conflict" });
      } else {
        setLibrarySave({
          kind: "failed",
          message: e instanceof Error ? e.message : String(e),
        });
      }
    }
  };

  return (
    <div className="builder-inspector-stack" data-testid="builder-site-editor">
      <Field
        label="name"
        value={site.display_name}
        onChange={(display_name) =>
          onUpdate({ display_name, site_id: identifier(display_name) || site.site_id })
        }
      />
      <NumberField
        label="latitude"
        value={site.lat_deg}
        suffix="deg"
        onChange={(lat_deg) => onUpdate({ lat_deg })}
      />
      <NumberField
        label="longitude"
        value={site.lon_deg}
        suffix="deg"
        onChange={(lon_deg) => onUpdate({ lon_deg })}
      />
      <NumberField
        label="altitude"
        value={site.alt_m}
        step={10}
        suffix="m"
        onChange={(alt_m) => onUpdate({ alt_m })}
      />
      <Field
        label="site lan"
        value={site.lan_ipv4}
        onChange={(lan_ipv4) => onUpdate({ lan_ipv4: lan_ipv4.trim() })}
      />
      <Field
        label="tags"
        value={site.tags.join(", ")}
        onChange={(value) =>
          onUpdate({
            tags: value
              .split(/[,\s]+/)
              .map((tag) => tag.trim())
              .filter((tag) => tag.length > 0),
          })
        }
      />

      {site.nodes.map((node, index) => (
        <div className="builder-card builder-card--open" key={index}>
          <div className="builder-card-head">
            <span className="builder-card-title">{node.node_id}</span>
            {site.nodes.length > 1 && (
              <IconButton
                icon="x"
                size={12}
                label={`Remove ${node.node_id}`}
                onClick={() =>
                  onUpdate({ nodes: site.nodes.filter((_, i) => i !== index) })
                }
              />
            )}
          </div>
          <div className="builder-card-body">
            <label className="builder-field builder-field--stack">
              <span className="builder-field-label">model</span>
              <select
                aria-label={`${node.node_id} model`}
                value={node.model_ref}
                onChange={(e) => void setNodeModel(index, e.target.value)}
              >
                {nodes.entries
                  .filter((entry) => !entry.error)
                  .map((entry) => (
                    <option key={entry.ref} value={entry.ref}>
                      {entry.display_name ?? entry.id ?? entry.ref}
                    </option>
                  ))}
              </select>
            </label>
            {Object.entries(node.installed).map(([mount, count]) => (
              <label className="builder-field" key={mount}>
                <span className="builder-field-label">{mount}</span>
                <span className="builder-field-input">
                  <input
                    type="number"
                    min={1}
                    value={count}
                    onChange={(e) => {
                      const parsed = Math.max(1, Math.round(Number(e.target.value)));
                      if (Number.isFinite(parsed)) {
                        updateNode(index, {
                          installed: { ...node.installed, [mount]: parsed },
                        });
                      }
                    }}
                  />
                  <span className="builder-field-suffix">installed</span>
                </span>
              </label>
            ))}
            <Field
              label="lo0"
              value={node.lo0_ipv4}
              onChange={(lo0_ipv4) => updateNode(index, { lo0_ipv4: lo0_ipv4.trim() })}
            />
            <Field
              label="terr0"
              value={node.terr0_ipv4}
              onChange={(terr0_ipv4) => updateNode(index, { terr0_ipv4: terr0_ipv4.trim() })}
            />
          </div>
        </div>
      ))}
      <div className="builder-preset-row">
        <Button onClick={addNode}>+ add node</Button>
      </div>

      {editorError && <div className="builder-warning">{editorError}</div>}

      <div className="builder-preset-row">
        <Button
          onClick={() => void saveToLibrary()}
          disabled={librarySave.kind === "saving"}
        >
          {librarySave.kind === "conflict" ? "Overwrite in library?" : "Save to library"}
        </Button>
        {onClose && <Button onClick={onClose}>Close</Button>}
      </div>
      {librarySave.kind === "saved" && (
        <div className="builder-library-note" data-testid="library-note">
          in your library: {librarySave.ref}
        </div>
      )}
      {librarySave.kind === "failed" && (
        <div className="builder-warning">{librarySave.message}</div>
      )}
    </div>
  );
}
