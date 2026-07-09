// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
/** Ground segment editor — a segment is a combination of defined sites.
 *
 *  A site is a first-class primitive (nodes, terminals, networks at a
 *  location); members here are either references to defined sites — full
 *  fidelity, their nodes travel with them — or authored site drafts edited
 *  in place with the SiteEditor. Bulk paste mints full sites using the
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
import {
  LIBRARY_SAVE_COPY,
  readCatalogObject,
  useBuilderCatalog,
  useLibrarySave,
} from "./useBuilderWorld";
import {
  SCHEDULING_PRESETS,
  draftGroundMember,
  draftSiteFromDocument,
  groundWarnings,
  mintSiteMembers,
  nextMintIndex,
  parseSiteLines,
  refGroundMember,
  siteSetWrapperFromDraft,
  stampLanPrefix,
  stampLoopbackAddress,
  type DraftGroundSet,
  type DraftGroundSite,
  type SchedulingPresetKey,
  type Workspace,
} from "./workspace";

interface GroundEditorProps {
  draft: DraftGroundSet;
  /** Functional-only (N56): the caller reads the LATEST draft, never a stale
   *  render-closure, so a concurrent edit during an in-flight fetch (forkMember,
   *  setStampModel, the member save-flip) survives. */
  onUpdate: (update: (prev: DraftGroundSet) => DraftGroundSet) => void;
  onRemove: () => void;
  /** IG-2: focus the name when a create gesture opened this editor. */
  autoFocusName?: boolean;
  /** Connect gesture context (IG-7: "+ link to…" on the segment). */
  workspace: Workspace;
  onOpenRule: (ruleId: string) => void;
  onConnect: (targetSegmentId: string) => void;
  /** D7: a save of the whole set to the library, reported up so the bound
   *  window can converge the set back to this ref on a user close. */
  onSaved?: (ref: string, savedObject: Record<string, unknown>) => void;
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
  onSaved,
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
  const librarySave = useLibrarySave("site-sets");
  const warnings = groundWarnings(draft);

  const updateMember = (memberId: string, patch: Partial<DraftGroundSite>) => {
    onUpdate((prev) => ({
      ...prev,
      members: prev.members.map((member) =>
        member.member_id === memberId ? { ...member, ...patch } : member,
      ),
    }));
  };

  const addPastedSites = () => {
    const { rows, errors } = parseSiteLines(pasteText);
    setPasteErrors(errors);
    if (rows.length > 0) {
      onUpdate((prev) => ({ ...prev, members: [...prev.members, ...mintSiteMembers(prev, rows)] }));
      setPasteText("");
    }
  };

  // Place a defined site by reference — full fidelity, its nodes travel.
  const addFromLibrary = (ref: string, siteId: string, label: string, summary: string | null) => {
    setEditorError(null);
    if (draft.members.some((member) => member.site_id === siteId)) {
      setEditorError(`${siteId} is already placed — a site is a place and exists once`);
      return;
    }
    onUpdate((prev) => ({
      ...prev,
      members: [...prev.members, refGroundMember(ref, siteId, label, summary)],
    }));
  };

  // Customize a referenced site: fork the document into an authored member.
  const forkMember = async (member: DraftGroundSite) => {
    setEditorError(null);
    try {
      if (!member.ref) return;
      const { document } = await readCatalogObject(member.ref);
      const forked = draftGroundMember(draftSiteFromDocument(document));
      onUpdate((prev) => ({
        ...prev,
        // Carry the override from the LATEST matched member, not the click-time
        // closure — a concurrent per-site scheduling edit during the fork fetch
        // must survive (N56).
        members: prev.members.map((m) =>
          m.member_id === member.member_id
            ? { ...forked, scheduling_override: m.scheduling_override }
            : m,
        ),
      }));
      setEditingMember(forked.member_id);
    } catch (e) {
      setEditorError(e instanceof Error ? e.message : String(e));
    }
  };

  // Switching the stamp model re-seeds its mounts — affects future mints only.
  const setStampModel = async (ref: string) => {
    setEditorError(null);
    try {
      const { document } = await readCatalogObject(ref);
      const node = (document as { node?: Record<string, unknown> }).node;
      const mounts = ((node?.terminals as Record<string, unknown>[] | undefined) ?? []).map(
        (mount) => [String(mount.id), Number(mount.count ?? 1)] as const,
      );
      onUpdate((prev) => ({
        ...prev,
        stamp: { ...prev.stamp, node_ref: ref, installed: Object.fromEntries(mounts) },
      }));
    } catch (e) {
      setEditorError(e instanceof Error ? e.message : String(e));
    }
  };

  const saveToLibrary = () => {
    void librarySave.save(siteSetWrapperFromDraft(draft), onSaved);
  };

  const stampLabel =
    draft.stamp.node_ref.split("/").pop()?.replace(/\.yaml$/, "") || "pick a model";

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
                        site={member.site}
                        onUpdate={(update) =>
                          // Thread SiteEditor's functional update through the
                          // ground's own — find the member in the LATEST members
                          // and update its LATEST site, so a concurrent edit
                          // (this member or another) during a site-level fetch
                          // survives (N56).
                          onUpdate((prev) => ({
                            ...prev,
                            members: prev.members.map((m) => {
                              if (m.member_id !== member.member_id || !m.site) return m;
                              const site = update(m.site);
                              return { ...m, site, site_id: site.site_id, label: site.display_name };
                            }),
                          }))
                        }
                        onSaved={(ref) => {
                          // D7 member-level: this window is bound to the segment,
                          // not the member, so the authored member converges
                          // immediately — flip it to a ref in place, keeping its
                          // member_id and any scheduling_override (updateMember
                          // patches, never mints).
                          updateMember(member.member_id, { kind: "ref", ref, site: null });
                          setEditingMember(null);
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
                  onUpdate((prev) => ({
                    ...prev,
                    stamp: {
                      ...prev.stamp,
                      installed: { ...prev.stamp.installed, [mount]: parsed },
                    },
                  }))
                }
              />
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
              {/* The preview must read the SAME next index the mint uses
                  (nextMintIndex), never a literal 0 — otherwise it lies once the
                  segment already holds stamp-shaped members (N29). */}
              next minted site: lan {stampLanPrefix(draft.stamp, nextMintIndex(draft))}, lo0{" "}
              {stampLoopbackAddress(draft.stamp, nextMintIndex(draft))} …
            </div>
      </EditorCard>

      <EditorCard
        title="Scheduling"
        open={openCard === "scheduling"}
        onToggle={() => toggle("scheduling")}
        summary={SCHEDULING_PRESETS[draft.scheduling_preset].label.split(" — ")[0]}
      >
            <SelectField
              stack
              label="intent preset — writes the full explicit block (see YAML)"
              ariaLabel="Scheduling preset"
              value={draft.scheduling_preset}
              onChange={(value) =>
                onUpdate((prev) => ({ ...prev, scheduling_preset: value as SchedulingPresetKey }))
              }
              options={Object.entries(SCHEDULING_PRESETS).map(([key, preset]) => ({
                value: key,
                label: preset.label,
              }))}
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
        <Button
          onClick={saveToLibrary}
          disabled={draft.members.length === 0 || librarySave.saving}
        >
          {librarySave.label("Save to library")}
        </Button>
        <Button variant="danger" onClick={onRemove}>
          Discard segment
        </Button>
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
