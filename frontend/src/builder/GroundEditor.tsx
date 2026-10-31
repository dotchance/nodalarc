// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
/** Ground segment editor. A ground segment places the sites of a site set.
 *  The segment's own fields are its pending edit, edited in the field editor
 *  the caller passes in. The site set and its sites are documents: one the
 *  session proposes opens in the component window, and a catalog document is
 *  customized first. Pasted sites are minted as sites the session proposes,
 *  each installing the chosen node model on the chosen body; a site from the
 *  library joins by reference. Scheduling is an intent preset writing the
 *  full explicit block, with sparse per-site overrides ("= template", only
 *  exceptions stored). With no segment yet, the editor offers only the
 *  gestures that create one. The resolver's verdict arrives through backend
 *  compile.
 */

import { useState, type ReactNode } from "react";
import { Button, IconButton } from "../ui/Button";
import { Icon } from "../ui/icons/Icon";
import { KeyValueRow } from "../ui/KeyValueRow";
import {
  BodySelect,
  EditorCard,
  InlineSelect,
  PasteArea,
  SelectField,
} from "./editorKit";
import { SegmentLinksCard } from "./SegmentLinksCard";
import type { ListedLinkRule } from "./sessionListing";
import {
  isJsonObject,
  proposedBody,
  refStem,
  type SessionSegment,
} from "./sessionSegments";
import { useBuilderCatalog } from "./useBuilderWorld";
import type {
  BuilderProposedCatalogDocument,
  BuilderVisualAuthoringFacts,
  BuilderVisualGroundSiteIntent,
  BuilderVisualSchedulingPreset,
  BuilderVisualSchedulingPresetMetadata,
} from "./generated/builderApi";

/** Bulk site paste: one site per line, ``name, lat, lon[, alt_m]`` — commas
 *  or tabs (spreadsheet columns paste as tabs). Bad lines are reported,
 *  never silently dropped. */
export function parseSiteLines(text: string): {
  rows: BuilderVisualGroundSiteIntent[];
  errors: string[];
} {
  const rows: BuilderVisualGroundSiteIntent[] = [];
  const errors: string[] = [];
  for (const rawLine of text.split("\n")) {
    const line = rawLine.trim();
    if (!line) continue;
    const parts = line
      .split(/[\t,]/)
      .map((part) => part.trim())
      .filter((part) => part.length > 0);
    if (parts.length < 3) {
      errors.push(`"${line}" — expected: name, lat, lon`);
      continue;
    }
    const name = parts[0] ?? "";
    const lat = Number(parts[1]);
    const lon = Number(parts[2]);
    const alt = parts.length > 3 ? Number(parts[3]) : undefined;
    if (
      !Number.isFinite(lat) ||
      !Number.isFinite(lon) ||
      (alt !== undefined && !Number.isFinite(alt))
    ) {
      errors.push(`"${line}" — lat, lon, alt must be numbers`);
      continue;
    }
    if (!name) {
      errors.push(`"${line}" — name is required`);
      continue;
    }
    rows.push({
      name,
      lat_deg: lat,
      lon_deg: lon,
      ...(alt === undefined ? {} : { alt_m: alt }),
    });
  }
  return { rows, errors };
}

/** The ground segment the editor edits, with its field editor. */
interface GroundSegmentParts {
  segment: SessionSegment;
  /** The segment's field editor over its pending edit. */
  fields: ReactNode;
  /** Open the component chain under the segment's site set for customizing. */
  onCustomize: () => void;
  schedulingPresets: readonly BuilderVisualSchedulingPresetMetadata[];
  selectedSchedulingPreset: BuilderVisualSchedulingPreset | null;
  siteSchedulingPreset: (siteId: string) => BuilderVisualSchedulingPreset | null;
  onSchedulingPreset: (
    preset: BuilderVisualSchedulingPreset | null,
    siteId?: string,
  ) => Promise<void>;
  /** Remove the segment from the session; null while a change is being applied. */
  onRemove: (() => void) | null;
  placed: ReadonlyArray<SessionSegment>;
  /** Every link rule the session declares, as the backend lists them with
   *  the pending edits laid over them. */
  linkRules: ReadonlyArray<ListedLinkRule>;
  onOpenRule: (ruleId: string) => void;
  onConnect: (targetSegmentId: string) => void;
}

interface GroundEditorProps {
  /** Null before the segment exists: its first sites create it. */
  ground: GroundSegmentParts | null;
  /** The documents the session proposes. */
  proposals: ReadonlyArray<BuilderProposedCatalogDocument>;
  onMintSites: (
    sites: BuilderVisualGroundSiteIntent[],
    nodeRef: string,
    bodyRef: string,
  ) => Promise<void>;
  onAddSiteReference: (ref: string) => Promise<void>;
  /** Open the document the session proposes at `ref` in the component window. */
  onEdit: (ref: string) => void;
  authoring: BuilderVisualAuthoringFacts;
}

export function GroundEditor({
  ground,
  proposals,
  onMintSites,
  onAddSiteReference,
  onEdit,
  authoring,
}: GroundEditorProps) {
  const nodes = useBuilderCatalog("nodes");
  const siteCatalog = useBuilderCatalog("sites");
  const bodies = useBuilderCatalog("bodies");
  const [pasteText, setPasteText] = useState("");
  const [pasteErrors, setPasteErrors] = useState<string[]>([]);
  const [libraryOpen, setLibraryOpen] = useState(false);
  const [editorError, setEditorError] = useState<string | null>(null);
  const [minting, setMinting] = useState(false);
  const [nodeRef, setNodeRef] = useState<string | null>(null);
  const [bodyRef, setBodyRef] = useState<string | null>(authoring.default_body_ref);
  const report = (cause: unknown) =>
    setEditorError(cause instanceof Error ? cause.message : String(cause));

  const placement = ground?.segment.segment.placement;
  const siteSetRef =
    isJsonObject(placement) && typeof placement.from_site_set === "string"
      ? placement.from_site_set
      : null;
  const siteSet = proposedBody(proposals, siteSetRef, "site_set");
  const siteRefs = Array.isArray(siteSet?.sites)
    ? siteSet.sites.filter((site): site is string => typeof site === "string")
    : [];
  const overrides = Array.isArray(ground?.segment.segment.overrides)
    ? ground.segment.segment.overrides
    : [];
  const overridden = (siteId: string) =>
    overrides.some(
      (override) =>
        isJsonObject(override) && isJsonObject(override.match) && override.match.site === siteId,
    );
  // Sites are added where the session proposes the site set, or by creating
  // the segment.
  const canAddSites = ground === null || siteSet !== null;

  const setSchedulingPreset = (preset: BuilderVisualSchedulingPreset | null, siteId?: string) => {
    if (!ground) return;
    setEditorError(null);
    void ground.onSchedulingPreset(preset, siteId).catch(report);
  };

  const addPastedSites = async () => {
    const { rows, errors } = parseSiteLines(pasteText);
    setPasteErrors(errors);
    if (rows.length === 0) return;
    if (nodeRef === null || bodyRef === null) {
      setEditorError("choose the node model and the body the pasted sites install");
      return;
    }
    setMinting(true);
    setEditorError(null);
    try {
      await onMintSites(rows, nodeRef, bodyRef);
      setPasteText("");
    } catch (cause) {
      report(cause);
    } finally {
      setMinting(false);
    }
  };

  const addFromLibrary = async (ref: string) => {
    setEditorError(null);
    try {
      await onAddSiteReference(ref);
    } catch (cause) {
      report(cause);
    }
  };

  const siteLabel = (ref: string) => {
    const site = proposedBody(proposals, ref, "site");
    return typeof site?.display_name === "string" ? site.display_name : refStem(ref);
  };

  const addSites = (
    <>
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
      <SelectField
        stack
        label="node model the pasted sites install"
        ariaLabel="Site node model"
        value={nodeRef}
        onChange={(ref) => setNodeRef(ref || null)}
        options={nodes.entries.map((entry) => ({ value: entry.ref, label: entry.display_name }))}
      />
      <BodySelect
        label="on body"
        ariaLabel="Site body"
        value={bodyRef}
        onChange={(ref) => setBodyRef(ref || null)}
        bodies={bodies}
      />
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
          {siteCatalog.entries.map((entry) => (
            <button
              key={entry.ref}
              className="builder-outline-row"
              title={`Add ${entry.ref}`}
              onClick={() => void addFromLibrary(entry.ref)}
            >
              <span>{entry.display_name}</span>
              {entry.summary && <span className="builder-outline-count">{entry.summary}</span>}
            </button>
          ))}
        </div>
      )}
      <div className="builder-site-derived">
        a minted site installs the node model with the terminals it carries; edit the site
        afterwards in its own window
      </div>
    </>
  );

  if (ground === null) {
    return (
      <div className="builder-inspector-stack" data-testid="builder-ground-editor">
        <div className="builder-site-derived">
          The ground segment is created with its first sites.
        </div>
        {addSites}
        {editorError && <div className="builder-warning">{editorError}</div>}
      </div>
    );
  }

  return (
    <div className="builder-inspector-stack" data-testid="builder-ground-editor">
      <EditorCard title="Segment" open summary={ground.segment.segment_id}>
        {ground.fields}
      </EditorCard>

      <EditorCard
        title="Sites"
        open
        summary={
          siteSet === null
            ? (siteSetRef === null ? "no site set" : refStem(siteSetRef))
            : siteRefs.length === 1
              ? "1 site"
              : `${siteRefs.length} sites`
        }
      >
        <KeyValueRow label="site set">{siteSetRef ?? "none"}</KeyValueRow>
        {siteSet === null ? (
          <>
            <div className="builder-site-derived">
              a library component — customize it to edit it in this session
            </div>
            <Button disabled={siteSetRef === null} onClick={ground.onCustomize}>
              Customize…
            </Button>
          </>
        ) : (
          <>
            {siteRefs.map((ref) => {
              const siteId = refStem(ref);
              const label = siteLabel(ref);
              const preset = ground.siteSchedulingPreset(siteId);
              const proposed = proposedBody(proposals, ref, "site") !== null;
              return (
                <div className="builder-site-row" key={ref}>
                  <div className="builder-site-head">
                    <span className="builder-site-name">
                      <Icon name="locate-fixed" size={12} />
                      {label}
                    </span>
                    <InlineSelect
                      className="builder-ground-preset"
                      ariaLabel={`${label} scheduling`}
                      title="Per-site scheduling — only exceptions are stored"
                      value={overridden(siteId) ? (preset ?? "__custom__") : ""}
                      onChange={(value) => {
                        if (value === "__custom__") return;
                        setSchedulingPreset(
                          value === "" ? null : (value as BuilderVisualSchedulingPreset),
                          siteId,
                        );
                      }}
                      options={[
                        { value: "", label: "= template" },
                        ...(overridden(siteId) && preset === null
                          ? [{ value: "__custom__", label: "Imported block (custom)" }]
                          : []),
                        ...ground.schedulingPresets.map((choice) => ({
                          value: choice.id,
                          label: choice.label,
                        })),
                      ]}
                    />
                    <IconButton
                      icon="pencil"
                      size={12}
                      label={proposed ? `Edit ${label}` : `Customize ${label}`}
                      onClick={() => (proposed ? onEdit(ref) : ground.onCustomize())}
                    />
                  </div>
                  <span className="builder-site-derived">
                    {proposed ? "proposed by this session" : ref}
                  </span>
                </div>
              );
            })}
            <div className="builder-preset-row">
              <Button onClick={() => siteSetRef && onEdit(siteSetRef)}>Edit site set</Button>
            </div>
          </>
        )}
      </EditorCard>

      {canAddSites && (
        <EditorCard title="Add sites" open summary="paste or pick">
          {addSites}
        </EditorCard>
      )}

      <EditorCard
        title="Scheduling"
        open
        summary={
          ground.schedulingPresets
            .find((preset) => preset.id === ground.selectedSchedulingPreset)
            ?.label.split(" — ")[0] ?? "Custom (imported)"
        }
      >
        <SelectField
          stack
          label="intent preset — writes the full explicit block (see YAML)"
          ariaLabel="Scheduling preset"
          value={ground.selectedSchedulingPreset ?? ""}
          onChange={(value) => {
            if (value) setSchedulingPreset(value as BuilderVisualSchedulingPreset);
          }}
          options={[
            ...(ground.selectedSchedulingPreset === null
              ? [{ value: "", label: "Imported block (custom)" }]
              : []),
            ...ground.schedulingPresets.map((preset) => ({
              value: preset.id,
              label: preset.label,
            })),
          ]}
        />
      </EditorCard>

      <SegmentLinksCard
        placed={ground.placed}
        rules={ground.linkRules}
        segmentId={ground.segment.segment_id}
        onOpenRule={ground.onOpenRule}
        onConnect={ground.onConnect}
      />

      {editorError && <div className="builder-warning">{editorError}</div>}

      <Button
        variant="danger"
        onClick={ground.onRemove ?? undefined}
        disabled={ground.onRemove === null}
      >
        Remove segment
      </Button>
    </div>
  );
}
