// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
/** Site editor — a site is a first-class primitive: the terminals, nodes,
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
import { BodySelect, EditorCard, EditorName, Field, NumberField, SelectField } from "./editorKit";
import {
  LIBRARY_SAVE_COPY,
  readCatalogObject,
  useBuilderCatalog,
  useLibrarySave,
} from "./useBuilderWorld";
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
  /** IG-2: focus the name when a create gesture opened this editor. */
  autoFocusName?: boolean;
  /** D7: reported when the site is saved to the library. Embedded in a ground
   *  set, the host converges the authored member to this ref immediately. */
  onSaved?: (ref: string, savedObject: Record<string, unknown>) => void;
}

export function SiteEditor({
  site,
  onUpdate,
  onClose,
  autoFocusName = false,
  onSaved,
}: SiteEditorProps) {
  const nodes = useBuilderCatalog("nodes");
  const bodies = useBuilderCatalog("bodies");
  const [editorError, setEditorError] = useState<string | null>(null);
  const librarySave = useLibrarySave("sites");

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
    // Pick the first free gw{k} against the taken set — never length+1, which
    // re-collides after a delete-then-add (N27) and would duplicate the
    // node_id React key.
    const taken = new Set(site.nodes.map((node) => node.node_id));
    let k = 1;
    while (taken.has(`gw${k}`)) k += 1;
    onUpdate({
      nodes: [
        ...site.nodes,
        {
          node_id: `gw${k}`,
          model_ref: first?.model_ref ?? "",
          installed: first ? { ...first.installed } : {},
          lo0_ipv4: "",
          terr0_ipv4: "",
        },
      ],
    });
  };

  // A standalone (Library) site save has no post-save consequence and passes no
  // onSaved. Embedded in a ground set the host wires onSaved to converge the
  // authored member to the saved ref (D7).
  const saveToLibrary = () => void librarySave.save({ site: siteObjectFromDraft(site) }, onSaved);

  return (
    <div className="builder-inspector-stack" data-testid="builder-site-editor">
      <EditorName
        value={site.display_name}
        onChange={(display_name) =>
          onUpdate({ display_name, site_id: identifier(display_name) || site.site_id })
        }
        autoFocus={autoFocusName}
      />
      <BodySelect
        label="on body"
        ariaLabel="Site body"
        value={site.body}
        onChange={(body) => onUpdate({ body })}
        bodies={bodies}
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
        <EditorCard
          key={node.node_id}
          title={node.node_id}
          open
          actions={
            site.nodes.length > 1 && (
              <IconButton
                icon="x"
                size={12}
                label={`Remove ${node.node_id}`}
                onClick={() =>
                  onUpdate({ nodes: site.nodes.filter((_, i) => i !== index) })
                }
              />
            )
          }
        >
          <SelectField
              stack
              label="model"
              ariaLabel={`${node.node_id} model`}
              value={node.model_ref}
              onChange={(ref) => void setNodeModel(index, ref)}
              options={nodes.entries
                .filter((entry) => !entry.error)
                .map((entry) => ({
                  value: entry.ref,
                  label: entry.display_name ?? entry.id ?? entry.ref,
                }))}
            />
            {Object.entries(node.installed).map(([mount, count]) => (
              <NumberField
                key={mount}
                label={mount}
                value={count}
                min={1}
                integer
                suffix="installed"
                onChange={(parsed) =>
                  updateNode(index, { installed: { ...node.installed, [mount]: parsed } })
                }
              />
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
        </EditorCard>
      ))}
      <div className="builder-preset-row">
        <Button onClick={addNode}>+ add node</Button>
      </div>

      {editorError && <div className="builder-warning">{editorError}</div>}

      <div className="builder-preset-row">
        <Button onClick={saveToLibrary} disabled={librarySave.saving}>
          {librarySave.label("Save to library")}
        </Button>
        {onClose && <Button onClick={onClose}>Close</Button>}
      </div>
      {librarySave.state.kind === "saved" && (
        <div className="builder-library-note" data-testid="library-note">
          {LIBRARY_SAVE_COPY.savedNote(librarySave.state.ref)}
        </div>
      )}
      {librarySave.state.kind === "failed" && (
        <div className="builder-warning">{librarySave.state.message}</div>
      )}
    </div>
  );
}
