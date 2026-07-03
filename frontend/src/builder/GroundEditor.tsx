// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
/** Ground segment editor — a segment is a COMBINATION of defined sites.
 *
 *  A site is a first-class primitive (nodes, terminals, networks at a
 *  location); members here are either references to defined sites — full
 *  fidelity, their nodes travel with them — or authored site drafts edited
 *  in place with the SiteEditor. Bulk paste MINTS full sites using the
 *  segment's stamp (node model + addressing bases, applied at creation;
 *  every minted site owns its configuration afterwards). Scheduling is an
 *  intent preset writing the full explicit block, with sparse per-site
 *  overrides ("= template", only exceptions stored). Findings warn, never
 *  block; the resolver's verdict arrives verbatim via resolve-check.
 */

import { useState } from "react";
import { Button, IconButton } from "../ui/Button";
import { Icon } from "../ui/icons/Icon";
import {
  EditorName,
  Field,
  InlineSelect,
  NumberField,
  PasteArea,
  SelectField,
} from "./editorKit";
import { SegmentLinksCard } from "./SegmentLinksCard";
import { SiteEditor } from "./SiteEditor";
import { readCatalogObject, saveUserObject, useBuilderCatalog } from "./useBuilderWorld";
import {
  SCHEDULING_PRESETS,
  draftGroundMember,
  draftSiteFromDocument,
  groundWarnings,
  identifier,
  mintSiteMembers,
  parseSiteLines,
  refGroundMember,
  siteSetObjectFromDraft,
  stampLanPrefix,
  stampLoopbackAddress,
  type DraftGroundSet,
  type DraftGroundSite,
  type DraftSiteObject,
  type SchedulingPresetKey,
  type Workspace,
} from "./workspace";

interface GroundEditorProps {
  draft: DraftGroundSet;
  onUpdate: (patch: Partial<DraftGroundSet>) => void;
  onRemove: () => void;
  /** IG-2: focus the name when a create gesture opened this editor. */
  autoFocusName?: boolean;
  /** Connect gesture context (IG-7: "+ link to…" on the segment). */
  workspace: Workspace;
  onOpenRule: (ruleId: string) => void;
  onConnect: (targetSegmentId: string) => void;
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
  onRemove,
  autoFocusName = false,
  workspace,
  onOpenRule,
  onConnect,
}: GroundEditorProps) {
  const [openCard, setOpenCard] = useState<string | null>("sites");
  const toggle = (id: string) => setOpenCard((prev) => (prev === id ? null : id));
  const nodes = useBuilderCatalog("nodes");
  const siteCatalog = useBuilderCatalog("sites");
  const siteSets = useBuilderCatalog("site-sets");
  const [pasteText, setPasteText] = useState("");
  const [pasteErrors, setPasteErrors] = useState<string[]>([]);
  const [libraryOpen, setLibraryOpen] = useState(false);
  const [editingMember, setEditingMember] = useState<string | null>(null);
  const [editorError, setEditorError] = useState<string | null>(null);
  const [librarySave, setLibrarySave] = useState<
    | { kind: "idle" }
    | { kind: "saving" }
    | { kind: "conflict" }
    | { kind: "saved"; ref: string }
    | { kind: "failed"; message: string }
  >({ kind: "idle" });
  const warnings = groundWarnings(draft);

  const updateMember = (memberId: string, patch: Partial<DraftGroundSite>) => {
    onUpdate({
      members: draft.members.map((member) =>
        member.member_id === memberId ? { ...member, ...patch } : member,
      ),
    });
  };

  const addPastedSites = () => {
    const { rows, errors } = parseSiteLines(pasteText);
    setPasteErrors(errors);
    if (rows.length > 0) {
      onUpdate({ members: [...draft.members, ...mintSiteMembers(draft, rows)] });
      setPasteText("");
    }
  };

  // Place a DEFINED site by reference — full fidelity, its nodes travel.
  const addFromLibrary = (ref: string, siteId: string, label: string, summary: string | null) => {
    setEditorError(null);
    if (draft.members.some((member) => member.site_id === siteId)) {
      setEditorError(`${siteId} is already placed — a site is a place and exists once`);
      return;
    }
    onUpdate({ members: [...draft.members, refGroundMember(ref, siteId, label, summary)] });
  };

  // Customize a referenced site: fork the document into an authored member.
  const forkMember = async (member: DraftGroundSite) => {
    setEditorError(null);
    try {
      if (!member.ref) return;
      const { document } = await readCatalogObject(member.ref);
      const forked = draftGroundMember(draftSiteFromDocument(document));
      forked.scheduling_override = member.scheduling_override;
      onUpdate({
        members: draft.members.map((m) => (m.member_id === member.member_id ? forked : m)),
      });
      setEditingMember(forked.member_id);
    } catch (e) {
      setEditorError(e instanceof Error ? e.message : String(e));
    }
  };

  // Switching the stamp model re-seeds its mounts — affects FUTURE mints only.
  const setStampModel = async (ref: string) => {
    setEditorError(null);
    try {
      const { document } = await readCatalogObject(ref);
      const node = (document as { node?: Record<string, unknown> }).node;
      const mounts = ((node?.terminals as Record<string, unknown>[] | undefined) ?? []).map(
        (mount) => [String(mount.id), Number(mount.count ?? 1)] as const,
      );
      onUpdate({
        stamp: { ...draft.stamp, node_ref: ref, installed: Object.fromEntries(mounts) },
      });
    } catch (e) {
      setEditorError(e instanceof Error ? e.message : String(e));
    }
  };

  const saveToLibrary = async () => {
    setLibrarySave({ kind: "saving" });
    try {
      const id = identifier(draft.display_name) || identifier(draft.segment_id);
      const entry = await saveUserObject(
        "site-sets",
        { site_set: siteSetObjectFromDraft(draft, id) },
        { overwrite: librarySave.kind === "conflict" },
      );
      setLibrarySave({ kind: "saved", ref: entry.ref });
      void siteSets.refresh();
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

  const stampLabel =
    draft.stamp.node_ref.split("/").pop()?.replace(/\.yaml$/, "") || "pick a model";

  return (
    <div className="builder-inspector-stack" data-testid="builder-ground-editor">
      <EditorName
        value={draft.display_name}
        onChange={(display_name) => onUpdate({ display_name })}
        autoFocus={autoFocusName}
      />

      <div className={`builder-card${openCard === "sites" ? " builder-card--open" : ""}`}>
        <button className="builder-card-head" onClick={() => toggle("sites")}>
          <span className="builder-card-title">Sites</span>
          <span className="builder-card-summary">
            {draft.members.length === 1 ? "1 site" : `${draft.members.length} sites`}
          </span>
        </button>
        {openCard === "sites" && (
          <div className="builder-card-body">
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
                      value={member.scheduling_override ?? ""}
                      onChange={(value) =>
                        updateMember(member.member_id, {
                          scheduling_override: (value || null) as SchedulingPresetKey | null,
                        })
                      }
                      options={[
                        { value: "", label: "= template" },
                        ...Object.entries(SCHEDULING_PRESETS).map(([key, preset]) => ({
                          value: key,
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
                          void forkMember(member);
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
                        onUpdate({
                          members: draft.members.filter(
                            (m) => m.member_id !== member.member_id,
                          ),
                        })
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
                        site={member.site}
                        onUpdate={(patch) => {
                          const site = { ...(member.site as DraftSiteObject), ...patch };
                          updateMember(member.member_id, {
                            site,
                            site_id: site.site_id,
                            label: site.display_name,
                          });
                        }}
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
              <Button onClick={addPastedSites} disabled={pasteText.trim().length === 0}>
                + mint pasted sites
              </Button>
              <Button active={libraryOpen} onClick={() => setLibraryOpen((open) => !open)}>
                from library…
              </Button>
            </div>
            {libraryOpen && (
              <div className="builder-library-list">
                {siteCatalog.entries
                  .filter((entry) => !entry.error && entry.id)
                  .map((entry) => (
                    <button
                      key={entry.ref}
                      className="builder-outline-row"
                      title={`Add ${entry.ref}`}
                      onClick={() =>
                        addFromLibrary(
                          entry.ref,
                          entry.id as string,
                          entry.display_name ?? (entry.id as string),
                          entry.summary,
                        )
                      }
                    >
                      <span>{entry.display_name ?? entry.id}</span>
                      {entry.summary && (
                        <span className="builder-outline-count">{entry.summary}</span>
                      )}
                    </button>
                  ))}
              </div>
            )}
          </div>
        )}
      </div>

      <div className={`builder-card${openCard === "stamp" ? " builder-card--open" : ""}`}>
        <button className="builder-card-head" onClick={() => toggle("stamp")}>
          <span className="builder-card-title">New-site stamp</span>
          <span className="builder-card-summary">
            {stampLabel} · lan {draft.stamp.lan_base}.x
          </span>
        </button>
        {openCard === "stamp" && (
          <div className="builder-card-body">
            <div className="builder-site-derived">
              applied when minting pasted sites — each site owns its
              configuration afterwards (edit the site, not the stamp)
            </div>
            <SelectField
              stack
              label="node model"
              ariaLabel="Stamp node model"
              value={draft.stamp.node_ref}
              onChange={(ref) => void setStampModel(ref)}
              options={nodes.entries
                .filter((entry) => !entry.error)
                .map((entry) => ({
                  value: entry.ref,
                  label: entry.display_name ?? entry.id ?? entry.ref,
                }))}
            />
            {Object.entries(draft.stamp.installed).map(([mount, count]) => (
              <NumberField
                key={mount}
                label={mount}
                value={count}
                min={1}
                integer
                suffix="installed"
                onChange={(parsed) =>
                  onUpdate({
                    stamp: {
                      ...draft.stamp,
                      installed: { ...draft.stamp.installed, [mount]: parsed },
                    },
                  })
                }
              />
            ))}
            <Field
              label="lan base"
              value={draft.stamp.lan_base}
              suffix=".site.0/24"
              onChange={(lan_base) =>
                onUpdate({ stamp: { ...draft.stamp, lan_base: lan_base.trim() } })
              }
            />
            <Field
              label="loopback base"
              value={draft.stamp.loopback_base}
              suffix=".0.n/32"
              onChange={(loopback_base) =>
                onUpdate({ stamp: { ...draft.stamp, loopback_base: loopback_base.trim() } })
              }
            />
            <div className="builder-site-derived">
              next minted site: lan {stampLanPrefix(draft.stamp, 0)}, lo0{" "}
              {stampLoopbackAddress(draft.stamp, 0)} …
            </div>
          </div>
        )}
      </div>

      <div className={`builder-card${openCard === "scheduling" ? " builder-card--open" : ""}`}>
        <button className="builder-card-head" onClick={() => toggle("scheduling")}>
          <span className="builder-card-title">Scheduling</span>
          <span className="builder-card-summary">
            {SCHEDULING_PRESETS[draft.scheduling_preset].label.split(" — ")[0]}
          </span>
        </button>
        {openCard === "scheduling" && (
          <div className="builder-card-body">
            <SelectField
              stack
              label="intent preset — writes the full explicit block (see YAML)"
              ariaLabel="Scheduling preset"
              value={draft.scheduling_preset}
              onChange={(value) =>
                onUpdate({ scheduling_preset: value as SchedulingPresetKey })
              }
              options={Object.entries(SCHEDULING_PRESETS).map(([key, preset]) => ({
                value: key,
                label: preset.label,
              }))}
            />
          </div>
        )}
      </div>

      <div className={`builder-card${openCard === "routing" ? " builder-card--open" : ""}`}>
        <button className="builder-card-head" onClick={() => toggle("routing")}>
          <span className="builder-card-title">Routing intent</span>
          <span className="builder-card-summary">
            {draft.originated_ipv4.length > 0
              ? `${draft.originated_ipv4.length} originated`
              : "none originated"}
            {draft.tags.length > 0 && ` · ${draft.tags.join(" ")}`}
          </span>
        </button>
        {openCard === "routing" && (
          <div className="builder-card-body">
            <Field
              stack
              label="originated IPv4 prefixes"
              placeholder="198.51.100.0/24, 203.0.113.0/24"
              value={draft.originated_ipv4.join(", ")}
              onChange={(value) => onUpdate({ originated_ipv4: tokenList(value) })}
            />
            <Field
              stack
              label="segment tags — link rules select on these"
              placeholder="teleport, edge"
              value={draft.tags.join(", ")}
              onChange={(value) => onUpdate({ tags: tokenList(value) })}
            />
          </div>
        )}
      </div>

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
        <Button
          onClick={() => void saveToLibrary()}
          disabled={draft.members.length === 0 || librarySave.kind === "saving"}
        >
          {librarySave.kind === "conflict" ? "Overwrite in library?" : "Save to library"}
        </Button>
        <Button variant="danger" onClick={onRemove}>
          Discard segment
        </Button>
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
