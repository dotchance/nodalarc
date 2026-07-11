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
 *  Authoring: client-side drafts + library refs compiled by the backend on
 *  every edit; the rendered world is always the resolver's expansion.
 */

import {
  Fragment,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ComponentProps,
  type MutableRefObject,
} from "react";
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
import {
  BuilderTransitionStatus,
  transitionIsTerminal,
  useBuilderTransitionOperation,
} from "./BuilderTransitionStatus";
import { builderSnapshotFromWorld, distinctGroundStationSites } from "./builderSnapshot";
import { CandidateLines } from "./CandidateLines";
import { computeCandidates } from "./candidates";
import { EditorApplyRow, Field, InlineSelect, PasteArea } from "./editorKit";
import {
  accessBeamElevationDeg,
  capabilitiesBySegment,
} from "./linkPhysics";
import { CatalogObjectView } from "./CatalogObjectView";
import { ConstellationEditor } from "./ConstellationEditor";
import { CustomizeChainEditor } from "./CustomizeChainEditor";
import { GroundEditor } from "./GroundEditor";
import { LibraryPanel } from "./LibraryPanel";
import { LinkRuleEditor } from "./LinkRuleEditor";
import { CatalogDraftEditorWindow } from "./CatalogDraftEditorWindow";
import { OpenSessionPicker } from "./OpenSessionPicker";
import { BoundaryEditor, RoutingDomainEditor } from "./RoutingEditor";
import { SessionEditor, timeRateSummary } from "./SessionEditor";
import {
  canDeploy,
  announceCatalogDraftSaved,
  claimLibraryReveal,
  claimOutlineReveal,
  exportSessionYaml,
  importSessionYamlFiles,
  readCatalogObject,
  requestOutlineReveal,
  useLibraryReveal,
  useLibraryRevision,
  useOutlineReveal,
  useBuilderBootstrap,
  useBuilderCatalog,
  useBuilderWorld,
} from "./useBuilderWorld";
import { downloadBlob } from "../ui/downloadBlob";
import {
  GraphicalControlTreeEditor,
  type BuilderControlMutation,
} from "./GraphicalControlTreeEditor";
import { workspaceForSave, useWorkspace } from "./useWorkspace";
import {
  useEditorWindows,
  targetKey,
  type EditorTarget,
  type SessionBuffer,
} from "./useEditorWindows";
import { wallTarget } from "./wallTarget";
import {
  createStructuredRecovery,
  clearCatalogDraftRecovery,
  clearStructuredRecoveryScope,
  getRecoveryTabBinding,
  hasStructuredRecovery,
  loadCatalogDraftRecovery,
  restoreStructuredRecovery,
  stashStructuredRecovery,
  writeStructuredAutosave,
  writeCatalogDraftRecovery,
  type CatalogDraftEditorRecovery,
  type RecoveryStorageScope,
} from "./structuredDraftRecovery";
import {
  BuilderApiError,
  createCatalogDraft,
  openCatalogDraft,
} from "./builderApiClient";
import { workspaceFromVisualDraft } from "./visualWorkspace";
import {
  emittedRuleId,
  linkWarnings,
  placedSegments,
  routingWarnings,
  type DraftBoundary,
  type DraftConstellation,
  type DraftGroundSet,
  type DraftLinkRule,
  type DraftRoutingDomain,
  type Workspace,
} from "./workspace";
import type {
  BuilderDeployVerdict,
  BuilderVisualAuthoringFacts,
  BuilderVisualSchedulingPreset,
  BuilderVisualDraftCommandRequest,
  BuilderVisualDraftCommandResult,
  CatalogComponentDraftEnvelope,
  CatalogComponentFamily,
  CatalogDocumentSummary,
  SessionRef,
} from "./generated/builderApi";
import type { BuilderWorld } from "./builderTypes";

interface BuilderViewProps {
  /** True only while the builder is the shown view. The builder stays mounted
   *  when hidden so drafts, windows, and buffers survive a Live<->Builder
   *  toggle; `active` gates every operator surface that ACTS (the Scene
   *  subtree per the singleton law, global key listeners, and reveal-open
   *  effects) so a hidden builder never mounts a second Scene or
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

function sameWorkspace(left: Workspace | null, right: Workspace | null): boolean {
  if (left === right) return true;
  if (!left || !right) return false;
  return JSON.stringify(left) === JSON.stringify(right);
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
  | {
      kind: "saved";
      name: string;
      sessionRef: string;
      sessionRevision: string;
      deployVerdict: BuilderDeployVerdict;
    }
  | {
      kind: "deploying";
      name: string;
      sessionRef: string;
      sessionRevision: string;
      deployVerdict: BuilderDeployVerdict;
    }
  | {
      kind: "deploy-accepted";
      name: string;
      sessionRef: string;
      sessionRevision: string;
      deployVerdict: BuilderDeployVerdict;
      operationId: string;
    }
  | { kind: "save-committed-unverified"; message: string; sessionRef: string }
  | { kind: "failed"; message: string };

/** Save is a small dialog, not a silent write. The name is buffered here and
 *  committed once on Save (— the Session window stays the only live
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
        {saveState.kind === "save-committed-unverified" && (
          <div className="builder-warning">
            save committed, but storage verification failed: {saveState.message}. Reopen the
            session before editing or saving again.
          </div>
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
        Saves a ref-composed session to your catalog with its exact dependency
        closure and backend deployment verdict.
      </div>
      <Field
        label="save as"
        value={name}
        onChange={(value) => {
          setName(value);
          setNameTouched(true);
        }}
      />
      <div className="builder-site-derived">
        VS-API validates and owns the saved session identifier.
      </div>
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
      {saveState.kind === "save-committed-unverified" && (
        <div className="builder-warning">
          save committed, but storage verification failed: {saveState.message}. Reopen the
          session before editing or saving again.
        </div>
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
    openedSession,
    visualDraft,
    yamlBuffer,
    writer,
    assemblyResult,
    world,
    loading,
    error,
    resolveError,
    compileResult,
    compileIssues,
    settledDocumentDigest,
    settledDependencySha256,
    createDraft,
    openSession,
    editYamlBuffer,
    applyYamlBuffer,
    useCanonicalYaml,
    applyWorkspace,
    customizeChain,
    runVisualCommand,
    mutateControls,
    prepareRetarget,
    compileCurrent,
    adoptRecoveredStructuredDraft,
    captureCoordinator,
    captureIsCurrent,
    saveSession,
    adoptCommittedRetarget,
    deploySession,
    refreshSessions,
    clear,
  } = useBuilderWorld();
  // Builder-local selection: inspect-only, never shared with the live view's
  // selection (two different worlds must not share a pointer).
  const [selection, setSelection] = useState<Selection | null>(null);
  // The authoring workspace: client-side drafts, backend-compiled on every
  // edit; the world on screen is always the resolver's expansion of it.
  const {
    workspace,
    currentWorkspace,
    openWorkspace,
    commitWorkspace,
    updateSession,
    undo,
    removeRefSegment,
    removeConstellation,
    updateConstellation,
    removeGroundRef,
    updateGroundDraft,
    removeGroundDraft,
    updateLinkRule,
    removeLinkRule,
    updateRoutingDomain,
    removeRoutingDomain,
    updateBoundary,
    removeBoundary,
  } = useWorkspace();
  const nodeCatalog = useBuilderCatalog("nodes");
  const terminalCatalog = useBuilderCatalog("terminals");
  const builderBootstrap = useBuilderBootstrap();
  const recoveryTabBinding = useMemo(() => getRecoveryTabBinding(), []);
  const recoveryScope = useMemo<RecoveryStorageScope | null>(() => {
    const binding = builderBootstrap.bootstrap?.authoring_context_binding;
    return binding
      ? { authoringContextBinding: binding, tabBinding: recoveryTabBinding }
      : null;
  }, [builderBootstrap.bootstrap?.authoring_context_binding, recoveryTabBinding]);
  const priorRecoveryScope = useRef<RecoveryStorageScope | null>(null);
  useEffect(() => {
    const previous = priorRecoveryScope.current;
    if (
      previous &&
      recoveryScope &&
      previous.authoringContextBinding !== recoveryScope.authoringContextBinding
    ) {
      clearStructuredRecoveryScope(previous);
    }
    priorRecoveryScope.current = recoveryScope;
  }, [recoveryScope]);
  const authoring: BuilderVisualAuthoringFacts | null =
    builderBootstrap.bootstrap?.authoring ?? null;
  const schedulingPresets = builderBootstrap.bootstrap?.scheduling_presets ?? [];
  const [schedulingSelections, setSchedulingSelections] = useState<
    Record<string, BuilderVisualSchedulingPreset | null>
  >({});

  const [pendingDisplace, setPendingDisplace] = useState<{
    label: string;
    proceed: () => void;
  } | null>(null);
  // Refresh the typed catalog when the hidden-but-mounted Builder regains
  // visibility. Catalog rows, not filesystem paths or operational session
  // guesses, are the session-open authority.
  const prevActiveRef = useRef(active);
  useEffect(() => {
    const wasActive = prevActiveRef.current;
    prevActiveRef.current = active;
    if (active && !wasActive) void refreshSessions();
  }, [active, refreshSessions]);
  // A save is never a dead end: when any editor saves to the library, the
  // Library window opens (or focuses) and the panel lands on the asset.
  // Claimed through the module-level retired-nonce registry: a remount
  // never replays the last save, and each consumer role retires its own.
  const libraryReveal = useLibraryReveal();
  useEffect(() => {
    // Gated on `active`: a hidden builder must not claim the reveal nonce
    // the shown view owns, nor pop a Library window in an invisible pane.
    if (!active) return;
    if (!claimLibraryReveal("opener", libraryReveal)) return;
    openEditor({ kind: "catalog" });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [active, libraryReveal]);
  // ref floor: a placed reference has no editor, so its Use scrolls its
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
  // The floating-editor windows and their buffered edits (useEditorWindows).
  const {
    windows,
    openEditor,
    closeWindow,
    closeAllWindows,
    isOpen,
    buffers,
    currentBufferMutationRevision,
    patchBuffer,
    revertBuffer,
    applyBuffer,
    previewWorkspace,
    dirtyWindows,
    staleKeys,
    staleList,
    loadCurrentValues,
    dropAppliedBuffers,
    restoreRecoveryState,
  } = useEditorWindows({
    workspace,
    updateSession,
    updateConstellation,
    updateGroundDraft,
    updateLinkRule,
    updateRoutingDomain,
    updateBoundary,
  });
  const [structuredRecoveryRevision, setStructuredRecoveryRevision] = useState(0);
  const [saveState, setSaveState] = useState<SaveState>({ kind: "idle" });
  const currentStructuredRecovery = () =>
    recoveryScope &&
    createStructuredRecovery({
      authoringContextBinding: recoveryScope.authoringContextBinding,
      workspace,
      visualDraft,
      yaml: {
        text: yamlBuffer.text,
        appliedText: yamlBuffer.appliedText,
        generation: yamlBuffer.generation,
        canonicalizationRequired: yamlBuffer.canonicalizationRequired,
        canonicalizationAccepted: yamlBuffer.canonicalizationAccepted,
        issues: yamlBuffer.issues,
      },
      windows,
      buffers,
    });
  useEffect(() => {
    const recovery = currentStructuredRecovery();
    if (!recovery || !recoveryScope) return;
    const timer = setTimeout(() => {
      if (writeStructuredAutosave(recovery, recoveryScope)) {
        setStructuredRecoveryRevision((revision) => revision + 1);
      }
    }, 800);
    return () => clearTimeout(timer);
    // The exact full envelope, applied workspace, and save-relevant buffers are
    // the recovery identity; no flattened or fresh-draft reconstruction occurs.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [workspace, visualDraft, yamlBuffer, windows, buffers, recoveryScope]);
  const hasStructuredAutosave = () => {
    void structuredRecoveryRevision;
    return recoveryScope ? hasStructuredRecovery("autosave", recoveryScope) : false;
  };
  const hasStructuredBackup = () => {
    void structuredRecoveryRevision;
    return recoveryScope ? hasStructuredRecovery("backup", recoveryScope) : false;
  };
  const stashCurrentStructuredRecovery = (options?: { force?: boolean }) => {
    if (!recoveryScope) return "skipped" as const;
    const outcome = stashStructuredRecovery(
      currentStructuredRecovery(),
      recoveryScope,
      options,
    );
    if (outcome === "stashed") {
      setStructuredRecoveryRevision((revision) => revision + 1);
    }
    return outcome;
  };
  const restoreCurrentStructuredRecovery = () => {
    if (!recoveryScope) {
      setRestoreError("Builder recovery is unavailable until backend context loads");
      return;
    }
    const fromBackup = hasStructuredBackup();
    const result = restoreStructuredRecovery(
      fromBackup ? "backup" : "autosave",
      recoveryScope,
      { consume: fromBackup },
    );
    if (!result.ok) {
      setRestoreError(result.reason);
      return;
    }
    closeAllWindows();
    openWorkspace(result.recovery.workspace);
    restoreRecoveryState(result.recovery.editor);
    setSelection(null);
    setSaveState({ kind: "idle" });
    setRestoreError(null);
    adoptRecoveredStructuredDraft(result.recovery.visualDraft, {
      ...result.recovery.yaml,
      dirty: result.recovery.yaml.text !== result.recovery.yaml.appliedText,
      applied: result.recovery.yaml.issues.length === 0,
    });
    if (
      result.recovery.yaml.issues.length === 0 &&
      result.recovery.yaml.text === result.recovery.yaml.appliedText
    ) {
      void compileCurrent();
    }
    setStructuredRecoveryRevision((revision) => revision + 1);
  };
  /** Run a displacing gesture only after preserving the exact current draft. */
  const displace = (proceed: () => void, label: string) => {
    if (stashCurrentStructuredRecovery() === "refused") {
      setPendingDisplace({ label, proceed });
      return;
    }
    proceed();
  };
  const ensureThenCreate = (create: () => void, label: string) => {
    if (workspace) create();
    else
      displace(() => {
        void createDraft({})
          .then((draft) => {
            openWorkspace(workspaceFromVisualDraft(draft));
            create();
          })
          .catch((cause) =>
            setLibraryError(cause instanceof Error ? cause.message : String(cause)),
          );
      }, label);
  };
  // The wall's owning editor target (wallTarget). Matched against the
  // preview overlay — the refused document was serialized from it, so a dirty
  // rename must be matched by the dirty draft, not the applied state.
  const wall = wallTarget(previewWorkspace() ?? workspace, resolveError);
  /** Inline wall text for one open editor window (null = not this window's). */
  const wallFor = (target: EditorTarget): string | null =>
    wall && targetKey(target) === wall.key ? (resolveError?.error ?? null) : null;
  // THE edit→compile loop — the only caller. Submits applied state plus
  // dirty working copies so the canvas moves while you edit; Apply/Cancel
  // land here too (buffers change) and re-resolve the applied truth. The
  // library revision is a dependency on purpose: a user-catalog mutation
  // changes its dependency closure, so both settled digests must recompile.
  const libraryRevision = useLibraryRevision();
  // A Restore that finds no payload (missing/corrupt) surfaces here instead of
  // silently doing nothing; the current workspace and its world stand.
  const [restoreError, setRestoreError] = useState<string | null>(null);
  const [openSessionError, setOpenSessionError] = useState<string | null>(null);
  const hasDrafts =
    !!workspace &&
    workspace.space.length +
      workspace.space_refs.length +
      workspace.ground.length +
      workspace.ground_refs.length >
      0;
  const yamlBlocksGui =
    yamlBuffer.dirty ||
    !yamlBuffer.applied ||
    (yamlBuffer.canonicalizationRequired && !yamlBuffer.canonicalizationAccepted);
  useEffect(() => {
    if (saveState.kind === "saving" || !workspace || !visualDraft || yamlBlocksGui) return;
    const preview = previewWorkspace();
    if (!preview) return;
    const projected = visualDraft.authoring_workspace ?? visualDraft.applied_workspace;
    if (sameWorkspace(preview, projected as Workspace | null)) return;
    const bufferRevision = currentBufferMutationRevision();
    const appliedWorkspace = currentWorkspace();
    const timer = setTimeout(() => {
      void applyWorkspace(preview)
        .then((result) => {
          if (
            currentBufferMutationRevision() === bufferRevision &&
            currentWorkspace() === appliedWorkspace &&
            dirtyWindows === 0
          ) {
            openWorkspace(workspaceFromVisualDraft(result.visual_draft));
          }
        })
        .catch(() => undefined);
    }, 300);
    return () => clearTimeout(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [
    workspace,
    buffers,
    libraryRevision,
    visualDraft,
    yamlBlocksGui,
    saveState.kind,
  ]);

  useEffect(() => {
    if (!visualDraft || !yamlBuffer.dirty || dirtyWindows > 0) return;
    const generation = yamlBuffer.generation;
    const timer = setTimeout(() => {
      void applyYamlBuffer(generation).catch(() => undefined);
    }, 500);
    return () => clearTimeout(timer);
  }, [applyYamlBuffer, dirtyWindows, visualDraft, yamlBuffer.dirty, yamlBuffer.generation]);

  // Trust mechanics: Ctrl/Cmd+Z undoes the last workspace mutation unless
  // the user is typing in a field (native input undo wins there). Gated on
  // `active`: a hidden-but-mounted builder must never intercept a Ctrl+Z
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
  // the object a create gesture just made — its editor focuses the
  // name once.
  const [freshId, setFreshId] = useState<string | null>(null);
  const acceptedOperationId =
    saveState.kind === "deploy-accepted" ? saveState.operationId : null;
  const { operation: transitionOperation, error: transitionPollError } =
    useBuilderTransitionOperation(acceptedOperationId);
  const transitionInFlight =
    saveState.kind === "deploy-accepted" &&
    (transitionOperation === null || !transitionIsTerminal(transitionOperation.state));
  const [copyState, setCopyState] = useState<"idle" | "copied" | "failed">("idle");
  // the create-focus marker is one-shot. Drop it once the window it names
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
  // the Copy result is transient feedback, cleared after a beat so the
  // control returns to its resting label.
  useEffect(() => {
    if (copyState === "idle") return;
    const t = setTimeout(() => setCopyState("idle"), 2000);
    return () => clearTimeout(t);
  }, [copyState]);
  const findings = useMemo(() => {
    if (!workspace) return [];
    return (assemblyResult?.assembly_issues ?? []).map((issue) => {
      const match = /^workspace\.(space|ground|links|routing_domains|boundaries)\.(\d+)/.exec(
        issue.draft_path ?? "",
      );
      if (!match) return { message: issue.message, target: null as EditorTarget | null };
      const index = Number(match[2]);
      const target =
        match[1] === "space"
          ? workspace.space[index]
            ? ({ kind: "segment", id: workspace.space[index]!.segment_id } as const)
            : null
          : match[1] === "ground"
            ? workspace.ground[index]
              ? ({ kind: "ground", id: workspace.ground[index]!.segment_id } as const)
              : null
            : match[1] === "links"
              ? workspace.links[index]
                ? ({ kind: "link", id: workspace.links[index]!.rule_id } as const)
                : null
              : match[1] === "routing_domains"
                ? workspace.routing_domains[index]
                  ? ({ kind: "domain", id: workspace.routing_domains[index]!.domain_id } as const)
                  : null
                : workspace.boundaries[index]
                  ? ({ kind: "boundary", id: workspace.boundaries[index]!.boundary_id } as const)
                  : null;
      return { message: issue.message, target };
    });
  }, [assemblyResult?.assembly_issues, workspace]);
  /** One save path for both dialog actions: submit the exact draft, then adopt it. */
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
    let preparedRetargetForSave: Awaited<ReturnType<typeof prepareRetarget>> | null = null;
    let saveCapture: ReturnType<typeof captureCoordinator> | null = null;
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
      if (yamlBlocksGui) {
        throw new Error("apply valid canonical YAML before saving the graphical session");
      }
      if (nameTouched && !name.trim()) {
        throw new Error("session name is required");
      }
      const currentTargetName = visualDraft?.target_ref
        .split("/")
        .pop()
        ?.replace(/\.ya?ml$/, "");
      if (!currentTargetName) throw new Error("the current session target is unavailable");
      const retargetRequired = next.session_name !== currentTargetName;
      const workspaceForCurrentTarget = retargetRequired
        ? { ...next, session_name: currentTargetName }
        : next;
      const currentProjection = visualDraft?.authoring_workspace ?? visualDraft?.applied_workspace;
      let assembled =
        assemblyResult && sameWorkspace(
          workspaceForCurrentTarget,
          currentProjection as Workspace | null,
        )
          ? assemblyResult
          : await applyWorkspace(workspaceForCurrentTarget);
      const preparedRetarget = retargetRequired
        ? await prepareRetarget(`user:sessions/${next.session_name}.yaml`)
        : null;
      preparedRetargetForSave = preparedRetarget;
      if (preparedRetarget) assembled = preparedRetarget;
      if (!assembled.compile_result.save_verdict.allowed) {
        throw new Error(
          assembled.compile_result.save_verdict.blockers?.[0]?.message ??
            "the backend blocked this visual draft from saving",
        );
      }
      const capture = captureCoordinator();
      saveCapture = capture;
      const saved = await saveSession(assembled.save_request, capture);
      const result = saved.result;
      if (!saved.reopenedDraft) {
        if (preparedRetarget && captureIsCurrent(capture)) {
          adoptCommittedRetarget(preparedRetarget, {
            ref: result.session.ref,
            revision: result.session.revision,
            canonicalYaml: result.session.canonical_yaml,
          });
        }
        if (next !== workspace) {
          commitWorkspace(next, "save-committed-unverified");
        }
        if (applyAll) dropAppliedBuffers(appliedBuffers);
        setSaveState({
          kind: "save-committed-unverified",
          message:
            saved.postCommitError ??
            "the session was saved, but the persisted revision could not be reopened",
          sessionRef: result.session.ref,
        });
        return;
      }
      if (next !== workspace) {
        commitWorkspace(next, applyAll ? "apply-all-save" : "save-rename");
      }
      if (applyAll) {
        dropAppliedBuffers(appliedBuffers);
      }
      openWorkspace(workspaceFromVisualDraft(saved.reopenedDraft));
      setSaveState({
        kind: "saved",
        name: next.session_name,
        sessionRef: result.session.ref,
        sessionRevision: result.session.revision,
        deployVerdict: result.deploy_verdict,
      });
    } catch (e) {
      const detail = e instanceof BuilderApiError ? e.detail : null;
      if (
        detail &&
        typeof detail === "object" &&
        (detail as Record<string, unknown>).repository_committed === true
      ) {
        const committedRef = String(
          (detail as Record<string, unknown>).target_ref ?? "saved session",
        );
        if (
          preparedRetargetForSave &&
          saveCapture &&
          captureIsCurrent(saveCapture)
        ) {
          adoptCommittedRetarget(preparedRetargetForSave, { ref: committedRef });
          openWorkspace(workspaceFromVisualDraft(preparedRetargetForSave.visual_draft));
        }
        setSaveState({
          kind: "save-committed-unverified",
          message: e instanceof Error ? e.message : String(e),
          sessionRef: committedRef,
        });
      } else {
        setSaveState({
          kind: "failed",
          message: e instanceof Error ? e.message : String(e),
        });
      }
    }
  };
  // Deployment requires the saved backend verdict and exact settled digests.
  const deployGate = canDeploy({
    savedVerdict:
      saveState.kind === "saved" ||
      saveState.kind === "deploying" ||
      saveState.kind === "deploy-accepted"
        ? saveState.deployVerdict
        : null,
    settledDocumentDigest,
    settledDependencyDigest: settledDependencySha256,
    dirtyWindowCount: dirtyWindows,
  });
  // Standalone component authoring (Your library) — independent of sessions.
  const [catalogDraftRecovery, setCatalogDraftRecovery] =
    useState<CatalogDraftEditorRecovery | null>(null);
  const [catalogDraft, setCatalogDraft] = useState<CatalogComponentDraftEnvelope | null>(null);
  const [libraryError, setLibraryError] = useState<string | null>(null);
  const visualCommandInFlight = useRef(false);

  const schedulingSelectionKey = (
    segmentId: string,
    memberId?: string,
    targetRef = visualDraft?.target_ref,
  ) => `${targetRef ?? "no-draft"}|${segmentId}${memberId ? `/${memberId}` : ""}`;

  const rememberSchedulingSelection = (
    command: BuilderVisualDraftCommandRequest["command"],
    result: BuilderVisualDraftCommandResult,
  ) => {
    const preset = result.scheduling_preset;
    if (preset === undefined) return;
    let key: string | null = null;
    if (command.operation === "set_scheduling_preset") {
      key = schedulingSelectionKey(
        command.segment_id,
        command.member_id ?? undefined,
        result.draft.target_ref,
      );
    } else if (
      command.operation === "add_ground" ||
      command.operation === "place_ground_reference"
    ) {
      key = schedulingSelectionKey(result.affected_id, undefined, result.draft.target_ref);
    } else if (
      command.operation === "add_ground_site_reference" &&
      command.segment_id === undefined
    ) {
      const ground = result.draft.authoring_workspace?.ground?.find((candidate) =>
        candidate.members?.some((member) => member.member_id === result.affected_id),
      );
      if (ground?.segment_id) {
        key = schedulingSelectionKey(ground.segment_id, undefined, result.draft.target_ref);
      }
    }
    if (!key) return;
    setSchedulingSelections((current) => ({
      ...current,
      [key]: preset,
    }));
  };

  const executeVisualCommand = async (
    command: BuilderVisualDraftCommandRequest["command"],
    sourceWorkspace: Workspace,
    appliedWorkspace: Workspace,
    bufferRevision?: number,
  ): Promise<BuilderVisualDraftCommandResult> => {
    if (visualCommandInFlight.current) {
      throw new Error("another visual command is still being applied");
    }
    visualCommandInFlight.current = true;
    try {
      const projected = visualDraft?.authoring_workspace ?? visualDraft?.applied_workspace;
      if (!sameWorkspace(sourceWorkspace, projected as Workspace | null)) {
        await applyWorkspace(sourceWorkspace);
      }
      const result = await runVisualCommand(command);
      if (currentWorkspace() !== appliedWorkspace) {
        throw new Error("the session changed while the visual command was running; try again");
      }
      if (
        bufferRevision !== undefined &&
        currentBufferMutationRevision() !== bufferRevision
      ) {
        throw new Error("an editor changed while the visual command was running; try again");
      }
      return result;
    } finally {
      visualCommandInFlight.current = false;
    }
  };

  const applyWorkspaceCommand = async (
    command: BuilderVisualDraftCommandRequest["command"],
  ): Promise<BuilderVisualDraftCommandResult> => {
    const applied = currentWorkspace();
    if (!applied) throw new Error("there is no structured workspace to edit");
    const result = await executeVisualCommand(command, applied, applied);
    commitWorkspace(
      workspaceFromVisualDraft(result.draft),
      `backend visual command: ${result.operation}`,
    );
    rememberSchedulingSelection(command, result);
    return result;
  };

  const reopenRecoveredCatalogDraft = useRef(false);
  useEffect(() => {
    if (!recoveryScope) {
      setCatalogDraftRecovery(null);
      setCatalogDraft(null);
      return;
    }
    const recovered = loadCatalogDraftRecovery(recoveryScope);
    const recovery = recovered.ok ? recovered.recovery : null;
    setCatalogDraftRecovery(recovery);
    setCatalogDraft(recovery?.draft ?? null);
    reopenRecoveredCatalogDraft.current = recovery !== null;
  }, [recoveryScope]);
  useEffect(() => {
    if (!active || !reopenRecoveredCatalogDraft.current || !catalogDraftRecovery) return;
    reopenRecoveredCatalogDraft.current = false;
    openEditor({ kind: "library" });
  }, [active, catalogDraftRecovery, openEditor]);
  const handleCatalogDraftRecovery = useCallback(
    (recovery: CatalogDraftEditorRecovery | null) => {
      setCatalogDraftRecovery(recovery);
      if (recovery) {
        if (!recoveryScope) return;
        setCatalogDraft(recovery.draft);
        writeCatalogDraftRecovery(recovery, recoveryScope);
      } else if (recoveryScope) {
        clearCatalogDraftRecovery(recoveryScope);
      }
    },
    [recoveryScope],
  );
  const discardCatalogDraft = useCallback(() => {
    if (recoveryScope) clearCatalogDraftRecovery(recoveryScope);
    setCatalogDraftRecovery(null);
    setCatalogDraft(null);
    closeWindow("library");
  }, [closeWindow, recoveryScope]);

  // The Library's per-entry gestures. USE places a catalog reference in an
  // editable session; CUSTOMIZE opens a full backend draft at an explicit
  // user: target and persists it only when the backend save succeeds. Starting
  // an editable workspace from a typed read-only open clears that preview.
  const clearReadOnlyWorldBeforeCreate = () => {
    if (world && !workspace) clear();
  };

  const handleLibraryUse = (entry: CatalogDocumentSummary) => {
    setLibraryError(null);
    const entryId = (entry.ref.split("/").pop() ?? entry.ref).replace(/\.ya?ml$/, "");
    const label = `using ${entry.display_name ?? entryId}`;
    const name = entry.display_name ?? entryId;
    if (entry.family === "constellations" || entry.family === "space-node-sets") {
      // REF family: no editor exists for a placed reference (L6) — reveal its
      // outline row so the placement is visible (ref floor).
      ensureThenCreate(() => {
        clearReadOnlyWorldBeforeCreate();
        void applyWorkspaceCommand({
          operation: "place_space_reference",
          source_ref: entry.ref,
        })
          .then((result) => requestOutlineReveal(result.affected_id))
          .catch((cause) =>
            setLibraryError(cause instanceof Error ? cause.message : String(cause)),
          );
      }, label);
    } else if (entry.family === "site-sets") {
      ensureThenCreate(() => {
        clearReadOnlyWorldBeforeCreate();
        void applyWorkspaceCommand({
          operation: "place_ground_reference",
          site_set_ref: entry.ref,
        })
          .then((result) => requestOutlineReveal(result.affected_id))
          .catch((cause) =>
            setLibraryError(cause instanceof Error ? cause.message : String(cause)),
          );
      }, label);
    } else if (entry.family === "nodes") {
      // DRAFT family: open the created segment's editor, focused for rename.
      ensureThenCreate(() => {
        clearReadOnlyWorldBeforeCreate();
        if (!authoring) {
          setLibraryError("Builder authoring facts are unavailable");
          return;
        }
        void applyWorkspaceCommand({
          operation: "add_generated_space",
          phasing_mode: authoring.default_phasing_mode,
          node_ref: entry.ref,
        })
          .then((result) => {
            openEditor({ kind: "segment", id: result.affected_id });
            setFreshId(result.affected_id);
          })
          .catch((cause) =>
            setLibraryError(cause instanceof Error ? cause.message : String(cause)),
          );
      }, label);
    } else if (entry.family === "sites") {
      ensureThenCreate(() => {
        clearReadOnlyWorldBeforeCreate();
        void (async () => {
          const currentGround = currentWorkspace()?.ground ?? [];
          const ground = currentGround[currentGround.length - 1] ?? null;
          const result = await applyWorkspaceCommand({
            operation: "add_ground_site_reference",
            ...(ground ? { segment_id: ground.segment_id } : {}),
            site_ref: entry.ref,
          });
          const receivingGround = workspaceFromVisualDraft(result.draft).ground.find(
            (candidate) =>
              candidate.members.some((member) => member.member_id === result.affected_id),
          );
          if (!receivingGround) {
            throw new Error("backend site command returned no receiving ground segment");
          }
          openEditor({ kind: "ground", id: receivingGround.segment_id });
          if (!ground) setFreshId(receivingGround.segment_id);
        })().catch((cause) =>
          setLibraryError(cause instanceof Error ? cause.message : String(cause)),
        );
      }, label);
    } else {
      setLibraryError(`cannot use "${name}": unsupported family "${entry.family}"`);
    }
  };

  const handleLibraryCustomize = async (entry: CatalogDocumentSummary, targetRef: string) => {
    setLibraryError(null);
    if (catalogDraftRecovery) {
      openEditor({ kind: "library" });
      if (catalogDraftRecovery.draft.target_ref === targetRef) return;
      const message = `Finish or discard ${catalogDraftRecovery.draft.target_ref} before opening another component draft.`;
      setLibraryError(message);
      throw new Error(message);
    }
    try {
      const draft = await openCatalogDraft({
        source_ref: entry.ref,
        target_ref: targetRef,
      });
      setCatalogDraft(draft);
      openEditor({ kind: "library" });
    } catch (e) {
      const message = e instanceof Error ? e.message : String(e);
      setLibraryError(
        entry.namespace === "nodalarc" && message.includes("already exists")
          ? `${message}. Choose a different user: id for this shipped customization.`
          : message,
      );
      throw e;
    }
  };

  const handleLibraryInspect = async (entry: CatalogDocumentSummary) => {
    setLibraryError(null);
    try {
      const { document } = await readCatalogObject(entry.ref);
      openEditor({ kind: "inspect", ref: entry.ref, document });
    } catch (e) {
      setLibraryError(e instanceof Error ? e.message : String(e));
    }
  };

  const handleLibraryNew = async (family: string, objectId: string) => {
    setLibraryError(null);
    const targetRef = `user:${family}/${objectId}.yaml`;
    if (catalogDraftRecovery) {
      openEditor({ kind: "library" });
      if (catalogDraftRecovery.draft.target_ref === targetRef) return;
      const message = `Finish or discard ${catalogDraftRecovery.draft.target_ref} before opening another component draft.`;
      setLibraryError(message);
      throw new Error(message);
    }
    try {
      const draft = await createCatalogDraft({
        family: family as CatalogComponentFamily,
        object_id: objectId,
      });
      setCatalogDraft(draft);
      openEditor({ kind: "library" });
    } catch (cause) {
      setLibraryError(cause instanceof Error ? cause.message : String(cause));
      throw cause;
    }
  };
  // The connect gesture: both endpoints known before the rule
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
    setLibraryError(null);
    void applyWorkspaceCommand({
      operation: "connect_segments",
      from_segment_id: fromSegmentId,
      to_segment_id: targetSegmentId,
    })
      .then((result) => {
        openEditor({ kind: "link", id: result.affected_id });
        setFreshId(result.affected_id);
      })
      .catch((cause) =>
        setLibraryError(cause instanceof Error ? cause.message : String(cause)),
      );
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

  /** The body of one floating editor window. Null = the object no longer
   *  exists (undo, removal); the window simply doesn't render. */
  function renderWindow(
    target: EditorTarget,
  ): { title: string; content: React.ReactNode } | null {
    const standalone = [
      "inspect",
      "node-view",
      "open-session",
      "source-yaml",
      "customize-chain",
      "catalog",
      "library",
    ].includes(target.kind);
    if (!workspace && !standalone) return null;
    if (
      !authoring &&
      ["segment", "ground", "link", "domain", "boundary", "library"].includes(
        target.kind,
      )
    ) {
      return {
        title: "Builder unavailable",
        content: (
          <div className="builder-warning">
            Backend authoring facts are unavailable. Retry the Builder bootstrap request.
          </div>
        ),
      };
    }
    switch (target.kind) {
      case "session": {
        if (!workspace) return null;
        const key = targetKey(target);
        const sessionPick: SessionBuffer = {
          session_name: workspace.session_name,
          start_time: workspace.start_time,
          step_seconds: workspace.step_seconds,
          compression: workspace.compression,
          max_pairs_per_rule: workspace.max_pairs_per_rule,
          max_pairs_per_tick: workspace.max_pairs_per_tick,
        };
        const buf = buffers[key];
        const view = buf ? { ...workspace, ...(buf.draft as SessionBuffer) } : workspace;
        return {
          title: `Session · ${view.session_name}`,
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
        const applySpaceEditorCommand = async (
          command: BuilderVisualDraftCommandRequest["command"],
        ) => {
          const appliedWorkspace = currentWorkspace();
          const sourceWorkspace = previewWorkspace();
          if (!appliedWorkspace || !sourceWorkspace) {
            throw new Error("there is no structured workspace to edit");
          }
          const bufferRevision = currentBufferMutationRevision();
          const result = await executeVisualCommand(
            command,
            sourceWorkspace,
            appliedWorkspace,
            bufferRevision,
          );
          const updated = workspaceFromVisualDraft(result.draft).space.find(
            (candidate) => candidate.segment_id === draft.segment_id,
          );
          if (!updated) throw new Error("VS-API returned no updated space segment");
          patchBuffer(key, applied, () => updated);
        };
        const setPopulation: ComponentProps<typeof ConstellationEditor>["onSetPopulation"] = async (
          change,
        ) => {
          await applySpaceEditorCommand({
            operation: "set_space_population",
            segment_id: draft.segment_id,
            ...change,
          });
        };
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
              authoring={authoring!}
              key={draft.segment_id}
              autoFocusName={freshId === draft.segment_id}
              // the dwell readout reads the PREVIEW (applied + dirty
              // overlays) so a dirty session buffer's start_time is reflected,
              // never a stale applied value.
              workspace={previewWorkspace() ?? workspace}
              onOpenRule={openRule}
              onConnect={(other) => connect(draft.segment_id, other)}
              draft={draft}
              onUpdate={(update) => patchBuffer(key, applied, update)}
              onSetPopulation={setPopulation}
              onAuthorInlineNode={() =>
                applySpaceEditorCommand({
                  operation: "author_inline_space_node",
                  segment_id: draft.segment_id,
                })
              }
              onAddNodeTerminal={(terminalRef, role) =>
                applySpaceEditorCommand({
                  operation: "add_or_increment_node_terminal",
                  segment_id: draft.segment_id,
                  terminal_ref: terminalRef,
                  role,
                })
              }
              onSetNodeTerminalRole={(mountId, role) =>
                applySpaceEditorCommand({
                  operation: "set_node_terminal_role",
                  segment_id: draft.segment_id,
                  mount_id: mountId,
                  role,
                })
              }
              onAddNodeEthernet={() =>
                applySpaceEditorCommand({
                  operation: "add_node_ethernet_port",
                  segment_id: draft.segment_id,
                })
              }
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
        const applyGroundEditorCommand = async (
          command: BuilderVisualDraftCommandRequest["command"],
        ) => {
          const appliedWorkspace = currentWorkspace();
          const sourceWorkspace = previewWorkspace();
          if (!appliedWorkspace || !sourceWorkspace) {
            throw new Error("there is no structured workspace to edit");
          }
          const bufferRevision = currentBufferMutationRevision();
          const result = await executeVisualCommand(
            command,
            sourceWorkspace,
            appliedWorkspace,
            bufferRevision,
          );
          const updated = workspaceFromVisualDraft(result.draft).ground.find(
            (candidate) => candidate.segment_id === draft.segment_id,
          );
          if (!updated) throw new Error("VS-API returned no updated ground segment");
          patchBuffer(key, applied, () => updated);
        };
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
              authoring={authoring!}
              key={draft.segment_id}
              autoFocusName={freshId === draft.segment_id}
              workspace={workspace}
              onOpenRule={openRule}
              onConnect={(other) => connect(draft.segment_id, other)}
              schedulingPresets={schedulingPresets}
              selectedSchedulingPreset={
                schedulingSelections[schedulingSelectionKey(draft.segment_id)] ?? null
              }
              memberSchedulingPreset={(memberId) =>
                schedulingSelections[
                  schedulingSelectionKey(draft.segment_id, memberId)
                ] ?? null
              }
              onMintSites={async (sites) => {
                const appliedWorkspace = currentWorkspace();
                const sourceWorkspace = previewWorkspace();
                if (!appliedWorkspace || !sourceWorkspace) {
                  throw new Error("there is no structured workspace to edit");
                }
                const bufferRevision = currentBufferMutationRevision();
                const result = await executeVisualCommand(
                  {
                    operation: "mint_ground_members",
                    segment_id: draft.segment_id,
                    sites,
                  },
                  sourceWorkspace,
                  appliedWorkspace,
                  bufferRevision,
                );
                const updated = workspaceFromVisualDraft(result.draft).ground.find(
                  (candidate) => candidate.segment_id === draft.segment_id,
                );
                if (!updated) throw new Error("VS-API returned no updated ground segment");
                patchBuffer(key, applied, () => updated);
              }}
              onAddSiteReference={(ref) =>
                applyGroundEditorCommand({
                  operation: "add_ground_site_reference",
                  segment_id: draft.segment_id,
                  site_ref: ref,
                })
              }
              onSetStampNodeModel={(ref) =>
                applyGroundEditorCommand({
                  operation: "set_ground_stamp_node_model",
                  segment_id: draft.segment_id,
                  node_ref: ref,
                })
              }
              onSetSiteNodeModel={(memberId, nodeId, ref) =>
                applyGroundEditorCommand({
                  operation: "set_ground_site_node_model",
                  segment_id: draft.segment_id,
                  member_id: memberId,
                  node_id: nodeId,
                  node_ref: ref,
                })
              }
              onAddSiteNode={(memberId) =>
                applyGroundEditorCommand({
                  operation: "add_ground_site_node",
                  segment_id: draft.segment_id,
                  member_id: memberId,
                })
              }
              onSchedulingPreset={async (preset, memberId) => {
                const appliedWorkspace = currentWorkspace();
                const sourceWorkspace = previewWorkspace();
                if (!appliedWorkspace || !sourceWorkspace) {
                  throw new Error("there is no structured workspace to edit");
                }
                const bufferRevision = currentBufferMutationRevision();
                const result = await executeVisualCommand(
                  {
                    operation: "set_scheduling_preset",
                    segment_id: draft.segment_id,
                    preset,
                    ...(memberId ? { member_id: memberId } : {}),
                  },
                  sourceWorkspace,
                  appliedWorkspace,
                  bufferRevision,
                );
                const updated = workspaceFromVisualDraft(result.draft).ground.find(
                  (candidate) => candidate.segment_id === draft.segment_id,
                );
                if (!updated) throw new Error("VS-API returned no updated ground segment");
                patchBuffer(key, applied, () => updated);
                rememberSchedulingSelection(
                  {
                    operation: "set_scheduling_preset",
                    segment_id: draft.segment_id,
                    preset,
                    ...(memberId ? { member_id: memberId } : {}),
                  },
                  result,
                );
              }}
              draft={draft}
              onUpdate={(update) => patchBuffer(key, applied, update)}
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
              authoring={authoring!}
              key={rule.rule_id}
              autoFocusName={freshId === rule.rule_id}
              workspace={workspace}
              rule={rule}
              capabilities={segmentCapabilities}
              allocation={ruleAllocation}
              onRepoint={async (side, newSegmentId) => {
                const appliedWorkspace = currentWorkspace();
                const sourceWorkspace = previewWorkspace();
                if (!appliedWorkspace || !sourceWorkspace) {
                  throw new Error("there is no structured workspace to edit");
                }
                const bufferRevision = currentBufferMutationRevision();
                const result = await executeVisualCommand(
                  {
                    operation: "rederive_link",
                    rule_id: rule.rule_id,
                    side,
                    segment_id: newSegmentId,
                  },
                  sourceWorkspace,
                  appliedWorkspace,
                  bufferRevision,
                );
                const updated = workspaceFromVisualDraft(result.draft).links.find(
                  (candidate) => candidate.rule_id === rule.rule_id,
                );
                if (!updated) throw new Error("VS-API returned no rederived link rule");
                patchBuffer(key, applied, () => updated);
                return result.notice ?? "link physics rederived by VS-API";
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
              authoring={authoring!}
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
              authoring={authoring!}
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
      case "source-yaml": {
        return null;
      }
      case "customize-chain": {
        const dependencyRefs =
          assemblyResult?.compile_result.dependency_closure?.entries
            .filter((entry) => entry.family !== "sessions")
            .map((entry) => entry.ref) ?? [];
        return {
          title: "Customize referenced component",
          content: (
            <CustomizeChainEditor
              segmentId={target.segmentId}
              rootRef={target.rootRef}
              dependencyRefs={dependencyRefs}
              onCustomize={async (leafRef, targetLeafRef) => {
                const result = await customizeChain({
                  segment_id: target.segmentId,
                  leaf_ref: leafRef,
                  ...(targetLeafRef ? { target_leaf_ref: targetLeafRef } : {}),
                });
                if (result.applied) {
                  commitWorkspace(
                    workspaceFromVisualDraft(result.draft),
                    "customize-chain",
                  );
                }
                if (result.applied) setSaveState({ kind: "idle" });
                return result;
              }}
              onClose={() => closeWindow(targetKey(target))}
            />
          ),
        };
      }
      case "open-session": {
        // Catalog identity → backend-owned lossless visual draft. The browser
        // never parses persisted session grammar into its structured workspace.
        const openEntry = (entry: CatalogDocumentSummary, targetRef?: string) => {
          setOpenSessionError(null);
          // Preserve the current draft before opening displaces it; a refused
          // stash holds this gesture for the choice dialog.
          displace(() => {
            void openSession(entry, targetRef as SessionRef | undefined).then((result) => {
              if (!result.ok) {
                setOpenSessionError(result.reason);
                return;
              }
              openWorkspace(workspaceFromVisualDraft(result.draft));
              closeAllWindows();
              setSelection(null);
              setSaveState({ kind: "idle" });
            });
          }, `opening ${entry.display_name}`);
        };
        return {
          title: "Open a session",
          content: (
            <OpenSessionPicker
              sessions={sessions}
              sessionsError={sessionsError}
              openError={openSessionError}
              onOpen={openEntry}
              onExport={exportSessionYaml}
              onImport={async (yamlFiles, proposalToken) => {
                const result = await importSessionYamlFiles(yamlFiles, proposalToken);
                if (result.outcome === "committed") await refreshSessions();
                return result;
              }}
            />
          ),
        };
      }
      case "save-session": {
        // Save is a small dialog, not a silent write: confirm the name it
        // saves under (into your library), then Save. Deploy stays its own
        // toolbar action.
        const canSave =
          !!workspace &&
          !loading &&
          assemblyResult?.visual_draft.draft_revision === visualDraft?.draft_revision &&
          saveState.kind !== "save-committed-unverified" &&
          compileResult?.save_verdict.allowed === true;
        const saveBlocker =
          compileResult?.save_verdict.blockers?.[0]?.message ?? error;
        return {
          title: "Save session",
          content: workspace ? (
            <SaveSessionDialog
              // Remount per workspace identity: the buffered name follows
              // the session being saved, not a previous one.
              key={workspace.session_name}
              workspaceName={workspace.session_name}
              canSave={canSave}
              blockedReason={saveBlocker}
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
                onCustomize={handleLibraryCustomize}
                onInspect={(entry) => void handleLibraryInspect(entry)}
                onNew={handleLibraryNew}
              />
              {libraryError && <div className="builder-warning">{libraryError}</div>}
            </>
          ),
        };
      }
      case "library": {
        if (!catalogDraft) return null;
        const metadata = builderBootstrap.bootstrap?.families.find(
          (entry) => entry.family === catalogDraft.family,
        );
        if (!metadata) {
          return {
            title: "Catalog draft",
            content: <div className="builder-warning">Catalog family metadata is unavailable.</div>,
          };
        }
        const verb =
          catalogDraft.source_ref && catalogDraft.source_ref !== catalogDraft.target_ref
            ? "Customize"
            : catalogDraft.expected_target_revision
              ? "Edit"
              : "New";
        const objectKind = metadata.wrapper?.replace(/_/g, " ") ?? catalogDraft.family;
        return {
          title: `${verb} ${objectKind}`,
          content: (
            <CatalogDraftEditorWindow
              authoring={authoring!}
              key={catalogDraft.target_ref}
              initialDraft={catalogDraft}
              initialRecovery={catalogDraftRecovery}
              metadata={metadata}
              onSaved={async (result) => {
                setCatalogDraft(result.draft);
                handleCatalogDraftRecovery(null);
                await announceCatalogDraftSaved(result);
              }}
              onRecoveryChange={handleCatalogDraftRecovery}
              onDiscard={discardCatalogDraft}
              onClose={(dirty) => {
                if (!dirty) {
                  setCatalogDraft(null);
                  handleCatalogDraftRecovery(null);
                }
                closeWindow("library");
              }}
            />
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
  const assemblySettled =
    visualDraft !== null &&
    assemblyResult?.visual_draft.draft_revision === visualDraft.draft_revision;
  const hasSavableDraft = workspace !== null && visualDraft !== null;

  return (
    <div
      className={`builder-shell${yamlBlocksGui ? " builder-shell--gui-locked" : ""}`}
      data-testid="builder-shell"
    >
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
                setSaveState({ kind: "idle" });
                void createDraft({}).then(
                  (draft) => openWorkspace(workspaceFromVisualDraft(draft)),
                  (cause) =>
                    setRestoreError(cause instanceof Error ? cause.message : String(cause)),
                );
              }, "starting a new session");
            }}
          />
          <IconButton
            className="builder-toolbar-btn"
            icon="folder-open"
            size={17}
            disabled={loading && openedSession !== null}
            label={
              loading && openedSession
                ? `Opening ${openedSession.display_name}…`
                : "Open a session — from your library or the NodalArc library"
            }
            onClick={() => {
              // Refetch on every open so the picker never offers stale catalog
              // revisions after another authoring operation.
              void refreshSessions();
              openEditor({ kind: "open-session" });
            }}
          />
          <IconButton
            className="builder-toolbar-btn"
            icon="save"
            size={17}
            disabled={
              !hasSavableDraft ||
              loading ||
              !assemblySettled ||
              saveState.kind === "save-committed-unverified" ||
              compileResult?.save_verdict.allowed !== true
            }
            label={
              !hasSavableDraft
                ? "Nothing to save yet"
                : loading
                  ? "Compile must finish before save"
                  : !assemblySettled
                    ? "The latest visual draft must compile before save"
                  : saveState.kind === "save-committed-unverified"
                    ? `Save committed without verification for ${saveState.sessionRef}; reopen before saving again`
                  : compileResult?.save_verdict.allowed === true
                    ? "Save session to your library"
                    : (compileResult?.save_verdict.blockers?.[0]?.message ??
                      "The backend must compile this draft before save")
            }
            onClick={() => {
              openEditor({ kind: "save-session" });
            }}
          />
          <IconButton
            className="builder-toolbar-btn"
            icon="rocket"
            size={17}
            disabled={!deployGate.ok || saveState.kind === "deploying" || transitionInFlight}
            label={
              saveState.kind === "deploying"
                ? "Deployment request is being accepted"
                : transitionInFlight
                  ? `Deployment ${transitionOperation?.state ?? "accepted"}; wait for terminal proof`
                : deployGate.ok &&
                    (saveState.kind === "saved" || saveState.kind === "deploy-accepted")
                  ? `Deploy ${saveState.sessionRef} to cluster`
                  : (deployGate.reason ?? "save the session first, then deploy")
            }
            onClick={async () => {
              if (!deployGate.ok) return;
              if (saveState.kind !== "saved" && saveState.kind !== "deploy-accepted") return;
              if (transitionInFlight) return;
              const { name, sessionRef, sessionRevision, deployVerdict } = saveState;
              setSaveState({
                kind: "deploying",
                name,
                sessionRef,
                sessionRevision,
                deployVerdict,
              });
              try {
                const accepted = await deploySession({
                  session_ref: sessionRef,
                  expected_session_revision: sessionRevision,
                  expected_document_digest: deployVerdict.digests.document,
                  expected_dependency_digest: deployVerdict.digests.dependency,
                });
                setSaveState({
                  kind: "deploy-accepted",
                  name,
                  sessionRef,
                  sessionRevision,
                  deployVerdict,
                  operationId: accepted.operation_id,
                });
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
            disabled={!hasStructuredBackup() && !hasStructuredAutosave()}
            label={
              hasStructuredBackup()
                ? "Restore — bring back the draft the last Open or New displaced"
                : hasStructuredAutosave()
                  ? "Restore the autosaved draft from this browser"
                  : "Nothing to restore"
            }
            onClick={restoreCurrentStructuredRecovery}
          />
        </span>
        <IconButton
          className="builder-toolbar-btn"
          icon="library"
          size={17}
          label={
            catalogDraftRecovery
              ? `Library — resume unsaved ${catalogDraftRecovery.draft.target_ref}`
              : "Library — every block you could build with, shipped and yours"
          }
          onClick={() => {
            openEditor({ kind: "catalog" });
            if (catalogDraftRecovery) openEditor({ kind: "library" });
          }}
        />
      </div>
      <div className="builder-outline" data-testid="builder-outline">
        <div className="builder-zone-title">World</div>
        {openedSession && visualDraft && (
          <div className="builder-warning" data-testid="opened-session-lossless">
            {openedSession.display_name} is open as a synchronized graphical and YAML draft.
            VS-API owns parsing, canonicalization, component-chain forking, and save assembly.
            {visualDraft.source_ref && visualDraft.source_ref !== visualDraft.target_ref && (
              <div className="builder-site-derived">
                This copy targets {visualDraft.target_ref}. Change session.name in the source YAML
                to {(visualDraft.target_ref.split("/").pop() ?? "").replace(/\.ya?ml$/, "")} before
                Save; the backend blocks a mismatched identity.
              </div>
            )}
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
            workspace={previewWorkspace() ?? workspace}
            sessionNameIsPlaceholder={
              visualDraft
                ? visualDraft.session_name_is_placeholder &&
                  (previewWorkspace() ?? workspace).session_name ===
                    (visualDraft.target_ref.split("/").pop() ?? "").replace(/\.ya?ml$/, "")
                : false
            }
            saved={saveState.kind === "saved" || saveState.kind === "deploying" || saveState.kind === "deploy-accepted" ? ("name" in saveState ? saveState.name : null) : null}
            deployed={false}
            resolvedSiteCount={world ? distinctGroundStationSites(world.nodes) : null}
            onAddConstellation={() => {
              ensureThenCreate(() => {
                setLibraryError(null);
                if (!authoring) {
                  setLibraryError("Builder authoring facts are unavailable");
                  return;
                }
                void applyWorkspaceCommand({
                  operation: "add_generated_space",
                  phasing_mode: authoring.default_phasing_mode,
                })
                  .then((result) => {
                    openEditor({ kind: "segment", id: result.affected_id });
                    setFreshId(result.affected_id);
                  })
                  .catch((cause) =>
                    setLibraryError(cause instanceof Error ? cause.message : String(cause)),
                  );
              }, "adding a constellation");
            }}
            onAddGround={() => {
              ensureThenCreate(() => {
                setLibraryError(null);
                void applyWorkspaceCommand({ operation: "add_ground" })
                  .then((result) => {
                    openEditor({ kind: "ground", id: result.affected_id });
                    setFreshId(result.affected_id);
                  })
                  .catch((cause) =>
                    setLibraryError(cause instanceof Error ? cause.message : String(cause)),
                  );
              }, "adding a ground segment");
            }}
            onAddDomain={() => {
              setLibraryError(null);
              void applyWorkspaceCommand({ operation: "add_routing_domain" })
                .then((result) => {
                  openEditor({ kind: "domain", id: result.affected_id });
                  setFreshId(result.affected_id);
                })
                .catch((cause) =>
                  setLibraryError(cause instanceof Error ? cause.message : String(cause)),
                );
            }}
            onOpenSession={() => openEditor({ kind: "session" })}
            onOpenSegment={(kind, id) => openEditor({ kind, id })}
          />
        )}
        {workspace && (
          <div className="builder-outline-group" data-testid="builder-drafts">
            <div className="builder-library-entry">
              <span className="builder-outline-kind">
                Drafts · {workspace.session_name}
              </span>
              <span className="builder-library-actions">
                <span className="builder-outline-count">
                  {timeRateSummary(workspace.step_seconds, workspace.compression)}
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
                    label="Customize a nested component through backend-managed forks"
                    disabled={placed.source_ref === null}
                    onClick={() =>
                      openEditor({
                        kind: "customize-chain",
                        segmentId: placed.segment_id,
                        rootRef: placed.source_ref ?? "",
                      })
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
                    {draft.planes === null || draft.slots_per_plane === null
                      ? "incomplete"
                      : `${draft.planes * draft.slots_per_plane} sat`}
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
                    value={
                      schedulingSelections[schedulingSelectionKey(placed.segment_id)] ?? ""
                    }
                    onChange={(value) => {
                      if (!value) return;
                      setLibraryError(null);
                      void applyWorkspaceCommand({
                        operation: "set_scheduling_preset",
                        segment_id: placed.segment_id,
                        preset: value as BuilderVisualSchedulingPreset,
                      }).catch((cause) =>
                        setLibraryError(cause instanceof Error ? cause.message : String(cause)),
                      );
                    }}
                    options={[
                      ...(schedulingSelections[schedulingSelectionKey(placed.segment_id)] == null
                        ? [{ value: "", label: "Imported block (custom)" }]
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
                    label="Customize a nested component through backend-managed forks"
                    disabled={placed.site_set_ref === null}
                    onClick={() =>
                      openEditor({
                        kind: "customize-chain",
                        segmentId: placed.segment_id,
                        rootRef: placed.site_set_ref ?? "",
                      })
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
                  {rule.a.role === null || rule.b.role === null
                    ? "role incomplete"
                    : rule.a.role}
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
                <span className="builder-outline-count">
                  {domain.protocol ?? "protocol incomplete"}
                </span>
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
                  <span className="builder-outline-count">
                    {boundary.adapter ?? "adapter incomplete"}
                  </span>
                </button>
              );
            })}
            {placedSegments(workspace).length > 0 && (
              <div className="builder-preset-row">
                <Button
                  title="A protocol over member segments — seeds over everything"
                  onClick={() => {
                    setLibraryError(null);
                    void applyWorkspaceCommand({ operation: "add_routing_domain" })
                      .then((result) => {
                        openEditor({ kind: "domain", id: result.affected_id });
                        setFreshId(result.affected_id);
                      })
                      .catch((cause) =>
                        setLibraryError(
                          cause instanceof Error ? cause.message : String(cause),
                        ),
                      );
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
                    setLibraryError(null);
                    void applyWorkspaceCommand({ operation: "add_boundary" })
                      .then((result) =>
                        openEditor({ kind: "boundary", id: result.affected_id }),
                      )
                      .catch((cause) =>
                        setLibraryError(
                          cause instanceof Error ? cause.message : String(cause),
                        ),
                      );
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
              onClick={() => {
                ensureThenCreate(() => {
                  setLibraryError(null);
                  if (!authoring) {
                    setLibraryError("Builder authoring facts are unavailable");
                    return;
                  }
                  void applyWorkspaceCommand({
                    operation: "add_generated_space",
                    phasing_mode: authoring.default_phasing_mode,
                  })
                    .then((result) => {
                      openEditor({ kind: "segment", id: result.affected_id });
                      setFreshId(result.affected_id);
                    })
                    .catch((cause) =>
                      setLibraryError(cause instanceof Error ? cause.message : String(cause)),
                    );
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
          </div>
        )}
        {saveState.kind === "deploying" && (
          <div className="builder-library-note">switching the cluster…</div>
        )}
        {saveState.kind === "deploy-accepted" && (
          <BuilderTransitionStatus
            operationId={saveState.operationId}
            operation={transitionOperation}
            pollError={transitionPollError}
            reviewed={saveState.deployVerdict.digests}
          />
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
                    <div
                      className="builder-library-entry"
                      data-segment-id={seg.segment_id}
                    >
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
                      {visualDraft && (
                        <IconButton
                          icon="pencil"
                          size={12}
                          label={`Customize a referenced component under ${seg.display_name}`}
                          onClick={() =>
                            openEditor({
                              kind: "customize-chain",
                              segmentId: seg.segment_id,
                              rootRef: "",
                            })
                          }
                        />
                      )}
                    </div>
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
              : visualDraft
                ? "compiling backend visual draft…"
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
            {resolveError
              ? `The session does not resolve — the canvas returns when it does. ${truncateError(
                  resolveError.error,
                )}`
              : loading
                ? "Resolving draft…"
                : hasDrafts
                  ? "The backend returned no preview. Review its typed findings."
                  : "Add a constellation to begin — incomplete content remains in the visual draft"}
          </div>
        ) : openedSession ? (
          <div className="builder-zone-empty">
            {loading
              ? `Compiling ${openedSession.display_name} through the backend…`
              : error
                ? `The catalog session did not compile. ${truncateError(error)}`
                : `Backend compiled ${openedSession.display_name}. Edit its lossless source or customize a referenced component.`}
          </div>
        ) : (
          <div className="builder-start-card" data-testid="builder-start">
            <div className="builder-zone-title">Build a session from scratch</div>
            <ol className="builder-start-steps">
              <li>Add constellations — VS-API seeds an editable orbital draft</li>
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
                  setSaveState({ kind: "idle" });
                  void createDraft({}).then(
                    (draft) => openWorkspace(workspaceFromVisualDraft(draft)),
                    (cause) =>
                      setRestoreError(cause instanceof Error ? cause.message : String(cause)),
                  );
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
      <div className="builder-windows" data-testid="builder-windows">
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
              raiseId={win.key}
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
        {visualDraft?.authoring_workspace?.control_tree?.root && (
          <details className="builder-generic-controls" data-testid="session-generic-controls">
            <summary>Additional graphical fields</summary>
            <GraphicalControlTreeEditor
              tree={visualDraft.authoring_workspace.control_tree}
              disabled={yamlBlocksGui || dirtyWindows > 0 || writer !== null}
              hideSpecialized
              onMutate={async (commands: ReadonlyArray<BuilderControlMutation>) => {
                setLibraryError(null);
                try {
                  const result = await mutateControls(commands);
                  openWorkspace(workspaceFromVisualDraft(result.visual_draft));
                  setSaveState({ kind: "idle" });
                } catch (cause) {
                  setLibraryError(cause instanceof Error ? cause.message : String(cause));
                  throw cause;
                }
              }}
            />
          </details>
        )}
        {visualDraft ? (
          <>
            <div
              className={`builder-yaml-revision${yamlBuffer.issues.length > 0 ? " builder-yaml-revision--error" : ""}`}
              data-testid="builder-yaml-revision"
            >
              {visualDraft.applied_revision !== null && visualDraft.applied_revision !== undefined
                ? `Showing applied revision ${visualDraft.applied_revision}; buffer generation ${yamlBuffer.generation}${
                    yamlBuffer.issues.length > 0 ? " has errors" : ""
                  }`
                : `Buffer generation ${yamlBuffer.generation} has no valid graphical projection`}
            </div>
            {dirtyWindows > 0 && (
              <div className="builder-site-derived">
                Apply or discard graphical window edits before editing YAML.
              </div>
            )}
            <PasteArea
              className="builder-yaml-editor"
              value={yamlBuffer.text}
              disabled={dirtyWindows > 0 || (writer !== null && writer !== "yaml")}
              onChange={editYamlBuffer}
              ariaLabel="Session YAML"
              rows={32}
            />
            <div className="builder-preset-row">
              <Button
                disabled={
                  dirtyWindows > 0 ||
                  writer !== null ||
                  (!yamlBuffer.dirty && yamlBuffer.issues.length === 0)
                }
                onClick={() => void applyYamlBuffer(yamlBuffer.generation)}
              >
                {writer === "yaml" ? "Applying…" : "Apply YAML"}
              </Button>
              {yamlBuffer.canonicalizationRequired && (
                <Button
                  variant="primary"
                  disabled={writer !== null || dirtyWindows > 0}
                  onClick={() => void useCanonicalYaml()}
                >
                  Use canonical YAML
                </Button>
              )}
              <Button
                onClick={() => {
                  const p = navigator.clipboard?.writeText(yamlBuffer.text);
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
                onClick={() =>
                  downloadBlob(
                    yamlBuffer.text,
                    `${world?.session.name ?? workspace?.session_name ?? "session"}.yaml`,
                  )
                }
              >
                Download
              </Button>
            </div>
            {yamlBuffer.canonicalizationRequired && (
              <div className="builder-warning" data-testid="builder-canonicalization-warning">
                This buffer contains comments or formatting that canonical save and graphical edits
                cannot preserve. Review and choose Use canonical YAML before continuing graphically or
                saving.
              </div>
            )}
            {yamlBuffer.issues.map((issue, index) => (
              <div
                className="builder-warning"
                key={`${issue.code}:${issue.json_pointer ?? ""}:${index}`}
              >
                {issue.source_line && issue.source_column
                  ? `Line ${issue.source_line}, column ${issue.source_column}: `
                  : ""}
                {issue.message}
              </div>
            ))}
          </>
        ) : (
          <div className="builder-zone-empty">
            The session YAML buffer appears here when a draft opens.
          </div>
        )}
      </div>
      {workspace &&
        ((wall && !isOpen(wall.key)) || findings.length > 0) && (
        <div className="builder-rail" data-testid="builder-rail">
          {wall && !isOpen(wall.key) && (
            <button
              className="builder-rail-chip builder-rail-chip--wall"
              title={resolveError?.error}
              onClick={() => openEditor(wall.target)}
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
                openEditor(target);
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
        {error ? (
          <span
            className="builder-status-item builder-status-item--error"
            title={error}
          >
            {truncateError(error)}
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
              ? loading
                ? "resolving draft…"
                : hasDrafts
                  ? "backend returned no resolved preview"
                  : "add a constellation to begin"
              : openedSession
                ? loading
                  ? `opening ${openedSession.display_name}…`
                  : `opened ${openedSession.display_name} losslessly`
                : "no session loaded"}
          </span>
        )}
        {compileIssues
          .filter((issue) => issue.severity !== "error")
          .slice(0, 2)
          .map((issue) => (
            <span
              className="builder-status-item builder-status-item--hint"
              title={`${issue.stage}: ${issue.code}`}
              key={`${issue.stage}:${issue.code}:${issue.message}`}
            >
              {issue.message}
            </span>
          ))}
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
        {saveState.kind === "save-committed-unverified" && (
          <span className="builder-status-item builder-status-item--error">
            save committed but unverified for {saveState.sessionRef} — reopen before retrying
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
                  stashCurrentStructuredRecovery({ force: true });
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
