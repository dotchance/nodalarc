// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
/** Ground segment editor — a segment is a combination of defined sites.
 *
 *  A site contains nodes, terminals, and networks at a
 *  location); members here are either references to defined sites — full
 *  fidelity, their nodes travel with them — or authored site drafts edited
 *  in place with the SiteEditor. Bulk paste mints full sites using the
 *  segment's stamp (node model + addressing bases, applied at creation;
 *  every minted site owns its configuration afterwards). Scheduling is an
 *  intent preset writing the full explicit block, with sparse per-site
 *  overrides ("= template", only exceptions stored). Findings warn, never
 *  block; the resolver's verdict arrives verbatim via backend compile.
 */

import { useState } from "react";
import { Button, IconButton } from "../ui/Button";
import { Icon } from "../ui/icons/Icon";
import {
  BodySelect,
  EditorCard,
  EditorName,
  Field,
  InlineSelect,
  NumberField,
  PasteArea,
  SelectField,
} from "./editorKit";
import { SegmentLinksCard } from "./SegmentLinksCard";
import { SiteEditor } from "./SiteEditor";
import { useBuilderCatalog } from "./useBuilderWorld";
import {
  groundWarnings,
  parseSiteLines,
  type DraftGroundSet,
  type ParsedSiteLine,
  type Workspace,
} from "./workspace";
import type {
  BuilderVisualAuthoringFacts,
  BuilderVisualSchedulingPreset,
  BuilderVisualSchedulingPresetMetadata,
} from "./generated/builderApi";

interface GroundEditorProps {
  draft: DraftGroundSet;
  /** Functional-only: the caller reads the LATEST draft, never a stale
   *  render-closure, so a concurrent edit during an in-flight model fetch
   *  survives. */
  onUpdate: (update: (prev: DraftGroundSet) => DraftGroundSet) => void;
  onMintSites: (sites: ParsedSiteLine[]) => Promise<void>;
  onAddSiteReference: (ref: string) => Promise<void>;
  onSetStampNodeModel: (ref: string) => Promise<void>;
  onSetSiteNodeModel: (memberId: string, nodeId: string, ref: string) => Promise<void>;
  onAddSiteNode: (memberId: string) => Promise<void>;
  onRemove: () => void;
  /** focus the name when a create gesture opened this editor. */
  autoFocusName?: boolean;
  /** Connect gesture context ("+ link to…" on the segment). */
  workspace: Workspace;
  onOpenRule: (ruleId: string) => void;
  onConnect: (targetSegmentId: string) => void;
  schedulingPresets: readonly BuilderVisualSchedulingPresetMetadata[];
  selectedSchedulingPreset: BuilderVisualSchedulingPreset | null;
  memberSchedulingPreset: (memberId: string) => BuilderVisualSchedulingPreset | null;
  onSchedulingPreset: (
    preset: BuilderVisualSchedulingPreset | null,
    memberId?: string,
  ) => Promise<void>;
  authoring: BuilderVisualAuthoringFacts;
}

/** Parse a comma/space separated tag or prefix list; empty tokens drop. */
function tokenList(value: string): string[] {
  return value
    .split(/[,\s]+/)
    .map((token) => token.trim())
    .filter((token) => token.length > 0);
}

export function GroundEditor({
  draft,
  onUpdate,
  onMintSites,
  onAddSiteReference,
  onSetStampNodeModel,
  onSetSiteNodeModel,
  onAddSiteNode,
  onRemove,
  autoFocusName = false,
  workspace,
  onOpenRule,
  onConnect,
  schedulingPresets,
  selectedSchedulingPreset,
  memberSchedulingPreset,
  onSchedulingPreset,
  authoring,
}: GroundEditorProps) {
  const [openCard, setOpenCard] = useState<string | null>("sites");
  const toggle = (id: string) => setOpenCard((prev) => (prev === id ? null : id));
  const nodes = useBuilderCatalog("nodes");
  const siteCatalog = useBuilderCatalog("sites");
  const bodies = useBuilderCatalog("bodies");
  const [pasteText, setPasteText] = useState("");
  const [pasteErrors, setPasteErrors] = useState<string[]>([]);
  const [libraryOpen, setLibraryOpen] = useState(false);
  const [editingMember, setEditingMember] = useState<string | null>(null);
  const [editorError, setEditorError] = useState<string | null>(null);
  const [minting, setMinting] = useState(false);
  const warnings = groundWarnings(draft);

  const setSchedulingPreset = (
    preset: BuilderVisualSchedulingPreset | null,
    memberId?: string,
  ) => {
    setEditorError(null);
    void onSchedulingPreset(preset, memberId).catch((cause) =>
      setEditorError(cause instanceof Error ? cause.message : String(cause)),
    );
  };

  const addPastedSites = async () => {
    const { rows, errors } = parseSiteLines(pasteText);
    setPasteErrors(errors);
    if (rows.length > 0) {
      setMinting(true);
      setEditorError(null);
      try {
        await onMintSites(rows);
        setPasteText("");
      } catch (cause) {
        setEditorError(cause instanceof Error ? cause.message : String(cause));
      } finally {
        setMinting(false);
      }
    }
  };

  // Place a defined site by reference — full fidelity, its nodes travel.
  const addFromLibrary = async (ref: string) => {
    setEditorError(null);
    try {
      await onAddSiteReference(ref);
    } catch (cause) {
      setEditorError(cause instanceof Error ? cause.message : String(cause));
    }
  };

  const setStampModel = async (ref: string) => {
    setEditorError(null);
    try {
      await onSetStampNodeModel(ref);
    } catch (e) {
      setEditorError(e instanceof Error ? e.message : String(e));
    }
  };

  const stampLabel =
    draft.stamp.node_ref?.split("/").pop()?.replace(/\.yaml$/, "") || "pick a model";

  return (
    <div className="builder-inspector-stack" data-testid="builder-ground-editor">
      <EditorName
        value={draft.display_name}
        onChange={(display_name) => onUpdate((prev) => ({ ...prev, display_name }))}
        autoFocus={autoFocusName}
      />

      <EditorCard
        title="Sites"
        open={openCard === "sites"}
        onToggle={() => toggle("sites")}
        summary={draft.members.length === 1 ? "1 site" : `${draft.members.length} sites`}
      >
            {draft.members.map((member) => (
              <div key={member.member_id}>
                <div className="builder-site-row">
                  <div className="builder-site-head">
                    <span className="builder-site-name">
                      <Icon name="locate-fixed" size={12} />
                      {member.label}
                    </span>
                    <InlineSelect
                      className="builder-ground-preset"
                      ariaLabel={`${member.label} scheduling`}
                      title="Per-site scheduling — only exceptions are stored"
                      value={
                        member.scheduling_override === null
                          ? ""
                          : (memberSchedulingPreset(member.member_id) ?? "__custom__")
                      }
                      onChange={(value) => {
                        if (value === "__custom__") return;
                        setSchedulingPreset(
                          value === "" ? null : (value as BuilderVisualSchedulingPreset),
                          member.member_id,
                        );
                      }}
                      options={[
                        { value: "", label: "= template" },
                        ...(member.scheduling_override !== null &&
                        memberSchedulingPreset(member.member_id) === null
                          ? [{ value: "__custom__", label: "Imported block (custom)" }]
                          : []),
                        ...schedulingPresets.map((preset) => ({
                          value: preset.id,
                          label: preset.label,
                        })),
                      ]}
                    />
                    <IconButton
                      icon="pencil"
                      size={12}
                      label={
                        member.kind === "ref"
                          ? `Customize ${member.label}: fork into an editable site`
                          : `Edit ${member.label}`
                      }
                      onClick={() => {
                        if (member.kind === "ref") {
                          setEditorError(
                            `Customize ${member.ref} from the Library, then update this session to use the new user: reference.`,
                          );
                        } else {
                          setEditingMember((prev) =>
                            prev === member.member_id ? null : member.member_id,
                          );
                        }
                      }}
                    />
                    <IconButton
                      icon="x"
                      size={12}
                      label={`Remove ${member.label}`}
                      onClick={() =>
                        onUpdate((prev) => ({
                          ...prev,
                          members: prev.members.filter((m) => m.member_id !== member.member_id),
                        }))
                      }
                    />
                  </div>
                  <span className="builder-site-derived">
                    {member.kind === "ref"
                      ? (member.summary ?? member.ref)
                      : member.site
                        ? `authored · lan ${member.site.lan_ipv4} · ${
                            member.site.nodes.length === 1
                              ? "1 node"
                              : `${member.site.nodes.length} nodes`
                          }`
                        : ""}
                  </span>
                </div>
                {member.kind === "draft" &&
                  member.site &&
                  editingMember === member.member_id && (
                    <div className="builder-site-embedded">
                      <SiteEditor
                        authoring={authoring}
                        site={member.site}
                        onSetNodeModel={(nodeId, ref) =>
                          onSetSiteNodeModel(member.member_id, nodeId, ref)
                        }
                        onAddNode={() => onAddSiteNode(member.member_id)}
                        onUpdate={(update) =>
                          // Thread SiteEditor's functional update through the
                          // ground's own — find the member in the LATEST members
                          // and update its LATEST site, so a concurrent edit
                          // (this member or another) during a site-level fetch
                          // survives.
                          onUpdate((prev) => ({
                            ...prev,
                            members: prev.members.map((m) => {
                              if (m.member_id !== member.member_id || !m.site) return m;
                              const site = update(m.site);
                              return { ...m, site, site_id: site.site_id, label: site.display_name };
                            }),
                          }))
                        }
                        onClose={() => setEditingMember(null)}
                      />
                    </div>
                  )}
              </div>
            ))}
            <PasteArea
              placeholder={"paste sites, one per line:\nDenver, 39.7, -104.9\nPerth, -31.9, 115.8"}
              value={pasteText}
              onChange={setPasteText}
            />
            {pasteErrors.map((error) => (
              <div className="builder-warning" key={error}>
                {error}
              </div>
            ))}
            <div className="builder-preset-row">
              <Button
                onClick={() => void addPastedSites()}
                disabled={pasteText.trim().length === 0 || minting}
              >
                {minting ? "minting…" : "+ mint pasted sites"}
              </Button>
              <Button active={libraryOpen} onClick={() => setLibraryOpen((open) => !open)}>
                from library…
              </Button>
            </div>
            {libraryOpen && (
              <div className="builder-library-list">
                {siteCatalog.entries
                  .map((entry) => (
                    <button
                      key={entry.ref}
                      className="builder-outline-row"
                      title={`Add ${entry.ref}`}
                      onClick={() => void addFromLibrary(entry.ref)}
                    >
                      <span>{entry.display_name}</span>
                      {entry.summary && (
                        <span className="builder-outline-count">{entry.summary}</span>
                      )}
                    </button>
                  ))}
              </div>
            )}
      </EditorCard>

      <EditorCard
        title="New-site stamp"
        open={openCard === "stamp"}
        onToggle={() => toggle("stamp")}
        summary={
          <>
            {stampLabel} · lan {draft.stamp.lan_base}.x
          </>
        }
      >
            <div className="builder-site-derived">
              applied when minting pasted sites — each site owns its
              configuration afterwards (edit the site, not the stamp)
            </div>
            <BodySelect
              label="on body"
              ariaLabel="Stamp body"
              value={draft.stamp.body}
              onChange={(body) => onUpdate((prev) => ({ ...prev, stamp: { ...prev.stamp, body } }))}
              bodies={bodies}
            />
            <SelectField
              stack
              label="node model"
              ariaLabel="Stamp node model"
              value={draft.stamp.node_ref}
              onChange={(ref) => void setStampModel(ref)}
              options={nodes.entries
                .map((entry) => ({
                  value: entry.ref,
                  label: entry.display_name,
                }))}
            />
            {Object.entries(draft.stamp.installed).map(([mount, count]) => (
              <div key={mount}>
                <NumberField
                  label={mount}
                  value={count}
                  min={1}
                  integer
                  suffix="installed"
                  onChange={(parsed) =>
                    onUpdate((prev) => ({
                      ...prev,
                      stamp: {
                        ...prev.stamp,
                        installed: { ...prev.stamp.installed, [mount]: parsed },
                      },
                    }))
                  }
                />
                <SelectField
                  label={`${mount} ground boresight`}
                  value={draft.stamp.boresights[mount]?.mode ?? ""}
                  onChange={(mode) =>
                    onUpdate((prev) => {
                      const boresights = { ...prev.stamp.boresights };
                      if (mode === authoring.ground_access_boresight.mode) {
                        boresights[mount] = { ...authoring.ground_access_boresight };
                      } else {
                        delete boresights[mount];
                      }
                      return {
                        ...prev,
                        stamp: { ...prev.stamp, boresights },
                      };
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
              label="lan base"
              value={draft.stamp.lan_base}
              suffix=".site.0/24"
              onChange={(lan_base) =>
                onUpdate((prev) => ({ ...prev, stamp: { ...prev.stamp, lan_base: lan_base.trim() } }))
              }
            />
            <Field
              label="loopback base"
              value={draft.stamp.loopback_base}
              suffix=".0.n/32"
              onChange={(loopback_base) =>
                onUpdate((prev) => ({
                  ...prev,
                  stamp: { ...prev.stamp, loopback_base: loopback_base.trim() },
                }))
              }
            />
            <div className="builder-site-derived">
              VS-API allocates each minted site's LAN, terr0, and lo0 from this stamp.
            </div>
      </EditorCard>

      <EditorCard
        title="Scheduling"
        open={openCard === "scheduling"}
        onToggle={() => toggle("scheduling")}
        summary={
          schedulingPresets
            .find((preset) => preset.id === selectedSchedulingPreset)
            ?.label.split(" — ")[0] ?? "Custom (imported)"
        }
      >
            <SelectField
              stack
              label="intent preset — writes the full explicit block (see YAML)"
              ariaLabel="Scheduling preset"
              value={selectedSchedulingPreset ?? ""}
              onChange={(value) => {
                if (value) setSchedulingPreset(value as BuilderVisualSchedulingPreset);
              }}
              options={[
                ...(selectedSchedulingPreset === null
                  ? [{ value: "", label: "Imported block (custom)" }]
                  : []),
                ...schedulingPresets.map((preset) => ({
                  value: preset.id,
                  label: preset.label,
                })),
              ]}
            />
      </EditorCard>

      <EditorCard
        title="Routing intent"
        open={openCard === "routing"}
        onToggle={() => toggle("routing")}
        summary={
          <>
            {draft.originated_ipv4.length > 0
              ? `${draft.originated_ipv4.length} originated`
              : "none originated"}
            {draft.tags.length > 0 && ` · ${draft.tags.join(" ")}`}
          </>
        }
      >
            <Field
              stack
              label="originated IPv4 prefixes"
              placeholder="198.51.100.0/24, 203.0.113.0/24"
              value={draft.originated_ipv4.join(", ")}
              onChange={(value) => onUpdate((prev) => ({ ...prev, originated_ipv4: tokenList(value) }))}
            />
            <Field
              stack
              label="segment tags — link rules select on these"
              placeholder="teleport, edge"
              value={draft.tags.join(", ")}
              onChange={(value) => onUpdate((prev) => ({ ...prev, tags: tokenList(value) }))}
            />
      </EditorCard>

      <SegmentLinksCard
        workspace={workspace}
        segmentId={draft.segment_id}
        onOpenRule={onOpenRule}
        onConnect={onConnect}
      />

      {warnings.map((warning) => (
        <div className="builder-warning" key={warning}>
          {warning}
        </div>
      ))}
      {editorError && <div className="builder-warning">{editorError}</div>}

      <div className="builder-preset-row">
        <Button variant="danger" onClick={onRemove}>
          Discard segment
        </Button>
      </div>
      <div className="builder-site-derived">
        Session save persists authored sites through the backend proposal. Reusable site and
        site-set components are created from the Library.
      </div>
    </div>
  );
}
