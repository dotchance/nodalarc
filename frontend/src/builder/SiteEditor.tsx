// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
/** Site editor — a site contains the terminals, nodes,
 *  networks, and parameters that make up a location, not just a lat/lon.
 *
 *  Authors the grammar's Site object: identity, surface location, LAN,
 *  tags, and 1..N installed nodes (model + installed mounts + lo0/terr0).
 *  Used for transient session-authored members. Standalone catalog editing is
 *  backend-owned by CatalogDraftEditorWindow so this surface never serializes
 *  or persists a catalog document.
 */

import { useState } from "react";
import { Button, IconButton } from "../ui/Button";
import { BodySelect, EditorCard, EditorName, Field, NumberField, SelectField } from "./editorKit";
import type { BuilderVisualAuthoringFacts } from "./generated/builderApi";
import { useBuilderCatalog } from "./useBuilderWorld";
import { type DraftSiteNode, type DraftSiteObject } from "./workspace";

interface SiteEditorProps {
  site: DraftSiteObject;
  /** Functional-only: the caller reads the LATEST draft, never a stale
   *  render-closure, so a concurrent edit during an in-flight fetch survives.
   *  Reseed/replace is explicit — `onUpdate(() => replacement)`. */
  onUpdate: (update: (prev: DraftSiteObject) => DraftSiteObject) => void;
  onSetNodeModel: (nodeId: string, ref: string) => Promise<void>;
  onAddNode: () => Promise<void>;
  /** Embedded editor close action. */
  onClose?: () => void;
  /** focus the name when a create gesture opened this editor. */
  autoFocusName?: boolean;
  authoring: BuilderVisualAuthoringFacts;
}

export function SiteEditor({
  site,
  onUpdate,
  onSetNodeModel,
  onAddNode,
  onClose,
  autoFocusName = false,
  authoring,
}: SiteEditorProps) {
  const nodes = useBuilderCatalog("nodes");
  const bodies = useBuilderCatalog("bodies");
  const [editorError, setEditorError] = useState<string | null>(null);

  // Match on the stable node_id, never an array index. Direct field edits remain
  // local intent; model selection and installation seeding are backend commands.
  const updateNode = (
    node_id: string,
    patch: Partial<DraftSiteNode> | ((node: DraftSiteNode) => Partial<DraftSiteNode>),
  ) => {
    onUpdate((prev) => ({
      ...prev,
      nodes: prev.nodes.map((node) =>
        node.node_id === node_id
          ? { ...node, ...(typeof patch === "function" ? patch(node) : patch) }
          : node,
      ),
    }));
  };

  const setNodeModel = async (node_id: string, ref: string) => {
    setEditorError(null);
    try {
      await onSetNodeModel(node_id, ref);
    } catch (e) {
      setEditorError(e instanceof Error ? e.message : String(e));
    }
  };

  const addNode = async () => {
    setEditorError(null);
    try {
      await onAddNode();
    } catch (e) {
      setEditorError(e instanceof Error ? e.message : String(e));
    }
  };

  return (
    <div className="builder-inspector-stack" data-testid="builder-site-editor">
      <EditorName
        value={site.display_name}
        onChange={(display_name) =>
          onUpdate((prev) => ({
            ...prev,
            display_name,
          }))
        }
        autoFocus={autoFocusName}
      />
      <BodySelect
        label="on body"
        ariaLabel="Site body"
        value={site.body}
        onChange={(body) => onUpdate((prev) => ({ ...prev, body }))}
        bodies={bodies}
      />
      <NumberField
        label="latitude"
        value={site.lat_deg}
        suffix="deg"
        onChange={(lat_deg) => onUpdate((prev) => ({ ...prev, lat_deg }))}
      />
      <NumberField
        label="longitude"
        value={site.lon_deg}
        suffix="deg"
        onChange={(lon_deg) => onUpdate((prev) => ({ ...prev, lon_deg }))}
      />
      <NumberField
        label="altitude"
        value={site.alt_m}
        step={10}
        suffix="m"
        onChange={(alt_m) => onUpdate((prev) => ({ ...prev, alt_m }))}
      />
      <Field
        label="site lan"
        value={site.lan_ipv4}
        onChange={(lan_ipv4) => onUpdate((prev) => ({ ...prev, lan_ipv4: lan_ipv4.trim() }))}
      />
      <Field
        label="tags"
        value={site.tags.join(", ")}
        onChange={(value) =>
          onUpdate((prev) => ({
            ...prev,
            tags: value
              .split(/[,\s]+/)
              .map((tag) => tag.trim())
              .filter((tag) => tag.length > 0),
          }))
        }
      />

      {site.nodes.map((node) => (
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
                  onUpdate((prev) => ({
                    ...prev,
                    nodes: prev.nodes.filter((n) => n.node_id !== node.node_id),
                  }))
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
              onChange={(ref) => void setNodeModel(node.node_id, ref)}
              options={nodes.entries
                .map((entry) => ({
                  value: entry.ref,
                  label: entry.display_name,
                }))}
            />
            {Object.entries(node.installed).map(([mount, count]) => (
              <div key={mount}>
                <NumberField
                  label={mount}
                  value={count}
                  min={1}
                  integer
                  suffix="installed"
                  onChange={(parsed) =>
                    updateNode(node.node_id, (n) => ({
                      installed: { ...n.installed, [mount]: parsed },
                    }))
                  }
                />
                <SelectField
                  label={`${mount} ground boresight`}
                  value={node.boresights[mount]?.mode ?? ""}
                  onChange={(mode) =>
                    updateNode(node.node_id, (current) => {
                      const boresights = { ...current.boresights };
                      if (mode === authoring.ground_access_boresight.mode) {
                        boresights[mount] = { ...authoring.ground_access_boresight };
                      } else {
                        delete boresights[mount];
                      }
                      return { boresights };
                    })
                  }
                  options={[
                    { value: "", label: "none" },
                    {
                      value: authoring.ground_access_boresight.mode,
                      label: authoring.ground_access_boresight.mode.replace(/_/g, " "),
                    },
                  ]}
                />
              </div>
            ))}
            <Field
              label="lo0"
              value={node.lo0_ipv4}
              onChange={(lo0_ipv4) => updateNode(node.node_id, { lo0_ipv4: lo0_ipv4.trim() })}
            />
            <Field
              label="terr0"
              value={node.terr0_ipv4}
              onChange={(terr0_ipv4) => updateNode(node.node_id, { terr0_ipv4: terr0_ipv4.trim() })}
            />
        </EditorCard>
      ))}
      <div className="builder-preset-row">
        <Button onClick={() => void addNode()}>+ add node</Button>
      </div>

      {editorError && <div className="builder-warning">{editorError}</div>}

      {onClose && <Button onClick={onClose}>Close</Button>}
    </div>
  );
}
