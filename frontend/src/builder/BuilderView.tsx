// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
/** Session builder — four-zone authoring shell.
 *
 *  Zones: outline tree (left, grouped by
 *  body) | world canvas (center) | docked card inspector (right) | status
 *  bar (bottom, the single home for counts/validation/gates). The builder
 *  renders the resolver's expansion of a session — never a builder-local
 *  view of what a session means.
 *
 *  Authoring: client-side drafts + library refs, serialized through the one
 *  serializer and resolve-checked server-side on every edit; the rendered
 *  world is always the resolver's expansion of the current draft.
 */

import { Fragment, useEffect, useMemo, useRef, useState, type MutableRefObject } from "react";
import { Scene } from "../globe/r3f/Scene";
import { VisualizationErrorBoundary } from "../globe/VisualizationErrorBoundary";
import { buildRegimeIndex } from "../taxonomy/regime";
import { Button, IconButton } from "../ui/Button";
import { FloatingWindow } from "../ui/FloatingWindow";
import type { GlobeActions } from "../globe/actions";
import type {
  ColorMode,
  GlobeMode,
  ReferenceFrame,
  Selection,
  StateSnapshot,
} from "../types";
import { Icon } from "../ui/icons/Icon";
import { BuildGuide } from "./BuildGuide";
import { BuilderInspector } from "./BuilderInspector";
import { builderSnapshotFromWorld, distinctGroundStationSites } from "./builderSnapshot";
import { CandidateLines } from "./CandidateLines";
import { computeCandidates } from "./candidates";
import { EditorApplyRow, Field, InlineSelect } from "./editorKit";
import {
  accessBeamElevationDeg,
  capabilitiesBySegment,
  connectSegments,
  rederiveRule,
} from "./linkPhysics";
import { CatalogObjectView } from "./CatalogObjectView";
import { ConstellationEditor } from "./ConstellationEditor";
import { GroundEditor } from "./GroundEditor";
import { LibraryPanel } from "./LibraryPanel";
import { LinkRuleEditor } from "./LinkRuleEditor";
import { NodeEditor } from "./NodeEditor";
import { BoundaryEditor, RoutingDomainEditor } from "./RoutingEditor";
import { SessionEditor } from "./SessionEditor";
import { SiteEditor } from "./SiteEditor";
import { TerminalEditor } from "./TerminalEditor";
import {
  canDeploy,
  claimLibraryReveal,
  claimOutlineReveal,
  readCatalogObject,
  requestOutlineReveal,
  useLibraryReveal,
  useLibraryRevision,
  useLibrarySave,
  useOutlineReveal,
  useBuilderCatalog,
  useBuilderWorld,
} from "./useBuilderWorld";
import { downloadBlob } from "../ui/downloadBlob";
import { workspaceForSave, useWorkspace } from "./useWorkspace";
import {
  useEditorWindows,
  targetKey,
  type EditorTarget,
  type SessionBuffer,
} from "./useEditorWindows";
import {
  defaultDraftNode,
  defaultDraftTerminal,
  newDraftConstellation,
  draftConstellationFromDocuments,
  draftGroundSetFromDocuments,
  draftNodeFromDocument,
  draftSiteFromDocument,
  draftTerminalFromDocument,
  completenessFindings,
  defaultBoundary,
  defaultRoutingDomain,
  emittedDomainId,
  emittedRuleId,
  identifier,
  linkWarnings,
  placedSegments,
  routingWarnings,
  newDraftGroundSet,
  newDraftSiteObject,
  nodeObjectFromDraft,
  refGroundMember,
  SCHEDULING_PRESETS,
  toSessionDocument,
  workspaceFromSessionDocument,
  type DraftBoundary,
  type DraftConstellation,
  type DraftGroundSet,
  type DraftLinkRule,
  type DraftNode,
  type DraftRoutingDomain,
  type DraftSiteObject,
  type DraftTerminal,
  type SchedulingPresetKey,
} from "./workspace";
import type {
  BuilderCatalogEntry,
  BuilderSessionListEntry,
  BuilderWorld,
} from "./builderTypes";

// B3: the running-session auto-import is entry-scoped, not mount-scoped. The
// tried-file marker lives at MODULE scope (like the retired-reveal-nonce
// registry) so it survives a Live<->Builder toggle — which now keeps
// BuilderView mounted — and the import fires once per app session, on entry.
let _importTriedFile: string | null = null;

interface BuilderViewProps {
  /** True only while the builder is the shown view. The builder stays mounted
   *  when hidden (B3) so drafts, windows, and buffers survive a Live<->Builder
   *  toggle; `active` gates every operator surface that ACTS (the Scene
   *  subtree per the singleton law, global key listeners, the reveal-open and
   *  auto-import effects) so a hidden builder never mounts a second Scene or
   *  intercepts live-mode input. Passive state — autosave, backup, workspace
   *  mutations — keeps running while hidden. */
  active: boolean;
  /** Shared display state — the toolbar operates on the builder scene exactly
   *  as it does on the live scene (same Scene component, same toggles). */
  colorMode: ColorMode;
  globeMode: GlobeMode;
  referenceFrame: ReferenceFrame;
  showSatPaths: boolean;
  showIslLinks: boolean;
  showGroundLinks: boolean;
  showGroundTracks: boolean;
  showTrails: boolean;
  /** The app-level camera/screenshot handle; only one Scene is mounted at a
   *  time, so the builder scene owns it while the builder view is active. */
  actionsRef: MutableRefObject<GlobeActions | null>;
}

interface SegmentSummary {
  segment_id: string;
  body: string | null;
  satellites: number;
  grounds: number;
  relays: number;
  /** The user's name for the segment — the tree row's label. */
  display_name: string;
  /** First member (sorted by node_id) — the tree row's fly-to target. */
  first_node_id: string;
}

function summarizeSegments(world: BuilderWorld): SegmentSummary[] {
  const names = new Map(world.segments.map((s) => [s.segment_id, s.display_name]));
  const bySegment = new Map<string, SegmentSummary>();
  for (const node of world.nodes) {
    let summary = bySegment.get(node.segment_id);
    if (!summary) {
      const body =
        world.ephemeris.nodes[node.node_id]?.reference_body ??
        node.surface_position?.body ??
        null;
      summary = {
        segment_id: node.segment_id,
        display_name: names.get(node.segment_id) ?? node.segment_id,
        body,
        satellites: 0,
        grounds: 0,
        relays: 0,
        first_node_id: node.node_id,
      };
      bySegment.set(node.segment_id, summary);
    }
    if (node.node_id < summary.first_node_id) summary.first_node_id = node.node_id;
    if (node.kind === "satellite") summary.satellites += 1;
    else if (node.kind === "ground_station") summary.grounds += 1;
    else summary.relays += 1;
  }
  return [...bySegment.values()].sort((a, b) => a.segment_id.localeCompare(b.segment_id));
}

/** The world tree groups by body (the world-centered thesis): body headers,
 *  segment rows beneath. Segments whose body is unknown sort last. */
/** Display counts read as words: "1 segment", "8 segments" — never "1 segments". */
function count(n: number, word: string): string {
  return `${n} ${word}${n === 1 ? "" : "s"}`;
}

/** One-line form of a refusal for the compact surfaces (canvas note,
 *  status bar) — the owning window's wall carries the full text. */
function truncateError(text: string, max = 180): string {
  return text.length > max ? `${text.slice(0, max)}\u2026` : text;
}

function groupByBody(segments: SegmentSummary[]): [string, SegmentSummary[]][] {
  const byBody = new Map<string, SegmentSummary[]>();
  for (const seg of segments) {
    const key = seg.body ?? "(unplaced)";
    const group = byBody.get(key);
    if (group) group.push(seg);
    else byBody.set(key, [seg]);
  }
  return [...byBody.entries()].sort(([a], [b]) =>
    a === "earth" ? -1 : b === "earth" ? 1 : a.localeCompare(b),
  );
}

type SaveState =
  | { kind: "idle" }
  | { kind: "saving" }
  | { kind: "saved"; name: string; file: string; artifact_sha256: string }
  | { kind: "deploying"; name: string; file: string; artifact_sha256: string }
  | { kind: "deployed"; name: string; file: string; artifact_sha256: string }
  | { kind: "failed"; message: string };

/** Save is a small dialog, not a silent write. The name is buffered here and
 *  committed once on Save (IG-14 — the Session window stays the only live
 *  name editor); with unapplied windows the primary action applies them
 *  first, and saving around them is the quieter, explicit choice. */
function SaveSessionDialog({
  workspaceName,
  canSave,
  blockedReason,
  saveState,
  dirtyWindows,
  staleList,
  onSave,
  onClose,
}: {
  workspaceName: string;
  canSave: boolean;
  blockedReason: string | null;
  saveState: SaveState;
  dirtyWindows: number;
  staleList: { key: string; title: string }[];
  onSave: (opts: {
    applyAll: boolean;
    name: string;
    nameTouched: boolean;
    confirmedStaleKeys?: ReadonlySet<string>;
  }) => void;
  onClose: () => void;
}) {
  const [name, setName] = useState(workspaceName);
  // An untouched name field must never override a rename the apply-all
  // overlays carry in from a dirty Session window — only a name the user
  // actually typed here wins.
  const [nameTouched, setNameTouched] = useState(false);
  // The stale-window confirm sub-view. Each stale window starts DECLINED:
  // overwriting a value that moved underneath the edit is always an explicit
  // opt-in, never a default of a bulk gesture.
  const [confirming, setConfirming] = useState(false);
  const [confirmed, setConfirmed] = useState<Record<string, boolean>>({});
  const fileId = identifier(name) || workspaceName;
  const saving = saveState.kind === "saving";
  const hasStale = staleList.length > 0;
  const confirmedKeys = () =>
    new Set(staleList.filter((s) => confirmed[s.key]).map((s) => s.key));
  // A completed save returns to the summary view (which carries the "saved"
  // line); the confirm sub-view is a pre-save gesture, not a result surface.
  useEffect(() => {
    if (saveState.kind === "saved") setConfirming(false);
  }, [saveState.kind]);

  // `hasStale` guards the sub-view: an external change that resolves the last
  // stale window mid-confirm falls back to the summary rather than an empty list.
  if (confirming && hasStale) {
    const confirmCount = staleList.filter((s) => confirmed[s.key]).length;
    return (
      <div className="builder-inspector-stack" data-testid="builder-save-dialog">
        <div className="builder-warning" data-testid="save-stale-confirm">
          {count(staleList.length, "window")} changed underneath your edits.
          Check the ones whose edits should overwrite the current values; the
          rest are left open with their edits.
        </div>
        <label className="builder-checkbox-row">
          <input
            type="checkbox"
            checked={confirmCount === staleList.length}
            onChange={(e) =>
              setConfirmed(
                Object.fromEntries(staleList.map((s) => [s.key, e.target.checked])),
              )
            }
          />
          overwrite all
        </label>
        {staleList.map((s) => (
          <label key={s.key} className="builder-checkbox-row" data-testid="stale-confirm-row">
            <input
              type="checkbox"
              checked={confirmed[s.key] ?? false}
              onChange={(e) =>
                setConfirmed((prev) => ({ ...prev, [s.key]: e.target.checked }))
              }
            />
            {s.title}
          </label>
        ))}
        {saveState.kind === "failed" && (
          <div className="builder-warning">save failed: {saveState.message}</div>
        )}
        <div className="builder-preset-row">
          <Button
            variant="primary"
            disabled={!canSave || saving}
            onClick={() =>
              onSave({ applyAll: true, name, nameTouched, confirmedStaleKeys: confirmedKeys() })
            }
          >
            <Icon name="save" size={13} />{" "}
            {saving
              ? "Saving…"
              : confirmCount > 0
                ? `Overwrite ${count(confirmCount, "window")} and save`
                : "Save without the stale windows"}
          </Button>
          <Button disabled={saving} onClick={() => setConfirming(false)}>
            Back
          </Button>
        </div>
      </div>
    );
  }

  return (
    <div className="builder-inspector-stack" data-testid="builder-save-dialog">
      <div className="builder-site-derived">
        Saves to your session library as a resolvable file, listed with every
        other session and deployable from the rocket.
      </div>
      <Field
        label="save as"
        value={name}
        onChange={(value) => {
          setName(value);
          setNameTouched(true);
        }}
      />
      <div className="builder-site-derived">file id: {fileId}</div>
      {!canSave && dirtyWindows === 0 && (
        <div className="builder-warning">
          {blockedReason ?? "the session must resolve before it can be saved"}
        </div>
      )}
      {!canSave && dirtyWindows > 0 && (
        <div className="builder-warning" data-testid="save-preview-refused">
          the previewed edits do not resolve
          {blockedReason ? `: ${blockedReason}` : ""} — apply and save is
          unavailable; saving the applied state asks the server about the
          session without them
        </div>
      )}
      {saveState.kind === "saved" && (
        <div className="builder-site-derived" data-testid="save-confirm">
          saved as {saveState.name} — deploy it with the rocket
        </div>
      )}
      {saveState.kind === "failed" && (
        <div className="builder-warning">save failed: {saveState.message}</div>
      )}
      <div className="builder-preset-row">
        {dirtyWindows > 0 ? (
          <>
            <Button
              variant="primary"
              disabled={!canSave || saving}
              // With stale windows present, the primary opens the confirm flow
              // rather than applying blindly; with none it applies directly.
              onClick={() =>
                hasStale
                  ? setConfirming(true)
                  : onSave({ applyAll: true, name, nameTouched })
              }
            >
              <Icon name="save" size={13} />{" "}
              {saving
                ? "Saving…"
                : hasStale
                  ? `Apply ${count(dirtyWindows, "edit")} and save…`
                  : `Apply ${count(dirtyWindows, "edit")} and save`}
            </Button>
            <Button
              // The applied-only escape hatch is gated by the APPLIED
              // session's truth, which only the server knows once dirty
              // windows diverge the preview — so it always attempts, and a
              // refusal lands verbatim as the save error.
              disabled={saving}
              title={`leaves out the ${count(dirtyWindows, "window")} with unapplied edits`}
              onClick={() => onSave({ applyAll: false, name, nameTouched })}
            >
              Save applied state only
            </Button>
          </>
        ) : (
          <Button
            variant="primary"
            disabled={!canSave || saving}
            onClick={() => onSave({ applyAll: false, name, nameTouched })}
          >
            <Icon name="save" size={13} /> {saving ? "Saving…" : "Save"}
          </Button>
        )}
        <Button onClick={onClose}>Close</Button>
      </div>
    </div>
  );
}

export function BuilderView({
  active,
  colorMode,
  globeMode,
  referenceFrame,
  showSatPaths,
  showIslLinks,
  showGroundLinks,
  showGroundTracks,
  showTrails,
  actionsRef,
}: BuilderViewProps) {
  const {
    sessions,
    sessionsError,
    world,
    documentYaml,
    loadedDocument,
    loadedFile,
    loading,
    error,
    resolveError,
    settledArtifactSha256,
    deployReady,
    deployBlockers,
    loadSession,
    resolveDocument,
    saveSession,
    deploySession,
    refreshSessions,
    clear,
  } = useBuilderWorld();
  // Builder-local selection: inspect-only, never shared with the live view's
  // selection (two different worlds must not share a pointer).
  const [selection, setSelection] = useState<Selection | null>(null);
  // The authoring workspace: client-side drafts, resolve-checked on every
  // edit; the world on screen is always the resolver's expansion of it.
  const {
    workspace,
    startNew,
    openWorkspace,
    commitWorkspace,
    updateSession,
    undo,
    hasAutosave,
    restoreAutosave,
    stashAutosaveToBackup,
    hasBackup,
    restoreBackup,
    close: closeWorkspace,
    addConstellation,
    addConstellationRef,
    addDraft,
    removeRefSegment,
    removeConstellation,
    updateConstellation,
    addGroundRef,
    updateGroundRef,
    removeGroundRef,
    addGroundDraft,
    addGroundMember,
    replaceGroundRefWithDraft,
    updateGroundDraft,
    removeGroundDraft,
    convergeGroundToRef,
    addLinkRule,
    updateLinkRule,
    removeLinkRule,
    addRoutingDomain,
    updateRoutingDomain,
    removeRoutingDomain,
    addBoundary,
    updateBoundary,
    removeBoundary,
  } = useWorkspace();
  const nodeCatalog = useBuilderCatalog("nodes");
  const terminalCatalog = useBuilderCatalog("terminals");

  // --- Editing the running session ------------------------------------
  // Entering the builder beside a running session loads that session as
  // the workspace — rapid iteration between builder and cluster. The one
  // exception is an unsaved browser draft: autosave overwrites its slot
  // as soon as any workspace exists, so auto-importing over a draft would
  // silently destroy it — that case gets an explicit choice instead.
  const runningSession = sessions.find((s) => s.active) ?? null;
  const [importPending, setImportPending] = useState<BuilderSessionListEntry | null>(null);
  // A refused import names the session the user actually opened — the
  // running session is not the only thing the picker can open.
  const [importIssues, setImportIssues] = useState<{
    name: string;
    issues: string[];
  } | null>(null);
  // The file the current workspace was imported from (provenance marker).
  const [importedFrom, setImportedFrom] = useState<string | null>(null);
  // B3 backup refuse/choice: a gesture that would displace the current draft
  // (New, Open, auto-import adoption) first stashes it. If the stash is
  // REFUSED — a real, different draft already occupies the backup slot — the
  // gesture holds here and the choice dialog offers overwrite-or-cancel
  // instead of silently destroying either draft.
  const [pendingDisplace, setPendingDisplace] = useState<{
    label: string;
    proceed: () => void;
  } | null>(null);
  /** Run a displacing gesture, preserving the current draft to the backup
   *  slot first. On a refused stash, hold the gesture for the choice dialog. */
  const displace = (proceed: () => void, label: string) => {
    if (stashAutosaveToBackup() === "refused") {
      setPendingDisplace({ label, proceed });
      return;
    }
    proceed();
  };
  /** A self-ensuring creation gesture (M4): with a workspace open, just create
   *  (the gesture adds to it — no displacement). With none open, creating one
   *  displaces the prior autosave draft, so route through `displace` — preserve
   *  it to the backup with the refuse/overwrite choice, never a silent loss. */
  const ensureThenCreate = (create: () => void, label: string) => {
    if (workspace) create();
    else displace(create, label);
  };
  const startImport = (entry: BuilderSessionListEntry) => {
    setImportIssues(null);
    setImportPending(entry);
    loadSession(entry.file);
  };
  useEffect(() => {
    // The running session always loads on ENTRY — that is what entering the
    // builder beside a running cluster means. A browser draft never silently
    // stands in for it (a stale draft wearing the running session's name
    // showed an empty world while thirty nodes ran); a displaced draft is
    // preserved to the backup slot and restorable below. Gated on `active` so
    // a hidden builder is never a background importer, and keyed on the
    // module-scope marker so a hide/show toggle does not replay the import.
    if (!active || workspace || importPending || !runningSession) return;
    if (_importTriedFile === runningSession.file) return;
    _importTriedFile = runningSession.file;
    startImport(runningSession);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [active, workspace, importPending, runningSession]);
  // P2/B3 cross-phase contract: refresh the session list when the builder
  // regains visibility. After B3 the builder stays mounted, so "regains
  // visibility" is the active false->true transition, not a remount — the
  // mount-time fetch only fires on first entry, so a re-entry must refetch or
  // the running chip and auto-import target go stale on an external switch.
  const prevActiveRef = useRef(active);
  useEffect(() => {
    const wasActive = prevActiveRef.current;
    prevActiveRef.current = active;
    if (active && !wasActive) void refreshSessions();
  }, [active, refreshSessions]);
  useEffect(() => {
    if (!importPending || loadedDocument === null || loadedFile !== importPending.file) return;
    if (workspace) {
      // The user started something while the load was in flight — theirs wins.
      setImportPending(null);
      return;
    }
    const result = workspaceFromSessionDocument(loadedDocument);
    if (result.workspace) {
      // Preserve any displaced draft before adoption; if that stash is
      // refused, the choice dialog holds the adoption (the world stays on
      // screen read-only meanwhile — never a silently lossy workspace).
      const imported = result.workspace;
      const entry = importPending;
      displace(() => {
        openWorkspace(imported);
        setImportedFrom(entry.file);
      }, `loading ${entry.name}`);
    } else {
      // The world/YAML stay on screen read-only; the note says why the
      // session cannot be edited — never a silently lossy workspace.
      setImportIssues({ name: importPending.name, issues: result.issues });
    }
    setImportPending(null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [importPending, loadedDocument, loadedFile, workspace]);
  // A save is never a dead end: when any editor saves to the library, the
  // Library window opens (or focuses) and the panel lands on the asset.
  // Claimed through the module-level retired-nonce registry: a remount
  // never replays the last save, and each consumer role retires its own.
  const libraryReveal = useLibraryReveal();
  useEffect(() => {
    // Gated on `active` (B3): a hidden builder must not claim the reveal nonce
    // the shown view owns, nor pop a Library window in an invisible pane.
    if (!active) return;
    if (!claimLibraryReveal("opener", libraryReveal)) return;
    openEditor({ kind: "catalog" });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [active, libraryReveal]);
  // IG-1 ref floor: a placed reference has no editor, so its Use scrolls its
  // outline row into view and flashes it. A SEPARATE consume-once channel from
  // the Library reveal — a placement shows where it landed in the session
  // anatomy, it never opens the Library.
  const outlineReveal = useOutlineReveal();
  const [revealedSegment, setRevealedSegment] = useState<string | null>(null);
  useEffect(() => {
    // Clear the flash when the builder is hidden — otherwise the effect cleanup
    // cancels the reset timer and the row stays lit, replaying on every re-show
    // (the same reason freshId resets on !active).
    if (!active) {
      setRevealedSegment(null);
      return;
    }
    const claimed = claimOutlineReveal("outline", outlineReveal);
    if (!claimed) return;
    setRevealedSegment(claimed.segmentId);
    const timer = setTimeout(() => setRevealedSegment(null), 2600);
    return () => clearTimeout(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [active, outlineReveal]);
  useEffect(() => {
    if (!revealedSegment) return;
    // segment_ids are NATS-safe (lowercase, no dots/special chars), so they are
    // selector-safe; guard CSS.escape since jsdom does not provide the CSS global.
    const escaped =
      typeof CSS !== "undefined" && CSS.escape ? CSS.escape(revealedSegment) : revealedSegment;
    // scrollIntoView is optional-chained: jsdom does not implement it.
    document.querySelector(`[data-segment-id="${escaped}"]`)?.scrollIntoView?.({ block: "nearest" });
  }, [revealedSegment]);
  useEffect(() => {
    // The import must end with its resolve: a failed fetch or a competing
    // action (clear/+ New discards the in-flight response) would otherwise
    // leave "Loading…" claimed forever — a false in-progress display — and
    // permanently disable the edit-running path for this mount.
    if (!importPending || loading) return;
    if (loadedDocument !== null && loadedFile === importPending.file) return;
    setImportPending(null);
  }, [importPending, loading, loadedDocument, loadedFile]);
  // The floating-editor windows and their buffered edits (M18: useEditorWindows).
  const {
    windows,
    openEditor,
    annotateWindowSaved,
    closeWindow,
    closeAllWindows,
    isOpen,
    buffers,
    patchBuffer,
    revertBuffer,
    applyBuffer,
    previewWorkspace,
    dirtyWindows,
    staleKeys,
    staleList,
    loadCurrentValues,
    dropAppliedBuffers,
  } = useEditorWindows({
    workspace,
    updateSession,
    updateConstellation,
    updateGroundDraft,
    updateLinkRule,
    updateRoutingDomain,
    updateBoundary,
    convergeGroundToRef,
  });
  /** The wall's owning editor target, from the resolver's own scope — the
   *  serialized subject id maps to drafts via the same identifier()
   *  transform the serializer uses; never by parsing prose or runtime ids.
   *  Matched against the preview overlay: the refused document was
   *  serialized from it, so a dirty rename must be matched by the dirty
   *  draft, not the applied state. */
  const wallTarget = ((): { target: EditorTarget; key: string } | null => {
    if (!workspace || !resolveError) return null;
    const preview = previewWorkspace() ?? workspace;
    const subject = resolveError.subject;
    if (subject?.kind === "link_rule") {
      const rule = preview.links.find((r) => emittedRuleId(r) === subject.id);
      if (rule) {
        const target: EditorTarget = { kind: "link", id: rule.rule_id };
        return { target, key: targetKey(target) };
      }
    }
    if (subject?.kind === "routing_domain") {
      const domain = preview.routing_domains.find(
        (d) => emittedDomainId(d) === subject.id,
      );
      if (domain) {
        const target: EditorTarget = { kind: "domain", id: domain.domain_id };
        return { target, key: targetKey(target) };
      }
    }
    const segmentId = resolveError.segment_id;
    if (segmentId) {
      if (preview.space.some((d) => d.segment_id === segmentId)) {
        const target: EditorTarget = { kind: "segment", id: segmentId };
        return { target, key: targetKey(target) };
      }
      if (preview.ground.some((d) => d.segment_id === segmentId)) {
        const target: EditorTarget = { kind: "ground", id: segmentId };
        return { target, key: targetKey(target) };
      }
    }
    return null;
  })();
  /** Inline wall text for one open editor window (null = not this window's). */
  const wallFor = (target: EditorTarget): string | null =>
    wallTarget && targetKey(target) === wallTarget.key ? (resolveError?.error ?? null) : null;
  // THE edit→resolve loop — the only caller. Serializes applied state plus
  // dirty working copies so the canvas moves while you edit; Apply/Cancel
  // land here too (buffers change) and re-resolve the applied truth. The
  // library revision is a dependency on purpose: a user-catalog mutation
  // changes what saving this document would write (references dereference
  // server-side), so the settled artifact hash must re-settle.
  const libraryRevision = useLibraryRevision();
  // Whether the last preview serialized to a document that emits segments —
  // the only honest discriminator between "a resolve is coming" and the
  // all-held-back steady state that fires no resolve ever. A serializer throw
  // (P1's suffix-exhaustion cap, structurally near-impossible) is a refusal on
  // the same channel resolver refusals use, never an async crash.
  const [previewEmits, setPreviewEmits] = useState(false);
  const [serializeError, setSerializeError] = useState<string | null>(null);
  // A Restore that finds no payload (missing/corrupt) surfaces here instead of
  // silently doing nothing; the current workspace and its world stand (N10).
  const [restoreError, setRestoreError] = useState<string | null>(null);
  const hasDrafts =
    !!workspace &&
    workspace.space.length +
      workspace.space_refs.length +
      workspace.ground.length +
      workspace.ground_refs.length >
      0;
  useEffect(() => {
    // A pure no-op while no workspace exists: the running-session auto-import
    // in flight and the refused-import read-only display both depend on the
    // world surviving until a workspace appears. Clearing here would kill
    // auto-import and wipe the refused world.
    if (!workspace) return;
    const preview = previewWorkspace();
    if (!preview) return;
    let document: Record<string, unknown>;
    try {
      document = toSessionDocument(preview);
    } catch (e) {
      // Valid YAML or a clear refusal, never an async crash: clear the world
      // and render the throw as the state.
      setPreviewEmits(false);
      setSerializeError(e instanceof Error ? e.message : String(e));
      clear();
      return;
    }
    setSerializeError(null);
    const segments = (document.segments as unknown[] | undefined) ?? [];
    if (segments.length === 0) {
      // Emits nothing (no drafts, or every draft held back): the world/YAML/
      // status must stop describing a prior draft. clear() replaces the
      // content early-return; it never fires a resolve for empty content.
      setPreviewEmits(false);
      clear();
      return;
    }
    setPreviewEmits(true);
    const timer = setTimeout(() => {
      resolveDocument(document);
    }, 300);
    return () => clearTimeout(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [workspace, buffers, libraryRevision]);

  // Trust mechanics: Ctrl/Cmd+Z undoes the last workspace mutation unless
  // the user is typing in a field (native input undo wins there). Gated on
  // `active` (B3): a hidden-but-mounted builder must never intercept a Ctrl+Z
  // the live view's user intends for something else.
  useEffect(() => {
    if (!active) return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (!(event.ctrlKey || event.metaKey) || event.key.toLowerCase() !== "z") return;
      const target = event.target as HTMLElement | null;
      const tag = target?.tagName;
      if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") return;
      event.preventDefault();
      undo();
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [active, undo]);
  // IG-2: the object a create gesture just made — its editor focuses the
  // name once.
  const [freshId, setFreshId] = useState<string | null>(null);
  const [saveState, setSaveState] = useState<SaveState>({ kind: "idle" });
  const [copyState, setCopyState] = useState<"idle" | "copied" | "failed">("idle");
  // N13: the create-focus marker is one-shot. Drop it once the window it names
  // is gone (a closed window must not re-grab focus if that id reappears), and
  // whenever the builder is left, so returning never focuses a stale field.
  useEffect(() => {
    if (!active) {
      if (freshId !== null) setFreshId(null);
      return;
    }
    if (freshId && !windows.some((w) => "id" in w.target && w.target.id === freshId)) {
      setFreshId(null);
    }
  }, [active, windows, freshId]);
  // N16: the Copy result is transient feedback, cleared after a beat so the
  // control returns to its resting label.
  useEffect(() => {
    if (copyState === "idle") return;
    const t = setTimeout(() => setCopyState("idle"), 2000);
    return () => clearTimeout(t);
  }, [copyState]);
  // N14: the completeness rail reads this in two places (the guard and the
  // chip map); compute it once per workspace, not twice per render.
  const findings = useMemo(
    () => (workspace ? completenessFindings(workspace) : []),
    [workspace],
  );
  /** One save path for both dialog actions: build the exact document to
   *  save locally, save it, then adopt it — the saved artifact and the
   *  adopted workspace cannot diverge, and the race with the async state
   *  update is unrepresentable. */
  const performSave = async ({
    applyAll,
    name,
    nameTouched,
    confirmedStaleKeys,
  }: {
    applyAll: boolean;
    name: string;
    nameTouched: boolean;
    confirmedStaleKeys?: ReadonlySet<string>;
  }) => {
    if (!workspace) return;
    if (applyAll && staleKeys.size > 0 && confirmedStaleKeys === undefined) {
      // Fail-closed backstop: a bulk apply-and-save with stale windows must
      // pass through the confirm flow, which decides each stale window
      // (confirm = overwrite the moved value, decline = leave the window out).
      // A direct call carrying no decisions refuses rather than silently
      // overwrite an object the user reverted.
      setSaveState({
        kind: "failed",
        message: "confirm the stale windows before applying",
      });
      return;
    }
    setSaveState({ kind: "saving" });
    try {
      // Declined stale windows (stale, not confirmed) are excluded from the
      // overlay and survive the save open/dirty/stale; confirmed stale +
      // non-stale dirty buffers apply.
      const excludeKeys = new Set(
        [...staleKeys].filter((k) => !confirmedStaleKeys?.has(k)),
      );
      // The exact buffers this save overlays, captured by identity BEFORE the
      // network round-trip. The save is non-modal — other editor windows stay
      // live — so the post-save clear removes only these applied buffers,
      // matched by reference. Anything created or re-edited during the await
      // has a fresh identity and survives, and declined-stale buffers were
      // never in this set: no in-flight edit is silently discarded.
      const appliedBuffers = new Map(
        Object.entries(buffers).filter(([k, b]) => b.dirty && !excludeKeys.has(k)),
      );
      const next = workspaceForSave(workspace, buffers, {
        applyAll,
        dialogName: name,
        nameTouched,
        excludeKeys,
      });
      const result = await saveSession(toSessionDocument(next));
      if (next !== workspace) {
        commitWorkspace(next, applyAll ? "apply-all-save" : "save-rename");
      }
      if (applyAll) {
        dropAppliedBuffers(appliedBuffers);
      }
      setSaveState({
        kind: "saved",
        name: result.name,
        file: result.file,
        artifact_sha256: result.artifact_sha256,
      });
    } catch (e) {
      setSaveState({
        kind: "failed",
        message: e instanceof Error ? e.message : String(e),
      });
    }
  };
  // The deploy gate: server-hash equality between the saved artifact and
  // the settled resolve of what is on screen, with zero unapplied windows.
  const deployGate = canDeploy({
    savedFile:
      saveState.kind === "saved" || saveState.kind === "deployed" ? saveState.file : null,
    savedArtifactSha256:
      saveState.kind === "saved" || saveState.kind === "deployed"
        ? saveState.artifact_sha256
        : null,
    settledArtifactSha256,
    dirtyWindowCount: dirtyWindows,
    deployReady,
    deployBlockers,
  });
  // Standalone component authoring (Your library) — independent of sessions.
  const [libraryEditor, setLibraryEditor] = useState<
    | { kind: "terminal"; draft: DraftTerminal }
    | { kind: "node"; draft: DraftNode }
    | { kind: "site"; draft: DraftSiteObject }
    | null
  >(null);
  // The fifth save machine (the Library's "New node" window) adopts the shared
  // hook — gaining the in-flight saving state the hand-rolled copy lacked, so a
  // double-click no longer double-submits and shows a spurious "Overwrite?".
  const libraryNodeSave = useLibrarySave("nodes");
  const [libraryError, setLibraryError] = useState<string | null>(null);

  // The Library's per-entry gestures. USE places the block in the session
  // (self-ensuring: no open workspace starts one); EDIT forks it into an
  // editable draft; clicking the row inspects it.
  // Third-class member (N11): a Use/Customize gesture self-ensures a
  // workspace, so when one is created while a refused-import read-only world
  // is still on screen (world set, workspace null) the stale world would
  // render behind the new draft. The refused-import state is the only
  // triggering precondition, so the clear is narrow.
  const clearRefusedWorldBeforeCreate = () => {
    if (world && !workspace) clear();
  };

  const handleLibraryUse = (entry: BuilderCatalogEntry) => {
    setLibraryError(null);
    const label = `using ${entry.display_name ?? entry.id ?? entry.ref}`;
    const name = entry.display_name ?? entry.id ?? entry.ref;
    if (entry.family === "constellations") {
      // REF family: no editor exists for a placed reference (L6) — reveal its
      // outline row so the placement is visible (IG-1 ref floor).
      ensureThenCreate(() => {
        clearRefusedWorldBeforeCreate();
        requestOutlineReveal(addConstellationRef(entry.ref, name));
      }, label);
    } else if (entry.family === "site-sets") {
      ensureThenCreate(() => {
        clearRefusedWorldBeforeCreate();
        requestOutlineReveal(addGroundRef(entry.ref, name));
      }, label);
    } else if (entry.family === "nodes") {
      // DRAFT family: open the created segment's editor, focused for rename.
      ensureThenCreate(() => {
        clearRefusedWorldBeforeCreate();
        const id = addConstellation(entry.ref);
        openEditor({ kind: "segment", id });
        setFreshId(id);
      }, label);
    } else if (entry.family === "sites" && entry.id) {
      const siteId = entry.id;
      ensureThenCreate(() => {
        clearRefusedWorldBeforeCreate();
        const { segmentId, created } = addGroundMember(
          refGroundMember(entry.ref, siteId, entry.display_name ?? siteId, entry.summary),
          () => newDraftGroundSet(defaultGroundNodeRef ?? "", {}),
        );
        // Open the receiving set's editor either way; create-focus only a set
        // this Use actually created — never steal focus onto an existing set's
        // name (a rename footgun).
        openEditor({ kind: "ground", id: segmentId });
        if (created) setFreshId(segmentId);
      }, label);
    } else {
      // Fall-through: a sites entry with no id, or an unknown family — surface it
      // (IG-3), never a silent no-op branch.
      setLibraryError(
        `cannot use "${name}": ${
          entry.family === "sites"
            ? "the site has no id to place"
            : `unsupported family "${entry.family}"`
        }`,
      );
    }
  };

  const handleLibraryCustomize = async (entry: BuilderCatalogEntry) => {
    setLibraryError(null);
    try {
      const { document } = await readCatalogObject(entry.ref);
      if (entry.family === "terminals") {
        const seeded = draftTerminalFromDocument(document);
        setLibraryEditor({
          kind: "terminal",
          draft: {
            ...seeded,
            id: identifier(`${seeded.id}-custom`),
            display_name: `${seeded.display_name} (custom)`,
          },
        });
        openEditor({ kind: "library" });
      } else if (entry.family === "nodes") {
        const seeded = draftNodeFromDocument(document);
        setLibraryEditor({
          kind: "node",
          draft: {
            ...seeded,
            id: identifier(`${seeded.id}-custom`),
            display_name: `${seeded.display_name} (custom)`,
          },
        });
        openEditor({ kind: "library" });
      } else if (entry.family === "constellations") {
        const constellation = (document as { constellation?: { orbit?: unknown } })
          .constellation;
        const orbitRef =
          typeof constellation?.orbit === "string" ? constellation.orbit : null;
        const orbitDocument = orbitRef
          ? (await readCatalogObject(orbitRef)).document
          : null;
        const draft = draftConstellationFromDocuments(document, orbitDocument);
        ensureThenCreate(() => {
          clearRefusedWorldBeforeCreate();
          addDraft(draft);
          openEditor({ kind: "segment", id: draft.segment_id });
        }, `customizing ${entry.display_name ?? entry.id ?? entry.ref}`);
      } else if (entry.family === "site-sets") {
        const draft = await forkGroundSet(entry.ref);
        ensureThenCreate(() => {
          clearRefusedWorldBeforeCreate();
          addGroundDraft(draft);
          openEditor({ kind: "ground", id: draft.segment_id });
        }, `customizing ${entry.display_name ?? entry.id ?? entry.ref}`);
      } else if (entry.family === "sites") {
        const seeded = draftSiteFromDocument(document);
        setLibraryEditor({
          kind: "site",
          draft: {
            ...seeded,
            site_id: identifier(`${seeded.site_id}-custom`),
            display_name: `${seeded.display_name} (custom)`,
          },
        });
        openEditor({ kind: "library" });
      }
    } catch (e) {
      setLibraryError(e instanceof Error ? e.message : String(e));
    }
  };

  /** Fork a site set into an editable ground draft: read the set document,
   *  then every member site document. Referenced members stay references at
   *  full fidelity; inline members become editable site drafts. */
  const forkGroundSet = async (ref: string): Promise<DraftGroundSet> => {
    const { document } = await readCatalogObject(ref);
    const memberRefs =
      (document as { site_set?: { sites?: unknown[] } }).site_set?.sites ?? [];
    const siteEntries = await Promise.all(
      memberRefs.map(async (member) => {
        if (typeof member === "string") {
          return { ref: member, document: (await readCatalogObject(member)).document };
        }
        const inline = member as Record<string, unknown>;
        return { ref: null, document: "site" in inline ? inline : { site: inline } };
      }),
    );
    return draftGroundSetFromDocuments(document, siteEntries);
  };

  const handleLibraryInspect = async (entry: BuilderCatalogEntry) => {
    setLibraryError(null);
    try {
      const { document } = await readCatalogObject(entry.ref);
      openEditor({ kind: "inspect", ref: entry.ref, document });
    } catch (e) {
      setLibraryError(e instanceof Error ? e.message : String(e));
    }
  };

  const handleLibraryNew = (family: string) => {
    setLibraryError(null);
    if (family === "terminals") {
      setLibraryEditor({ kind: "terminal", draft: defaultDraftTerminal() });
      openEditor({ kind: "library" });
    } else if (family === "nodes") {
      // A fresh node starts with a clean save machine — otherwise a prior
      // failed/conflict would carry over (a stale warning, or a silent
      // overwrite:true on the new node). Matches ConstellationEditor's reset.
      libraryNodeSave.reset();
      setLibraryEditor({ kind: "node", draft: defaultDraftNode() });
      openEditor({ kind: "library" });
    } else if (family === "constellations" && defaultNodeRef) {
      const draft = newDraftConstellation(defaultNodeRef);
      ensureThenCreate(() => {
        addDraft(draft);
        openEditor({ kind: "segment", id: draft.segment_id });
        setFreshId(draft.segment_id);
      }, "adding a constellation");
    } else if (family === "sites" && defaultGroundNodeRef) {
      void (async () => {
        try {
          const { document } = await readCatalogObject(defaultGroundNodeRef);
          const node = (document as { node?: Record<string, unknown> }).node;
          const mounts = (
            (node?.terminals as Record<string, unknown>[] | undefined) ?? []
          ).map((mount) => [String(mount.id), Number(mount.count ?? 1)] as const);
          setLibraryEditor({
            kind: "site",
            draft: newDraftSiteObject(defaultGroundNodeRef, Object.fromEntries(mounts)),
          });
          openEditor({ kind: "library" });
        } catch (e) {
          setLibraryError(e instanceof Error ? e.message : String(e));
        }
      })();
    } else if (family === "site-sets" && defaultGroundNodeRef) {
      // Seed installed mounts from the default model's faceplate so the new
      // segment starts complete; blank-first on sites (paste/gazetteer next).
      void (async () => {
        try {
          const { document } = await readCatalogObject(defaultGroundNodeRef);
          const node = (document as { node?: Record<string, unknown> }).node;
          const mounts = (
            (node?.terminals as Record<string, unknown>[] | undefined) ?? []
          ).map((mount) => [String(mount.id), Number(mount.count ?? 1)] as const);
          const draft = newDraftGroundSet(
            defaultGroundNodeRef,
            Object.fromEntries(mounts),
          );
          ensureThenCreate(() => {
            addGroundDraft(draft);
            openEditor({ kind: "ground", id: draft.segment_id });
            setFreshId(draft.segment_id);
          }, "adding a ground segment");
        } catch (e) {
          setLibraryError(e instanceof Error ? e.message : String(e));
        }
      })();
    }
  };
  // The connect gesture (IG-7): both endpoints known before the rule
  // exists, physics derived from the resolved world's faceplates.
  const segmentCapabilities = useMemo(() => capabilitiesBySegment(world), [world]);
  // Beam discs on the body while a segment's editor is open: every satellite
  // of that segment, at the elevation floor its access terminals actually
  // declare — the resolved world is the only physics source (a node with no
  // access terminal simply has no beam). The selected satellite always shows
  // (CoverageFootprint unions it in) and reads the same real floors here.
  const beamFootprints = useMemo(() => {
    if (!world) return undefined;
    const openSegments = new Set(
      windows
        .filter((w) => w.target.kind === "segment")
        .map((w) => (w.target as { kind: "segment"; id: string }).id),
    );
    const nodeIds = world.nodes
      .filter((n) => n.kind === "satellite" && openSegments.has(n.segment_id))
      .map((n) => n.node_id);
    const byId = new Map(world.nodes.map((n) => [n.node_id, n]));
    return {
      nodeIds,
      elevationFor: (nodeId: string) => {
        const node = byId.get(nodeId);
        return node ? accessBeamElevationDeg(node) : null;
      },
    };
  }, [world, windows]);
  const openRule = (ruleId: string) => {
    openEditor({ kind: "link", id: ruleId });
  };
  // The tree-row connect gesture: a spline IconButton (the app's link
  // glyph) opens a readable target list under the row — the port-picker
  // idiom. A select posing as the control failed twice: wide it read as a
  // blank broken field, narrow it read as glyph soup.
  const [connectFor, setConnectFor] = useState<string | null>(null);
  const connectTargets = (selfId: string) =>
    (workspace ? placedSegments(workspace) : []).map((segment) => ({
      value: segment.segment_id,
      label:
        segment.segment_id === selfId
          ? `${segment.label} (mesh)`
          : `${segment.label} (${segment.kind})`,
    }));
  const connect = (fromSegmentId: string, targetSegmentId: string) => {
    if (!workspace) return;
    setConnectFor(null);
    try {
      const rule = connectSegments(workspace, world, fromSegmentId, targetSegmentId);
      addLinkRule(rule);
      openEditor({ kind: "link", id: rule.rule_id });
      setFreshId(rule.rule_id);
    } catch (e) {
      setLibraryError(e instanceof Error ? e.message : String(e));
    }
  };
  const connectButton = (segmentId: string, label: string) => (
    <IconButton
      icon="spline"
      size={12}
      active={connectFor === segmentId}
      label={`Connect ${label}: pick the other end — physics derive from both faceplates`}
      onClick={() => setConnectFor(connectFor === segmentId ? null : segmentId)}
    />
  );
  const connectPicker = (segmentId: string) =>
    connectFor === segmentId ? (
      <div className="builder-terminal-picker" data-testid="connect-picker">
        {connectTargets(segmentId).map((target) => (
          <button
            key={target.value}
            className="builder-outline-row"
            onClick={() => connect(segmentId, target.value)}
          >
            <span className="builder-outline-name">
              <Icon name="spline" size={12} />
              {target.label}
            </span>
          </button>
        ))}
      </div>
    ) : null;

  // Default node model for a fresh constellation: prefer the catalog's space
  // nodes (directory layout is authoring convention, so this is a display
  // heuristic only — the picker offers every node either way).
  const defaultNodeRef =
    nodeCatalog.entries.find((e) => !e.error && e.ref.includes("nodes/space/"))?.ref ??
    nodeCatalog.entries.find((e) => !e.error)?.ref ??
    null;
  // Same heuristic for a fresh ground segment's template node.
  const defaultGroundNodeRef =
    nodeCatalog.entries.find((e) => !e.error && e.ref.includes("nodes/ground/"))?.ref ??
    nodeCatalog.entries.find((e) => !e.error)?.ref ??
    null;
  /** The body of one floating editor window. Null = the object no longer
   *  exists (undo, removal); the window simply doesn't render. */
  function renderWindow(
    target: EditorTarget,
  ): { title: string; content: React.ReactNode } | null {
    if (!workspace && target.kind !== "inspect" && target.kind !== "node-view") return null;
    switch (target.kind) {
      case "session": {
        if (!workspace) return null;
        const key = targetKey(target);
        const sessionPick: SessionBuffer = {
          name: workspace.name,
          start_time: workspace.start_time,
          step_seconds: workspace.step_seconds,
          compression: workspace.compression,
          max_pairs_per_rule: workspace.max_pairs_per_rule,
          max_pairs_per_tick: workspace.max_pairs_per_tick,
        };
        const buf = buffers[key];
        const view = buf ? { ...workspace, ...(buf.draft as SessionBuffer) } : workspace;
        return {
          title: `Session · ${view.name}`,
          content: (
            <SessionEditor
              workspace={view}
              onUpdate={(patch) =>
                patchBuffer(key, sessionPick, (d) => ({ ...d, ...patch }))
              }
            />
          ),
        };
      }
      case "segment": {
        const applied = workspace?.space.find((d) => d.segment_id === target.id);
        if (!workspace || !applied) return null;
        const key = targetKey(target);
        const draft = (buffers[key]?.draft as DraftConstellation | undefined) ?? applied;
        const segWall = wallFor(target);
        return {
          title: draft.display_name,
          content: (
            <>
            {segWall && (
              <div className="builder-wall" data-testid="wall-banner">
                {segWall}
              </div>
            )}
            <ConstellationEditor
              key={draft.segment_id}
              autoFocusName={freshId === draft.segment_id}
              // N28: the dwell readout reads the PREVIEW (applied + dirty
              // overlays) so a dirty session buffer's start_time is reflected,
              // never a stale applied value.
              workspace={previewWorkspace() ?? workspace}
              onOpenRule={openRule}
              onConnect={(other) => connect(draft.segment_id, other)}
              draft={draft}
              onUpdate={(update) => patchBuffer(key, applied, update)}
              onUpdateOrbit={(patch) =>
                patchBuffer(key, applied, (d) => ({
                  ...d,
                  orbit: { ...d.orbit, ...patch },
                }))
              }
              onRemove={() => {
                removeConstellation(draft.segment_id);
                closeWindow(key);
              }}
            />
            </>
          ),
        };
      }
      case "ground": {
        const applied = workspace?.ground.find((d) => d.segment_id === target.id);
        if (!workspace || !applied) return null;
        const key = targetKey(target);
        const draft = (buffers[key]?.draft as DraftGroundSet | undefined) ?? applied;
        const groundWall = wallFor(target);
        return {
          title: draft.display_name,
          content: (
            <>
            {groundWall && (
              <div className="builder-wall" data-testid="wall-banner">
                {groundWall}
              </div>
            )}
            <GroundEditor
              key={draft.segment_id}
              autoFocusName={freshId === draft.segment_id}
              workspace={workspace}
              onOpenRule={openRule}
              onConnect={(other) => connect(draft.segment_id, other)}
              draft={draft}
              onUpdate={(update) => patchBuffer(key, applied, update)}
              onSaved={(ref, snapshot) => annotateWindowSaved(key, ref, snapshot)}
              onRemove={() => {
                removeGroundDraft(draft.segment_id);
                closeWindow(key);
              }}
            />
            </>
          ),
        };
      }
      case "link": {
        const applied = workspace?.links.find((r) => r.rule_id === target.id);
        if (!workspace || !applied) return null;
        const key = targetKey(target);
        const rule = (buffers[key]?.draft as DraftLinkRule | undefined) ?? applied;
        const ruleWall = wallFor(target);
        const ruleAllocation =
          world?.allocations.find((a) => a.rule_id === emittedRuleId(rule)) ?? null;
        return {
          title: rule.label || rule.rule_id,
          content: (
            <>
            {ruleWall && (
              <div className="builder-wall" data-testid="wall-banner">
                {ruleWall}
              </div>
            )}
            <LinkRuleEditor
              key={rule.rule_id}
              autoFocusName={freshId === rule.rule_id}
              workspace={workspace}
              rule={rule}
              capabilities={segmentCapabilities}
              allocation={ruleAllocation}
              onRepoint={(side, newSegmentId) => {
                const { patch, notice } = rederiveRule(
                  workspace,
                  world,
                  rule,
                  side,
                  newSegmentId,
                );
                patchBuffer(key, applied, (r) => ({ ...r, ...patch }));
                return notice;
              }}
              onUpdate={(patch) =>
                patchBuffer(key, applied, (r) => ({ ...r, ...patch }))
              }
              onUpdateEndpoint={(side, patch) =>
                patchBuffer(key, applied, (r) => ({
                  ...r,
                  [side]: { ...r[side], ...patch },
                }))
              }
              onRemove={() => {
                removeLinkRule(rule.rule_id);
                closeWindow(key);
              }}
            />
            </>
          ),
        };
      }
      case "domain": {
        const applied = workspace?.routing_domains.find(
          (d) => d.domain_id === target.id,
        );
        if (!workspace || !applied) return null;
        const key = targetKey(target);
        const domain = (buffers[key]?.draft as DraftRoutingDomain | undefined) ?? applied;
        const domainWall = wallFor(target);
        return {
          title: domain.label,
          content: (
            <>
            {domainWall && (
              <div className="builder-wall" data-testid="wall-banner">
                {domainWall}
              </div>
            )}
            <RoutingDomainEditor
              key={domain.domain_id}
              autoFocusName={freshId === domain.domain_id}
              workspace={workspace}
              domain={domain}
              onUpdate={(patch) =>
                patchBuffer(key, applied, (d) => ({ ...d, ...patch }))
              }
              onRemove={() => {
                removeRoutingDomain(domain.domain_id);
                closeWindow(key);
              }}
            />
            </>
          ),
        };
      }
      case "boundary": {
        const applied = workspace?.boundaries.find(
          (b) => b.boundary_id === target.id,
        );
        if (!workspace || !applied) return null;
        const key = targetKey(target);
        const boundary = (buffers[key]?.draft as DraftBoundary | undefined) ?? applied;
        return {
          title: "Boundary",
          content: (
            <BoundaryEditor
              key={boundary.boundary_id}
              workspace={workspace}
              boundary={boundary}
              onUpdate={(patch) =>
                patchBuffer(key, applied, (b) => ({ ...b, ...patch }))
              }
              onRemove={() => {
                removeBoundary(boundary.boundary_id);
                closeWindow(key);
              }}
            />
          ),
        };
      }
      case "inspect":
        return {
          title: target.ref,
          content: <CatalogObjectView refStr={target.ref} document={target.document} />,
        };
      case "node-view": {
        const node = world?.nodes.find((n) => n.node_id === target.nodeId);
        if (!world || !node) return null;
        const owner =
          workspace?.space.find((d) => d.segment_id === node.segment_id) ??
          workspace?.ground.find((d) => d.segment_id === node.segment_id);
        const ownerKind = workspace?.space.some((d) => d.segment_id === node.segment_id)
          ? ("segment" as const)
          : ("ground" as const);
        return {
          title: node.node_id,
          content: (
            <div className="builder-inspector-stack">
              <BuilderInspector node={node} ephemeris={world.ephemeris} />
              {owner ? (
                <Button
                  onClick={() => openEditor({ kind: ownerKind, id: node.segment_id })}
                >
                  Edit {"display_name" in owner ? owner.display_name : node.segment_id}
                </Button>
              ) : (
                <div className="builder-zone-empty">
                  placed by reference — customize the block to edit it
                </div>
              )}
            </div>
          ),
        };
      }
      case "open-session": {
        // Open is a picker, not an inline dropdown: your saved sessions and
        // the shipped NodalArc sessions, each a row you open. Sessions the
        // editors cannot represent open read-only and say why.
        const openEntry = (entry: BuilderSessionListEntry) => {
          // Preserve the current draft before opening displaces it; a refused
          // stash holds this gesture for the choice dialog.
          displace(() => {
            // N11: clear before the swap so the previous session's world does
            // not render during the new load; the load's own resolve owns
            // failure (error-clears-world), so no conditional guard is needed.
            clear();
            closeWorkspace();
            closeAllWindows();
            setSelection(null);
            setImportedFrom(null);
            setSaveState({ kind: "idle" });
            startImport(entry);
          }, `opening ${entry.name}`);
        };
        // The server names each entry's source root; the tiers speak the
        // library's own vocabulary (★ yours / nodalarc library).
        const yours = sessions.filter((s) => s.source === "user");
        const shipped = sessions.filter((s) => s.source === "nodalarc");
        const group = (label: string, list: BuilderSessionListEntry[]) =>
          list.length === 0 ? null : (
            <div className="builder-picker-group" key={label}>
              <div className="builder-outline-kind">{label}</div>
              {list.map((entry) => (
                <button
                  className="builder-outline-row builder-picker-row"
                  key={entry.file}
                  onClick={() => openEntry(entry)}
                  title={`Open ${entry.name}`}
                >
                  <span className="builder-outline-name">
                    <Icon name="folder-open" size={12} />
                    {entry.name}
                    {entry.active ? " · running" : ""}
                  </span>
                  <span className="builder-outline-count">{entry.constellation}</span>
                </button>
              ))}
            </div>
          );
        return {
          title: "Open a session",
          content: (
            <div className="builder-picker" data-testid="builder-open-picker">
              {sessions.length === 0 && (
                <div className="builder-zone-empty">no sessions found</div>
              )}
              {group("★ yours", yours)}
              {group("nodalarc library", shipped)}
              {sessionsError && (
                <div className="builder-warning">{sessionsError}</div>
              )}
            </div>
          ),
        };
      }
      case "save-session": {
        // Save is a small dialog, not a silent write: confirm the name it
        // saves under (into your library), then Save. Deploy stays its own
        // toolbar action.
        const canSave = !!workspace && !!world && !error && !loading;
        return {
          title: "Save session",
          content: workspace ? (
            <SaveSessionDialog
              // Remount per workspace identity: the buffered name follows
              // the session being saved, not a previous one.
              key={workspace.name}
              workspaceName={workspace.name}
              canSave={canSave}
              blockedReason={error}
              saveState={saveState}
              dirtyWindows={dirtyWindows}
              staleList={staleList}
              onSave={(opts) => void performSave(opts)}
              onClose={() => closeWindow("save-session")}
            />
          ) : (
            <div className="builder-zone-empty">nothing to save yet</div>
          ),
        };
      }
      case "catalog": {
        // The library is everything you could use — a separate surface on
        // purpose. The rail lists only what this session is using; the two
        // read alike, and side by side they were indistinguishable.
        return {
          title: "Library",
          content: (
            <>
              <LibraryPanel
                onUse={handleLibraryUse}
                onCustomize={(entry) => void handleLibraryCustomize(entry)}
                onInspect={(entry) => void handleLibraryInspect(entry)}
                onNew={handleLibraryNew}
              />
              {libraryError && <div className="builder-warning">{libraryError}</div>}
            </>
          ),
        };
      }
      case "library": {
        if (!libraryEditor) return null;
        if (libraryEditor.kind === "terminal") {
          return {
            title: "New terminal",
            content: (
              <TerminalEditor
                draft={libraryEditor.draft}
                onChange={(update) =>
                  setLibraryEditor((prev) =>
                    prev?.kind === "terminal" ? { kind: "terminal", draft: update(prev.draft) } : prev,
                  )
                }
                catalog={terminalCatalog.entries}
                onSaved={() => {
                  setLibraryEditor(null);
                  closeWindow("library");
                  void terminalCatalog.refresh();
                }}
                onCancel={() => {
                  setLibraryEditor(null);
                  closeWindow("library");
                }}
              />
            ),
          };
        }
        if (libraryEditor.kind === "site") {
          return {
            title: "New site",
            content: (
              <SiteEditor
                key="library-site"
                autoFocusName
                site={libraryEditor.draft}
                onUpdate={(update) =>
                  setLibraryEditor((prev) =>
                    prev?.kind === "site" ? { kind: "site", draft: update(prev.draft) } : prev,
                  )
                }
                onClose={() => {
                  setLibraryEditor(null);
                  closeWindow("library");
                }}
              />
            ),
          };
        }
        return {
          title: "New node",
          content: (
            <div className="builder-inspector-stack">
              <NodeEditor
                key="library-node"
                autoFocusName
                draft={libraryEditor.draft}
                onChange={(update) =>
                  setLibraryEditor((prev) =>
                    prev?.kind === "node" ? { kind: "node", draft: update(prev.draft) } : prev,
                  )
                }
              />
              <div className="builder-preset-row">
                <Button
                  variant="primary"
                  disabled={libraryNodeSave.saving}
                  onClick={() =>
                    void libraryNodeSave.save(
                      { node: nodeObjectFromDraft(libraryEditor.draft) },
                      () => {
                        setLibraryEditor(null);
                        closeWindow("library");
                      },
                    )
                  }
                >
                  {libraryNodeSave.label("Save node to library")}
                </Button>
                <Button
                  onClick={() => {
                    setLibraryEditor(null);
                    closeWindow("library");
                  }}
                >
                  Cancel
                </Button>
              </div>
              {libraryNodeSave.state.kind === "failed" && (
                <div className="builder-warning">{libraryNodeSave.state.message}</div>
              )}
            </div>
          ),
        };
      }
    }
  }

  // Ground segments enumerate their members in the tree (small, placed sets —
  // click-at-your-granularity). Space segments stay aggregates; individual
  // satellites are spot-checked on the canvas, not listed 176-deep.
  const [expandedSegment, setExpandedSegment] = useState<string | null>(null);

  const segments = useMemo(() => (world ? summarizeSegments(world) : []), [world]);
  const bodyGroups = useMemo(() => groupByBody(segments), [segments]);
  const satelliteCount = world
    ? world.nodes.filter((n) => n.kind === "satellite").length
    : 0;
  const groundCount = world ? world.nodes.length - satelliteCount : 0;

  // Scene input derived from the resolved world, frozen at the session epoch.
  // A derivation failure is a real finding — surfaced in the status bar, and
  // nothing renders (never a partially-wrong world).
  // A different world invalidates the selection (stale node ids).
  const worldKey = world ? `${world.session.name}:${world.epoch_unix}` : null;
  useEffect(() => setSelection(null), [worldKey]);

  const { snapshot, snapshotError } = useMemo((): {
    snapshot: StateSnapshot | null;
    snapshotError: string | null;
  } => {
    if (!world) return { snapshot: null, snapshotError: null };
    try {
      return { snapshot: builderSnapshotFromWorld(world), snapshotError: null };
    } catch (e) {
      return { snapshot: null, snapshotError: e instanceof Error ? e.message : String(e) };
    }
  }, [world]);
  const regimeById = useMemo(() => buildRegimeIndex(world?.ephemeris ?? null), [world]);

  // Rule-scoped preview candidates at the epoch: the server decides the
  // geometry (through the runtime's own visibility composites) and ships the
  // verdicts on the world; this adapts them into the overlay and the rule
  // notes, deciding nothing itself. Toolbar link toggles gate the overlay per
  // kind, exactly as they gate the live view's link layers.
  const candidates = useMemo(() => (world ? computeCandidates(world) : null), [world]);
  const visiblePairs = useMemo(
    () =>
      candidates?.pairs.filter((pair) =>
        pair.kind === "access" ? showGroundLinks : showIslLinks,
      ) ?? [],
    [candidates, showGroundLinks, showIslLinks],
  );
  const darkRules = candidates
    ? candidates.previews.filter((p) => p.enabled && p.candidates === 0).length
    : 0;
  const ruleNotes = candidates
    ? candidates.previews
        .filter((p) => p.note)
        .map((p) => `${p.rule_id}: ${p.note}`)
        .join("\n")
    : "";

  return (
    <div className="builder-shell" data-testid="builder-shell">
      {/* Session verbs live here, with standard icons — a toolbar, like
          every other application. The rail below is session content only;
          the Library is one surface (its window), opened from here. */}
      <div className="builder-toolbar" data-testid="builder-toolbar">
        <span className="builder-mode-badge">Session Builder</span>
        <span className="builder-toolbar-group">
          <IconButton
            className="builder-toolbar-btn"
            icon="file-plus"
            size={17}
            label="New session — blank sheet (any current draft stays under Restore)"
            onClick={() => {
              displace(() => {
                clear();
                setSelection(null);
                closeAllWindows();
                setImportedFrom(null);
                setImportIssues(null);
                setSaveState({ kind: "idle" });
                startNew("untitled-session");
              }, "starting a new session");
            }}
          />
          <IconButton
            className="builder-toolbar-btn"
            icon="folder-open"
            size={17}
            disabled={!!importPending}
            label={importPending ? "Opening…" : "Open a session — from your library or the NodalArc library"}
            onClick={() => {
              // N15: the picker's running chip and auto-import target must not
              // claim a session the cluster switched away from — refetch on open.
              void refreshSessions();
              openEditor({ kind: "open-session" });
            }}
          />
          <IconButton
            className="builder-toolbar-btn"
            icon="save"
            size={17}
            disabled={!workspace}
            label={workspace ? "Save session to your library" : "Nothing to save yet"}
            onClick={() => openEditor({ kind: "save-session" })}
          />
          <IconButton
            className="builder-toolbar-btn"
            icon="rocket"
            size={17}
            disabled={!deployGate.ok}
            label={
              deployGate.ok && (saveState.kind === "saved" || saveState.kind === "deployed")
                ? `Deploy ${saveState.file} to cluster — the same switch every NodalArc session uses`
                : (deployGate.reason ?? "save the session first, then deploy")
            }
            onClick={async () => {
              // The gate is the truth: deploy ships exactly the saved file,
              // and only while it matches the settled resolve on screen.
              if (!deployGate.ok) return;
              if (saveState.kind !== "saved" && saveState.kind !== "deployed") return;
              const { name, file, artifact_sha256 } = saveState;
              setSaveState({ kind: "deploying", name, file, artifact_sha256 });
              try {
                await deploySession(file);
                setSaveState({ kind: "deployed", name, file, artifact_sha256 });
              } catch (e) {
                setSaveState({
                  kind: "failed",
                  message: e instanceof Error ? e.message : String(e),
                });
              }
            }}
          />
          <IconButton
            className="builder-toolbar-btn"
            icon="history"
            size={17}
            disabled={!hasBackup() && !hasAutosave()}
            label={
              hasBackup()
                ? "Restore — bring back the draft the last Open or New displaced"
                : hasAutosave()
                  ? "Restore the autosaved draft from this browser"
                  : "Nothing to restore"
            }
            onClick={() => {
              // N10/N9: clear and reset ONLY when the restore actually swaps
              // the workspace. On a missing/corrupt/unmigratable payload the
              // restore refuses with a reason and the stored value survives;
              // the current world/YAML/status stand (an unchanged workspace
              // never re-fires the resolve loop, so clear-then-fail would
              // strand a null world), and the reason surfaces.
              const result = hasBackup() ? restoreBackup() : restoreAutosave();
              if (result.ok) {
                closeAllWindows();
                setSelection(null);
                setImportedFrom(null);
                setImportIssues(null);
                setSaveState({ kind: "idle" });
                setRestoreError(null);
                clear();
              } else {
                setRestoreError(result.reason);
              }
            }}
          />
        </span>
        <IconButton
          className="builder-toolbar-btn"
          icon="library"
          size={17}
          label="Library — every block you could build with, shipped and yours"
          onClick={() => openEditor({ kind: "catalog" })}
        />
      </div>
      <div className="builder-outline" data-testid="builder-outline">
        <div className="builder-zone-title">World</div>
        {importIssues && (
          <div className="builder-warning" data-testid="import-issues">
            {importIssues.name} cannot be edited in the builder yet:
            {importIssues.issues.map((issue) => (
              <div key={issue}>· {issue}</div>
            ))}
          </div>
        )}
        {workspace && (nodeCatalog.error || terminalCatalog.error) && (
          <div className="builder-warning" data-testid="builder-catalog-error">
            hardware catalog unavailable — new constellations need it.{" "}
            {nodeCatalog.error ?? terminalCatalog.error}{" "}
            <Button
              onClick={() => {
                void nodeCatalog.refresh();
                void terminalCatalog.refresh();
              }}
            >
              retry
            </Button>
          </div>
        )}
        {workspace && (
          <BuildGuide
            workspace={workspace}
            saved={saveState.kind === "saved" || saveState.kind === "deploying" || saveState.kind === "deployed" ? ("name" in saveState ? saveState.name : null) : null}
            deployed={saveState.kind === "deployed"}
            resolvedSiteCount={world ? distinctGroundStationSites(world.nodes) : null}
            onAddConstellation={() => {
              if (!defaultNodeRef) return;
              const draft = newDraftConstellation(defaultNodeRef);
              ensureThenCreate(() => {
                addDraft(draft);
                openEditor({ kind: "segment", id: draft.segment_id });
                setFreshId(draft.segment_id);
              }, "adding a constellation");
            }}
            onAddGround={() => handleLibraryNew("site-sets")}
            onAddDomain={() => {
              const domain = defaultRoutingDomain(workspace);
              addRoutingDomain(domain);
              openEditor({ kind: "domain", id: domain.domain_id });
              setFreshId(domain.domain_id);
            }}
            onOpenSession={() => openEditor({ kind: "session" })}
            onOpenSegment={(kind, id) => openEditor({ kind, id })}
          />
        )}
        {workspace && (
          <div className="builder-outline-group" data-testid="builder-drafts">
            <div className="builder-library-entry">
              <span className="builder-outline-kind">
                Drafts · {workspace.name}
                {importedFrom && sessions.find((s) => s.file === importedFrom)?.active && (
                  <span
                    className="builder-running-chip"
                    title="Loaded from the session that was running when this workspace was opened"
                  >
                    running
                  </span>
                )}
              </span>
              <span className="builder-library-actions">
                <span className="builder-outline-count">
                  {workspace.step_seconds === 1 && workspace.compression === 1
                    ? "real time"
                    : `×${workspace.compression}`}
                </span>
                <IconButton
                  icon="settings"
                  size={13}
                  label="Session settings — name, time, candidate budget"
                  onClick={() => openEditor({ kind: "session" })}
                />
              </span>
            </div>
            {workspace.space_refs.map((placed) => (
              <Fragment key={placed.segment_id}>
              <div
                className={`builder-library-entry${
                  revealedSegment === placed.segment_id ? " builder-outline-row--revealed" : ""
                }`}
                data-segment-id={placed.segment_id}
              >
                <span className="builder-outline-name builder-outline-name--space builder-outline-row--segment">
                  <Icon name="orbit" size={12} />
                  {placed.label}
                </span>
                <span className="builder-library-actions">
                  {connectButton(placed.segment_id, placed.label)}
                  <IconButton
                    icon="pencil"
                    size={12}
                    label="Customize: fork into an editable draft"
                    onClick={() =>
                      void (async () => {
                        try {
                          const { document } = await readCatalogObject(placed.ref);
                          const constellation = (
                            document as { constellation?: { orbit?: unknown } }
                          ).constellation;
                          const orbitRef =
                            typeof constellation?.orbit === "string"
                              ? constellation.orbit
                              : null;
                          const orbitDocument = orbitRef
                            ? (await readCatalogObject(orbitRef)).document
                            : null;
                          const draft = draftConstellationFromDocuments(
                            document,
                            orbitDocument,
                          );
                          removeRefSegment(placed.segment_id);
                          addDraft(draft);
                          openEditor({ kind: "segment", id: draft.segment_id });
                        } catch (e) {
                          setLibraryError(e instanceof Error ? e.message : String(e));
                        }
                      })()
                    }
                  />
                  <IconButton
                    icon="x"
                    size={12}
                    label="Remove from the session"
                    onClick={() => removeRefSegment(placed.segment_id)}
                  />
                </span>
              </div>
              {connectPicker(placed.segment_id)}
              </Fragment>
            ))}
            {workspace.space.map((draft) => (
              <Fragment key={draft.segment_id}>
              <div className="builder-library-entry">
                <button
                  className={`builder-outline-row builder-outline-row--segment${
                    isOpen(`segment:${draft.segment_id}`)
                      ? " builder-outline-row--selected"
                      : ""
                  }`}
                  onClick={() => openEditor({ kind: "segment", id: draft.segment_id })}
                  title={`Edit ${draft.display_name}`}
                >
                  <span className="builder-outline-name builder-outline-name--space">
                    <Icon name="orbit" size={12} />
                    {draft.display_name}
                  </span>
                  <span className="builder-outline-count">
                    {draft.planes * draft.slots_per_plane} sat
                  </span>
                </button>
                <span className="builder-library-actions">
                  {connectButton(draft.segment_id, draft.display_name)}
                </span>
              </div>
              {connectPicker(draft.segment_id)}
              </Fragment>
            ))}
            {workspace.ground_refs.map((placed) => (
              <Fragment key={placed.segment_id}>
              <div
                className={`builder-library-entry${
                  revealedSegment === placed.segment_id ? " builder-outline-row--revealed" : ""
                }`}
                data-segment-id={placed.segment_id}
              >
                <span className="builder-outline-name builder-outline-name--ground builder-outline-row--segment">
                  <Icon name="satellite-dish" size={12} />
                  {placed.label}
                </span>
                <span className="builder-library-actions">
                  {connectButton(placed.segment_id, placed.label)}
                  <InlineSelect
                    ariaLabel={`Scheduling for ${placed.label}`}
                    title="Scheduling intent — writes the full explicit block"
                    className="builder-ground-preset"
                    value={placed.scheduling_preset}
                    onChange={(v) =>
                      updateGroundRef(placed.segment_id, {
                        scheduling_preset: v as SchedulingPresetKey,
                      })
                    }
                    options={Object.entries(SCHEDULING_PRESETS).map(([key, preset]) => ({
                      value: key,
                      label: preset.label,
                    }))}
                  />
                  <IconButton
                    icon="pencil"
                    size={12}
                    label="Customize: fork into an editable draft"
                    onClick={() =>
                      void (async () => {
                        try {
                          const draft = await forkGroundSet(placed.ref);
                          replaceGroundRefWithDraft(placed.segment_id, draft);
                          openEditor({ kind: "ground", id: draft.segment_id });
                        } catch (e) {
                          setLibraryError(e instanceof Error ? e.message : String(e));
                        }
                      })()
                    }
                  />
                  <IconButton
                    icon="x"
                    size={12}
                    label="Remove from the session"
                    onClick={() => removeGroundRef(placed.segment_id)}
                  />
                </span>
              </div>
              {connectPicker(placed.segment_id)}
              </Fragment>
            ))}
            {workspace.ground.map((draft) => (
              <Fragment key={draft.segment_id}>
              <div className="builder-library-entry">
                <button
                  className={`builder-outline-row builder-outline-row--segment${
                    isOpen(`ground:${draft.segment_id}`)
                      ? " builder-outline-row--selected"
                      : ""
                  }`}
                  onClick={() => openEditor({ kind: "ground", id: draft.segment_id })}
                  title={`Edit ${draft.display_name}`}
                >
                  <span className="builder-outline-name builder-outline-name--ground">
                    <Icon name="satellite-dish" size={12} />
                    {draft.display_name}
                  </span>
                  <span className="builder-outline-count">
                    {count(draft.members.length, "site")}
                  </span>
                </button>
                <span className="builder-library-actions">
                  {connectButton(draft.segment_id, draft.display_name)}
                </span>
              </div>
              {connectPicker(draft.segment_id)}
              </Fragment>
            ))}
            {(workspace.links.length > 0 || placedSegments(workspace).length > 0) && (
              <div className="builder-outline-kind">Links</div>
            )}
            {workspace.links.map((rule) => (
              <button
                className={`builder-outline-row builder-outline-row--segment${
                  isOpen(`link:${rule.rule_id}`) ? " builder-outline-row--selected" : ""
                }`}
                key={rule.rule_id}
                onClick={() => openEditor({ kind: "link", id: rule.rule_id })}
                title={`Edit ${rule.label || rule.rule_id}`}
              >
                <span className="builder-outline-name">
                  <Icon name="spline" size={12} />
                  {rule.label || rule.rule_id}
                </span>
                <span className="builder-outline-count">
                  {rule.a.role}
                  {!rule.enabled && " · off"}
                </span>
              </button>
            ))}
            {linkWarnings(workspace).map((warning) => (
              <div className="builder-warning" key={warning}>
                {warning}
              </div>
            ))}
            {(workspace.routing_domains.length > 0 ||
              placedSegments(workspace).length > 0) && (
              <div className="builder-outline-kind">Routing</div>
            )}
            {workspace.routing_domains.map((domain) => (
              <button
                className={`builder-outline-row builder-outline-row--segment${
                  isOpen(`domain:${domain.domain_id}`)
                    ? " builder-outline-row--selected"
                    : ""
                }`}
                key={domain.domain_id}
                onClick={() => openEditor({ kind: "domain", id: domain.domain_id })}
                title={`Edit ${domain.label}`}
              >
                <span className="builder-outline-name">
                  <Icon name="network" size={12} />
                  {domain.label}
                </span>
                <span className="builder-outline-count">{domain.protocol}</span>
              </button>
            ))}
            {workspace.boundaries.map((boundary) => {
              const overRule = workspace.links.find(
                (rule) => rule.rule_id === boundary.over_rule_id,
              );
              return (
                <button
                  className={`builder-outline-row builder-outline-row--segment${
                    isOpen(`boundary:${boundary.boundary_id}`)
                      ? " builder-outline-row--selected"
                      : ""
                  }`}
                  key={boundary.boundary_id}
                  onClick={() => openEditor({ kind: "boundary", id: boundary.boundary_id })}
                  title="Edit boundary"
                >
                  <span className="builder-outline-name">
                    <Icon name="columns-2" size={12} />
                    over {overRule?.label || boundary.over_rule_id}
                  </span>
                  <span className="builder-outline-count">{boundary.adapter}</span>
                </button>
              );
            })}
            {placedSegments(workspace).length > 0 && (
              <div className="builder-preset-row">
                <Button
                  title="A protocol over member segments — seeds over everything"
                  onClick={() => {
                    const domain = defaultRoutingDomain(workspace);
                    addRoutingDomain(domain);
                    openEditor({ kind: "domain", id: domain.domain_id });
                    setFreshId(domain.domain_id);
                  }}
                >
                  + domain
                </Button>
                <Button
                  disabled={
                    workspace.routing_domains.length < 2 ||
                    !workspace.links.some(
                      (rule) => rule.a.role !== "access" && rule.b.role !== "access",
                    )
                  }
                  title="A controlled exchange over a fixed link rule between two domains"
                  onClick={() => {
                    const boundary = defaultBoundary(workspace);
                    const fixed = workspace.links.find(
                      (rule) => rule.a.role !== "access" && rule.b.role !== "access",
                    );
                    if (fixed) boundary.over_rule_id = fixed.rule_id;
                    addBoundary(boundary);
                    openEditor({ kind: "boundary", id: boundary.boundary_id });
                  }}
                >
                  + boundary
                </Button>
              </div>
            )}
            {routingWarnings(workspace).map((warning) => (
              <div className="builder-warning" key={warning}>
                {warning}
              </div>
            ))}
            <Button
              disabled={!defaultNodeRef}
              onClick={() => {
                if (!defaultNodeRef) return;
                const draft = newDraftConstellation(defaultNodeRef);
                ensureThenCreate(() => {
                  addDraft(draft);
                  openEditor({ kind: "segment", id: draft.segment_id });
                  setFreshId(draft.segment_id);
                }, "adding a constellation");
              }}
            >
              + Add constellation
            </Button>
            {dirtyWindows > 0 && (
              <div className="builder-zone-empty">
                {count(dirtyWindows, "window")} with unapplied edits — Apply to
                include them in the save
              </div>
            )}
            {saveState.kind === "deploying" && (
              <div className="builder-library-note">switching the cluster…</div>
            )}
            {saveState.kind === "deployed" && (
              <div className="builder-library-note" data-testid="deploy-note">
                switching to {saveState.name} — watch the Live view
              </div>
            )}
          </div>
        )}
        {libraryError && <div className="builder-warning">{libraryError}</div>}
        {sessionsError && (
          <div className="builder-zone-empty builder-status-item--error">
            session list unavailable: {sessionsError}
          </div>
        )}
        {world ? (
          <div data-testid="builder-segments">
            {bodyGroups.map(([body, group]) => {
              const spaceSegments = group.filter((s) => s.satellites > 0 || s.relays > 0);
              const groundSegments = group.filter((s) => s.satellites === 0 && s.relays === 0);
              const renderSegment = (seg: SegmentSummary, space: boolean) => {
                const expandable = !space && seg.grounds > 0;
                const expanded = expandedSegment === seg.segment_id;
                return (
                  <div key={seg.segment_id}>
                    <button
                      className="builder-outline-row builder-outline-row--segment"
                      onClick={() => {
                        actionsRef.current?.focusNode(seg.first_node_id);
                        if (expandable) {
                          setExpandedSegment(expanded ? null : seg.segment_id);
                        }
                      }}
                      title={`Fly to ${seg.display_name}`}
                    >
                      <span
                        className={`builder-outline-name builder-outline-name--${space ? "space" : "ground"}`}
                      >
                        <Icon name={space ? "orbit" : "satellite-dish"} size={12} />
                        {seg.display_name}
                        {expandable ? (expanded ? " ▾" : " ▸") : ""}
                      </span>
                      <span className="builder-outline-count">
                        {seg.satellites > 0 && `${seg.satellites} sat`}
                        {seg.satellites > 0 && seg.grounds + seg.relays > 0 && " · "}
                        {seg.grounds > 0 && `${seg.grounds} gs`}
                        {seg.relays > 0 && ` · ${seg.relays} relay`}
                      </span>
                    </button>
                    {expandable &&
                      expanded &&
                      world?.nodes
                        .filter((n) => n.segment_id === seg.segment_id)
                        .map((n) => (
                          <button
                            className={`builder-outline-row builder-outline-row--member${
                              selection?.id === n.node_id
                                ? " builder-outline-row--selected"
                                : ""
                            }`}
                            key={n.node_id}
                            onClick={() => {
                              setSelection({
                                type:
                                  n.kind === "satellite" ? "satellite" : "ground_station",
                                id: n.node_id,
                              });
                              openEditor({ kind: "node-view", nodeId: n.node_id });
                              actionsRef.current?.focusNode(n.node_id);
                            }}
                            title={`Select ${n.node_id}`}
                          >
                            <span>{n.local_node_id}</span>
                          </button>
                        ))}
                  </div>
                );
              };
              return (
                <div className="builder-outline-group" key={body}>
                  <button
                    className="builder-outline-body"
                    onClick={() => actionsRef.current?.focusBody(body)}
                    title={`Fly to ${body}`}
                  >
                    {body}
                  </button>
                  {spaceSegments.length > 0 && (
                    <div className="builder-outline-kind">Constellations</div>
                  )}
                  {spaceSegments.map((seg) => renderSegment(seg, true))}
                  {groundSegments.length > 0 && (
                    <div className="builder-outline-kind">Ground sites</div>
                  )}
                  {groundSegments.map((seg) => renderSegment(seg, false))}
                </div>
              );
            })}
          </div>
        ) : (
          <div className="builder-zone-empty">
            {workspace
              ? resolveError
                ? "nothing resolves to list — fix the refusal below"
                : "resolving…"
              : "No session loaded"}
          </div>
        )}
      </div>
      <div className="builder-canvas" data-testid="builder-canvas">
        {active && world && snapshot ? (
          <VisualizationErrorBoundary onError={() => {}}>
            <Scene
              snapshot={snapshot}
              ephemeris={world.ephemeris}
              colorMode={colorMode}
              globeMode={globeMode}
              referenceFrame={referenceFrame}
              playbackPaused={true}
              playbackState={null}
              showIslLinks={showIslLinks}
              showGroundLinks={showGroundLinks}
              showSatPaths={showSatPaths}
              showGroundTracks={showGroundTracks}
              regimeById={regimeById}
              showTrails={showTrails}
              selection={selection}
              onSelect={(next) => {
                setSelection(next);
                if (next) openEditor({ kind: "node-view", nodeId: next.id });
              }}
              actionsRef={actionsRef}
              liveExplain={false}
              worldLayers={<CandidateLines pairs={visiblePairs} />}
              beamFootprints={beamFootprints}
            />
          </VisualizationErrorBoundary>
        ) : snapshotError ? (
          <div className="builder-zone-empty">{snapshotError}</div>
        ) : workspace ? (
          <div className="builder-zone-empty">
            {resolveError || serializeError
              ? `The session does not resolve — the canvas returns when it does. ${truncateError(
                  resolveError?.error ?? serializeError ?? "",
                )}`
              : previewEmits
                ? "Resolving draft…"
                : hasDrafts
                  ? "Nothing to emit — the content is held out. See the rail."
                  : "Add a constellation to begin — the world renders as soon as the draft resolves"}
          </div>
        ) : (
          <div className="builder-start-card" data-testid="builder-start">
            <div className="builder-zone-title">Build a session from scratch</div>
            <ol className="builder-start-steps">
              <li>Add constellations — orbit presets seed values you then own</li>
              <li>Pick or author the hardware — nodes, terminals, your own physics</li>
              <li>Place ground sites and their networks</li>
              <li>Save — a resolvable session file, deployable like any other</li>
            </ol>
            <Button
              variant="primary"
              onClick={() => {
                displace(() => {
                  clear();
                  setSelection(null);
                  closeAllWindows();
                  setImportedFrom(null);
                  setImportIssues(null);
                  setSaveState({ kind: "idle" });
                  startNew("untitled-session");
                }, "starting a new session");
              }}
            >
              <Icon name="file-plus" size={13} /> New session
            </Button>
            <div className="builder-zone-empty">
              The toolbar above holds the session verbs — New, Open, Save, Deploy,
              Restore, Library. Every step round-trips through the real resolver;
              the YAML pane shows the session document live.
            </div>
          </div>
        )}
      </div>
      <div data-testid="builder-windows">
        {windows.map((win) => {
          const body = renderWindow(win.target);
          if (!body) return null;
          const buf = buffers[win.key];
          const buffered = ["session", "segment", "ground", "link", "domain", "boundary"].includes(
            win.target.kind,
          );
          return (
            <FloatingWindow
              key={win.key}
              title={buf?.dirty ? `${body.title} •` : body.title}
              onClose={() => closeWindow(win.key)}
              initial={{ x: win.x, y: win.y, w: 380, h: 560 }}
              minWidth={320}
              minHeight={240}
            >
              <div className="builder-window-body">{body.content}</div>
              {buffered && (
                <EditorApplyRow
                  dirty={buf?.dirty ?? false}
                  stale={staleKeys.has(win.key)}
                  onApply={() => applyBuffer(win.target)}
                  onOk={() => {
                    applyBuffer(win.target);
                    closeWindow(win.key);
                  }}
                  onDefaults={() => revertBuffer(win.key)}
                  onLoadCurrent={() => loadCurrentValues(win.target)}
                  onCancel={() => closeWindow(win.key)}
                />
              )}
            </FloatingWindow>
          );
        })}
      </div>
      <div className="builder-inspector" data-testid="builder-yaml">
        <div className="builder-zone-title">Session YAML</div>
        {documentYaml ? (
          <>
            <div className="builder-preset-row">
              <Button
                onClick={() => {
                  const p = navigator.clipboard?.writeText(documentYaml);
                  if (!p) {
                    setCopyState("failed");
                    return;
                  }
                  p.then(
                    () => setCopyState("copied"),
                    () => setCopyState("failed"),
                  );
                }}
              >
                {copyState === "copied"
                  ? "Copied"
                  : copyState === "failed"
                    ? "Copy failed"
                    : "Copy"}
              </Button>
              <Button
                onClick={() => downloadBlob(documentYaml, `${world?.session.name ?? "session"}.yaml`)}
              >
                Download
              </Button>
            </div>
            <pre className="builder-yaml-body">{documentYaml}</pre>
          </>
        ) : (
          <div className="builder-zone-empty">
            The session document appears here as soon as a draft resolves.
          </div>
        )}
      </div>
      {workspace &&
        ((wallTarget && !isOpen(wallTarget.key)) || findings.length > 0) && (
        <div className="builder-rail" data-testid="builder-rail">
          {wallTarget && !isOpen(wallTarget.key) && (
            <button
              className="builder-rail-chip builder-rail-chip--wall"
              title={resolveError?.error}
              onClick={() => openEditor(wallTarget.target)}
            >
              {(resolveError?.error ?? "").slice(0, 96)}
              {(resolveError?.error ?? "").length > 96 ? "…" : ""}
            </button>
          )}
          {findings.map((finding) => (
            <button
              key={finding.message}
              className="builder-rail-chip"
              disabled={finding.target === null}
              title={finding.target ? "Jump to the owning editor" : undefined}
              onClick={() => {
                const target = finding.target;
                if (!target) return;
                if (target.kind === "session") openEditor({ kind: "session" });
                else if (target.kind === "link") openEditor({ kind: "link", id: target.id });
                else if (target.kind === "ground") openEditor({ kind: "ground", id: target.id });
                else openEditor({ kind: "segment", id: target.id });
              }}
            >
              {finding.message}
            </button>
          ))}
        </div>
      )}
      <div className="builder-status" data-testid="builder-status">
        <span className="builder-mode-badge">Session Builder</span>
        {dirtyWindows > 0 && (
          <span className="builder-preview-chip" data-testid="builder-preview-chip">
            previewing {count(dirtyWindows, "window")} of unapplied edits
          </span>
        )}
        {error || serializeError ? (
          <span
            className="builder-status-item builder-status-item--error"
            title={error ?? serializeError ?? undefined}
          >
            {truncateError(error ?? serializeError ?? "")}
          </span>
        ) : snapshotError ? (
          <span className="builder-status-item builder-status-item--error">
            {snapshotError}
          </span>
        ) : world ? (
          <>
            <span className="builder-status-item" title={ruleNotes || undefined}>
              ✓ resolves: {count(world.nodes.length, "node")} (
              {count(satelliteCount, "satellite")} · {groundCount} ground) ·{" "}
              {count(segments.length, "segment")}
              {candidates && world.link_rules.length > 0 && (
                <>
                  {" "}
                  · {count(world.link_rules.length, "rule")} →{" "}
                  {count(candidates.pairs.length, "LOS candidate")}
                  {darkRules > 0 && ` (${darkRules} dark)`}
                </>
              )}
            </span>
            {satelliteCount === 0 && (
              <span className="builder-status-item builder-status-item--hint">
                no satellites yet — add one to run contact previews
              </span>
            )}
          </>
        ) : (
          <span className="builder-status-item">
            {workspace
              ? previewEmits
                ? "resolving draft…"
                : hasDrafts
                  ? "nothing to emit — content held out"
                  : "add a constellation to begin"
              : importPending
                ? importPending.file === runningSession?.file
                  ? `loading running session ${importPending.name}…`
                  : `loading session ${importPending.name}…`
                : runningSession
                  ? `running: ${runningSession.name} — not loaded`
                  : "no session loaded"}
          </span>
        )}
        {saveState.kind === "saved" && (
          <span className="builder-status-item">
            {deployGate.ok
              ? `saved as ${saveState.name}`
              : `saved as ${saveState.name} — ${deployGate.reason}`}
          </span>
        )}
        {saveState.kind === "failed" && (
          <span className="builder-status-item builder-status-item--error">
            save failed: {saveState.message}
          </span>
        )}
        {restoreError && (
          <span className="builder-status-item builder-status-item--error">
            {restoreError}
          </span>
        )}
      </div>
      {pendingDisplace && (
        <div
          data-testid="builder-backup-choice"
          style={{
            position: "absolute",
            inset: 0,
            background: "rgba(0, 0, 0, 0.5)",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            zIndex: 1000,
          }}
        >
          <div
            style={{
              background: "var(--bg-panel, #1a1a1a)",
              border: "1px solid var(--border, #333)",
              padding: "18px",
              borderRadius: "6px",
              maxWidth: "440px",
              display: "flex",
              flexDirection: "column",
              gap: "12px",
            }}
          >
            <div style={{ fontWeight: 600 }}>A backed-up draft would be replaced</div>
            <div style={{ fontSize: "13px", opacity: 0.85 }}>
              {pendingDisplace.label} would overwrite the draft currently held under
              Restore. Overwrite it, or cancel and keep both.
            </div>
            <div style={{ display: "flex", gap: "8px", justifyContent: "flex-end" }}>
              <Button
                variant="primary"
                onClick={() => {
                  const held = pendingDisplace;
                  setPendingDisplace(null);
                  // The overwrite choice: force the stash so the current draft
                  // replaces the backup, then run the held gesture.
                  stashAutosaveToBackup({ force: true });
                  held.proceed();
                }}
              >
                Overwrite the backup
              </Button>
              <Button onClick={() => setPendingDisplace(null)}>Cancel — keep both</Button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
