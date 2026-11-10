// Copyright 2024-2026 .chance (dotchance)
// SPDX-License-Identifier: AGPL-3.0-only
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
  type MutableRefObject,
} from "react";
import { Scene, type SceneSnapshot } from "../globe/r3f/Scene";
import { VisualizationErrorBoundary } from "../globe/VisualizationErrorBoundary";
import { buildRegimeIndex } from "../taxonomy/regime";
import { Button, IconButton } from "../ui/Button";
import { FloatingWindow } from "../ui/FloatingWindow";
import { RecordHistoryToggle } from "../ui/RecordHistoryToggle";
import type { GlobeActions } from "../globe/actions";
import type { ColorMode, GlobeMode, ReferenceFrame, Selection } from "../types";
import { Icon } from "../ui/icons/Icon";
import { BuildGuide } from "./BuildGuide";
import { BuilderInspector } from "./BuilderInspector";
import {
  BuilderTransitionStatus,
  transitionIsTerminal,
  useBuilderTransitionOperation,
} from "./BuilderTransitionStatus";
import { AreaLegend } from "../routing/AreaLegend";
import { buildAreaColoring } from "../routing/instances";
import { builderSnapshotFromWorld } from "./builderSnapshot";
import { CandidateLines } from "./CandidateLines";
import { computeCandidates } from "./candidates";
import { Field, PasteArea } from "./editorKit";
import { PendingEditApplyRow, PendingObjectFields } from "./pendingEditKit";
import { CatalogObjectView } from "./CatalogObjectView";
import { ConstellationEditor } from "./ConstellationEditor";
import { CustomizeChainEditor } from "./CustomizeChainEditor";
import { GroundEditor } from "./GroundEditor";
import { LibraryPanel } from "./LibraryPanel";
import { LinkAndRoutingOutline } from "./LinkAndRoutingOutline";
import { LinkRuleWindow } from "./LinkRuleEditor";
import { CatalogDraftEditorWindow } from "./CatalogDraftEditorWindow";
import { ProposalWindow } from "./ProposalWindow";
import { OpenSessionPicker } from "./OpenSessionPicker";
import { BoundaryListWindow, RoutingDomainWindow } from "./RoutingEditor";
import { SessionEditor, timeRateSummary } from "./SessionEditor";
import { listSessionObjects } from "./sessionListing";
import {
  canDeploy,
  draftHoldsUnsavedWork,
  pendingEditKey,
  pendingEditsPhrase,
  refusalMessage,
  yamlBlocksGraphicalEditing,
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
import {
  pendingEditAddress,
  pendingEditTarget,
  pendingEditTitle,
  useEditorWindows,
  targetKey,
  type EditorTarget,
} from "./useEditorWindows";
import {
  BuilderApiError,
  createCatalogDraft,
  openCatalogDraft,
} from "./builderApiClient";
import { useYamlAutoApply } from "./useYamlBuffer";
import { isJsonObject, refStem, sessionSegment, sessionSegments } from "./sessionSegments";
import type {
  BuilderMemberAddress,
  BuilderVisualDraftAssemblyResult,
  BuilderVisualDraftEnvelope,
  BuilderDeployVerdict,
  BuilderIssue,
  BuilderVisualAuthoringFacts,
  BuilderVisualDraftApplyYamlResult,
  BuilderVisualSchedulingPreset,
  BuilderVisualDraftCommandRequest,
  BuilderVisualDraftCommandResult,
  CatalogComponentDraftEnvelope,
  CatalogComponentFamily,
  CatalogDocumentSummary,
  JsonValue,
  SessionRef,
} from "./generated/builderApi";

/** The editor that owns a refusal: the segment or link rule its pointer names in
 *  the session the backend compiled. A refusal naming no such object has none,
 *  and the page shows it at session level. */
function refusalTarget(
  issue: BuilderIssue | null,
  session: Readonly<Record<string, JsonValue>> | null,
): { target: EditorTarget; key: string } | null {
  const match = /^\/(segments|link_rules)\/(\d+)$/.exec(issue?.json_pointer ?? "");
  const list = match && session ? session[match[1]!] : undefined;
  const member = Array.isArray(list) ? list[Number(match![2])] : undefined;
  if (!member || typeof member !== "object" || Array.isArray(member)) return null;
  const id = member["id"];
  if (typeof id !== "string") return null;
  const target: EditorTarget =
    match![1] === "link_rules" ? { kind: "link", id } : { kind: "segment", id };
  return { target, key: targetKey(target) };
}
import type { BuilderWorld } from "./builderTypes";

interface BuilderViewProps {
  /** True while the Builder is visible. The component stays mounted so drafts,
   *  windows, and buffers survive view changes. Scene mounting, global key
   *  listeners, and reveal effects are active only while this flag is true. */
  active: boolean;
  /** Shared display state — the toolbar operates on the builder scene exactly
   *  as it does on the live scene (same Scene component, same toggles). */
  colorMode: ColorMode;
  /** The IS-IS or OSPF instance area coloring shows; null shows the first. */
  areaInstanceId: string | null;
  onSelectAreaInstance: (domainId: string) => void;
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

/** Save is a small dialog, not a silent write. The name it saves under is the
 *  session's identity: a new name saves the session under that name. With
 *  pending edits the primary action applies them first, and saving around
 *  them is the quieter, explicit choice. */
function SaveSessionDialog({
  sessionName,
  canSave,
  blockedReason,
  saveState,
  unappliedEdits,
  staleList,
  onSave,
  onClose,
}: {
  sessionName: string;
  canSave: boolean;
  blockedReason: string | null;
  saveState: SaveState;
  /** The pending edits. */
  unappliedEdits: number;
  /** The stale pending edits, each named by its object. */
  staleList: { key: string; title: string }[];
  onSave: (opts: {
    applyAll: boolean;
    name: string;
    confirmedStaleKeys?: ReadonlySet<string>;
  }) => void;
  onClose: () => void;
}) {
  const [name, setName] = useState(sessionName);
  // The stale confirm sub-view. Each stale edit starts DECLINED:
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
  // stale edit mid-confirm falls back to the summary rather than an empty list.
  if (confirming && hasStale) {
    const confirmCount = staleList.filter((s) => confirmed[s.key]).length;
    return (
      <div className="builder-inspector-stack" data-testid="builder-save-dialog">
        <div className="builder-warning" data-testid="save-stale-confirm">
          {count(staleList.length, "object")} changed underneath your edits.
          Check the ones whose edits should overwrite the current values; the
          rest are left out and keep their edits.
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
              onSave({ applyAll: true, name, confirmedStaleKeys: confirmedKeys() })
            }
          >
            <Icon name="save" size={13} />{" "}
            {saving
              ? "Saving…"
              : confirmCount > 0
                ? `Overwrite ${count(confirmCount, "object")} and save`
                : "Save without the stale edits"}
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
        onChange={setName}
      />
      <div className="builder-site-derived">
        VS-API validates and owns the saved session identifier.
      </div>
      {!canSave && unappliedEdits === 0 && (
        <div className="builder-warning">
          {blockedReason ?? "the session must resolve before it can be saved"}
        </div>
      )}
      {!canSave && unappliedEdits > 0 && (
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
        {unappliedEdits > 0 ? (
          <>
            <Button
              variant="primary"
              disabled={!canSave || saving}
              // With stale windows present, the primary opens the confirm flow
              // rather than applying blindly; with none it applies directly.
              onClick={() =>
                hasStale
                  ? setConfirming(true)
                  : onSave({ applyAll: true, name })
              }
            >
              <Icon name="save" size={13} />{" "}
              {saving
                ? "Saving…"
                : hasStale
                  ? `Apply ${count(unappliedEdits, "edit")} and save…`
                  : `Apply ${count(unappliedEdits, "edit")} and save`}
            </Button>
            <Button
              // The applied-only escape hatch is gated by the APPLIED
              // session's truth, which only the server knows once unapplied
              // edits diverge the preview — so it always attempts, and a
              // refusal lands verbatim as the save error.
              disabled={saving}
              title={`leaves out the ${count(unappliedEdits, "unapplied edit")}`}
              onClick={() => onSave({ applyAll: false, name })}
            >
              Save applied state only
            </Button>
          </>
        ) : (
          <Button
            variant="primary"
            disabled={!canSave || saving}
            onClick={() => onSave({ applyAll: false, name })}
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
  areaInstanceId,
  onSelectAreaInstance,
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
    pendingEdits,
    pendingEditOutcomes,
    checkingObjects,
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
    applyYamlBufferOnce,
    useCanonicalYaml,
    previewPendingEdits,
    showAcceptedCompile,
    customizeChain,
    runVisualCommand,
    mutateControls,
    editPendingObject,
    describePendingObject,
    holdObjectCheck,
    applyPendingEdits,
    discardPendingEdit,
    proposalSession,
    stageEdit,
    removeListMember,
    undo,
    prepareRetarget,
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
  const nodeCatalog = useBuilderCatalog("nodes");
  const terminalCatalog = useBuilderCatalog("terminals");
  const builderBootstrap = useBuilderBootstrap();
  const authoring: BuilderVisualAuthoringFacts | null =
    builderBootstrap.bootstrap?.authoring ?? null;
  const schedulingPresets = builderBootstrap.bootstrap?.scheduling_presets ?? [];
  const [schedulingSelections, setSchedulingSelections] = useState<
    Record<string, BuilderVisualSchedulingPreset | null>
  >({});

  // A gesture that would discard unsaved work waits here for the user's answer.
  const [pendingDisplace, setPendingDisplace] = useState<{
    message: string;
    answer: (discard: boolean) => void;
  } | null>(null);
  /** Ask whether to discard unsaved work; resolves true on "Discard and continue". */
  const askToDiscard = (message: string) =>
    new Promise<boolean>((answer) => setPendingDisplace({ message, answer }));
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
  // A placed reference has no editor, so its Use scrolls its outline row into
  // view and flashes it. A separate consume-once channel from
  // the Library reveal — a placement shows where it landed in the session
  // anatomy, it never opens the Library.
  const outlineReveal = useOutlineReveal();
  const [revealedSegment, setRevealedSegment] = useState<string | null>(null);
  useEffect(() => {
    // Clear the flash when the builder is hidden — otherwise the effect cleanup
    // cancels the reset timer and the row stays lit, replaying on every re-show.
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
  // The floating-editor windows (useEditorWindows).
  const { windows, openEditor, closeWindow, closeSessionWindows, isOpen } = useEditorWindows();
  const [saveState, setSaveState] = useState<SaveState>({ kind: "idle" });
  // Why the last removal from a link rule or routing domain window was
  // refused, with that window's key.
  const [removalFailure, setRemovalFailure] = useState<{ key: string; message: string } | null>(
    null,
  );
  // Whether the next deploy records the session run's history.
  const [recordHistory, setRecordHistory] = useState(false);
  const adoptAppliedYaml = useCallback((result: BuilderVisualDraftApplyYamlResult) => {
    if (result.applied) setSaveState({ kind: "idle" });
    return result;
  }, []);
  const applySessionYaml = useCallback(
    async (generation: number) => adoptAppliedYaml(await applyYamlBuffer(generation)),
    [adoptAppliedYaml, applyYamlBuffer],
  );
  const applySessionYamlOnce = useCallback(
    async (generation: number) => {
      const result = await applyYamlBufferOnce(generation);
      return result === null ? null : adoptAppliedYaml(result);
    },
    [adoptAppliedYaml, applyYamlBufferOnce],
  );
  const useCanonicalSessionYaml = useCallback(
    async () => adoptAppliedYaml(await useCanonicalYaml()),
    [adoptAppliedYaml, useCanonicalYaml],
  );
  // The draft as it was last opened, created or saved.
  const [cleanDraft, setCleanDraft] = useState<{ targetRef: string; revision: number } | null>(
    null,
  );
  const markClean = (draft: BuilderVisualDraftEnvelope) =>
    setCleanDraft({ targetRef: draft.target_ref, revision: draft.draft_revision });
  /** One accepting gesture at a time: a command, a customization, a control
   *  edit or a YAML apply starts only when no change is being applied. Null
   *  when one may start; otherwise why not. */
  const unsettledReason = (): string | null => {
    if (!visualDraft) return "No session draft is open.";
    if (writer !== null) return "A change is still being applied; try again when it finishes.";
    return null;
  };
  const refuseUnlessSettled = () => {
    const reason = unsettledReason();
    if (reason !== null) throw new Error(reason);
  };
  // Pending edits. The YAML buffer and the session pane wait while any is
  // pending.
  const pendingEditCount = pendingEdits.size;
  // The pending edits the backend found stale: the accepted session changed
  // their object since the edit began. Each is named as the save dialog lists it.
  const stalePendingEdits = [...pendingEdits.entries()]
    .filter(([key]) => pendingEditOutcomes.get(key)?.status === "stale")
    .map(([key, pending]) => ({ key, title: pendingEditTitle(pending.edit.address) }));
  const unsavedSessionWork = draftHoldsUnsavedWork({
    draft: visualDraft,
    clean: cleanDraft,
    pendingEditCount,
    yamlDirty: yamlBuffer.dirty,
  });
  /** Run a gesture that replaces the open draft, asking first when the draft
   *  holds unsaved work. */
  const displace = (proceed: () => void, label: string) => {
    if (!unsavedSessionWork || !visualDraft) {
      proceed();
      return;
    }
    void askToDiscard(`${label} discards the unsaved changes to ${visualDraft.target_ref}.`).then(
      (discard) => {
        if (discard) proceed();
      },
    );
  };
  const startNewSession = () =>
    displace(() => {
      clear();
      setSelection(null);
      closeSessionWindows();
      setSaveState({ kind: "idle" });
      void createDraft({}).then(
        (draft) => markClean(draft),
        (cause) => setDraftError(cause instanceof Error ? cause.message : String(cause)),
      );
    }, "starting a new session");
  const ensureThenCreate = (create: () => void, label: string) => {
    if (visualDraft) create();
    else
      displace(() => {
        void createDraft({})
          .then((draft) => {
            markClean(draft);
            create();
          })
          .catch((cause) =>
            setLibraryError(cause instanceof Error ? cause.message : String(cause)),
          );
      }, label);
  };
  // Every link rule, routing domain and boundary the accepted session
  // declares, as the backend lists them, with the pending edits laid over them.
  const listed = useMemo(
    () =>
      listSessionObjects(
        assemblyResult?.listing ?? null,
        [...pendingEdits.values()].flatMap((pending) =>
          pending.answer === null ? [] : [pending.answer],
        ),
      ),
    [assemblyResult?.listing, pendingEdits],
  );
  // A link or domain window shows an object the backend lists or a pending
  // edit holds. It closes when neither holds the object any more. The boundary
  // list window closes when the list holds no boundary and no edit of it is
  // pending.
  useEffect(() => {
    for (const w of windows) {
      const target = w.target;
      const gone =
        (target.kind === "link" && !listed.linkRules.some((rule) => rule.id === target.id)) ||
        (target.kind === "domain" &&
          !listed.routingDomains.some((domain) => domain.id === target.id)) ||
        (target.kind === "boundaries" &&
          listed.boundaries.pending === null &&
          listed.boundaries.items?.length === 0);
      if (gone) closeWindow(w.key);
    }
  }, [closeWindow, listed, windows]);
  const wall = refusalTarget(resolveError?.issue ?? null, compileResult?.canonical_session_json ?? null);
  /** Inline wall text for one open editor window (null = not this window's). */
  const wallFor = (target: EditorTarget): string | null =>
    wall && targetKey(target) === wall.key ? (resolveError?.error ?? null) : null;
  // The edit-to-compile preview: pending edits are previewed by compiling the
  // accepted session with them applied; the backend accepts nothing.
  // The library revision is a dependency so a preview recompiles when a
  // user-catalog mutation changes the dependency closure under open edits.
  const libraryRevision = useLibraryRevision();
  // A New session the backend refuses surfaces here; the current draft stands.
  const [draftError, setDraftError] = useState<string | null>(null);
  const [openSessionError, setOpenSessionError] = useState<string | null>(null);
  const draftSegments = sessionSegments(visualDraft?.applied_session);
  const hasDrafts = draftSegments.length > 0;
  const sessionTime = visualDraft?.applied_session?.time;
  const sessionTimeRate =
    isJsonObject(sessionTime)
      ? timeRateSummary(
          typeof sessionTime.step_seconds === "number" ? sessionTime.step_seconds : null,
          typeof sessionTime.compression === "number" ? sessionTime.compression : null,
        )
      : "no accepted session yet";
  const yamlBlocksGui = yamlBlocksGraphicalEditing(visualDraft !== null, yamlBuffer);
  const yamlHasErrors = yamlBuffer.issues.length > 0 || yamlBuffer.applyFailure !== null;
  /** Why the windows that edit pending edits cannot change their objects now;
   *  null when they can. Those edits are made on the accepted session, so they
   *  wait while a change is being applied, while the draft holds no accepted
   *  session, and while the YAML is unapplied. */
  const pendingEditRefusal = (): string | null => {
    const unsettled = unsettledReason();
    if (unsettled !== null) return unsettled;
    if (!visualDraft?.applied_session) {
      return "The session draft holds no accepted session yet. Add a segment first.";
    }
    if (yamlBlocksGui) return "Apply or canonicalize the session YAML before editing here.";
    return null;
  };
  useEffect(() => {
    if (saveState.kind === "saving" || writer !== null || !visualDraft || yamlBlocksGui) return;
    if (pendingEdits.size === 0) {
      showAcceptedCompile();
      return;
    }
    const timer = setTimeout(() => void previewPendingEdits(), 300);
    return () => clearTimeout(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pendingEdits, libraryRevision, visualDraft, yamlBlocksGui, saveState.kind, writer]);

  // A failed apply is held in the buffer state, which the YAML pane shows.
  const autoApplySessionYaml = useCallback(
    (generation: number) => void applySessionYamlOnce(generation).catch(() => undefined),
    [applySessionYamlOnce],
  );
  useYamlAutoApply(
    yamlBuffer,
    visualDraft !== null && pendingEditCount === 0,
    autoApplySessionYaml,
  );

  const acceptedOperationId =
    saveState.kind === "deploy-accepted" ? saveState.operationId : null;
  const { operation: transitionOperation, error: transitionPollError } =
    useBuilderTransitionOperation(acceptedOperationId);
  const transitionInFlight =
    saveState.kind === "deploy-accepted" &&
    (transitionOperation === null || !transitionIsTerminal(transitionOperation.state));
  const [copyState, setCopyState] = useState<"idle" | "copied" | "failed">("idle");
  // the Copy result is transient feedback, cleared after a beat so the
  // control returns to its resting label.
  useEffect(() => {
    if (copyState === "idle") return;
    const t = setTimeout(() => setCopyState("idle"), 2000);
    return () => clearTimeout(t);
  }, [copyState]);
  // The pending edits the backend has issues with: the issues of its latest
  // answer, or its refusal of the edit. Each names its object and opens the
  // window that edits it.
  const pendingEditFindings = [...pendingEdits.entries()].flatMap(([key, pending]) => {
    const outcome = pendingEditOutcomes.get(key);
    const [first] = [
      ...(outcome !== undefined && outcome.status !== "applied" ? outcome.issues : []),
      ...(pending.answer?.issues ?? []),
    ];
    if (first === undefined) return [];
    const address = pending.edit.address;
    return [
      {
        key,
        message: `${pendingEditTitle(address)}: ${first.message}`,
        target: pendingEditTarget(address),
      },
    ];
  });
  /** One save path for both dialog actions: submit the accepted session with
   *  its proposals under the name the dialog states, then adopt it. */
  const performSave = async ({
    applyAll,
    name,
    confirmedStaleKeys,
  }: {
    applyAll: boolean;
    name: string;
    confirmedStaleKeys?: ReadonlySet<string>;
  }) => {
    if (!visualDraft) return;
    const stalePendingKeys = new Set(stalePendingEdits.map((stale) => stale.key));
    if (applyAll && stalePendingKeys.size > 0 && confirmedStaleKeys === undefined) {
      // Fail-closed backstop: a bulk apply-and-save with stale pending edits
      // must pass through the confirm flow, which decides each one (confirm =
      // overwrite the moved value, decline = leave it out). A direct call
      // carrying no decisions refuses rather than silently overwrite an
      // object the user reverted.
      setSaveState({
        kind: "failed",
        message: "confirm the stale edits before applying",
      });
      return;
    }
    let preparedRetargetForSave: Awaited<ReturnType<typeof prepareRetarget>> | null = null;
    let saveCapture: ReturnType<typeof captureCoordinator> | null = null;
    setSaveState({ kind: "saving" });
    try {
      if (yamlBlocksGui) {
        throw new Error("apply valid canonical YAML before saving the graphical session");
      }
      const saveName = name.trim();
      if (!saveName) throw new Error("session name is required");
      // Pending edits are applied first, as one accepted revision through the
      // route that applies them. A declined stale edit stays pending; a
      // confirmed one is re-applied over the object as the session now holds
      // it. A refusal applies none of them and fails the save.
      let editsApplied: BuilderVisualDraftAssemblyResult | null = null;
      if (applyAll) {
        const keys = [...pendingEdits.keys()].filter(
          (key) => !stalePendingKeys.has(key) || confirmedStaleKeys?.has(key) === true,
        );
        if (keys.length > 0) {
          const result = await applyEdits(
            keys,
            new Set(keys.filter((key) => stalePendingKeys.has(key))),
          );
          if (!result.applied || result.assembly === null) {
            throw new Error(
              `the backend applied none of the ${count(keys.length, "pending edit")}: ${refusalMessage(
                result,
              )}`,
            );
          }
          editsApplied = result.assembly;
        }
      }
      // The accepted session this save writes: the revision the pending
      // edits made, or the one the page holds.
      let assembled = editsApplied ?? assemblyResult;
      if (
        assembled === null ||
        (editsApplied === null && assembled.visual_draft.draft_revision !== visualDraft.draft_revision)
      ) {
        throw new Error("the latest session draft must compile before save");
      }
      const preparedRetarget =
        saveName !== refStem(assembled.visual_draft.target_ref)
          ? await prepareRetarget(`user:sessions/${saveName}.yaml`)
          : null;
      preparedRetargetForSave = preparedRetarget;
      if (preparedRetarget) assembled = preparedRetarget;
      if (!assembled.compile_result.save_verdict.allowed) {
        throw new Error(
          assembled.compile_result.save_verdict.blockers?.[0]?.message ??
            "the backend blocked this session draft from saving",
        );
      }
      const draft = assembled.visual_draft;
      if (!draft.applied_session) throw new Error("the session draft holds no accepted session");
      const capture = captureCoordinator();
      saveCapture = capture;
      const saved = await saveSession(
        {
          draft: {
            draft_revision: draft.draft_revision,
            state: {
              session: draft.applied_session,
              catalog_documents: draft.catalog_documents ?? [],
            },
          },
          target_ref: draft.target_ref,
          expected_session_revision: draft.expected_session_revision ?? null,
        },
        capture,
      );
      const result = saved.result;
      if (!saved.reopenedDraft) {
        if (preparedRetarget && captureIsCurrent(capture)) {
          adoptCommittedRetarget(preparedRetarget, {
            ref: result.session.ref,
            revision: result.session.revision,
            canonicalYaml: result.session.canonical_yaml,
          });
        }
        setSaveState({
          kind: "save-committed-unverified",
          message:
            saved.postCommitError ??
            "the session was saved, but the persisted revision could not be reopened",
          sessionRef: result.session.ref,
        });
        return;
      }
      markClean(saved.reopenedDraft);
      setSaveState({
        kind: "saved",
        name: saveName,
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
    pendingEditCount,
  });
  // Standalone component authoring (Your library) — independent of sessions.
  const [catalogDraftDirty, setCatalogDraftDirty] = useState(false);
  const [catalogDraft, setCatalogDraft] = useState<CatalogComponentDraftEnvelope | null>(null);
  const [libraryError, setLibraryError] = useState<string | null>(null);
  const visualCommandInFlight = useRef(false);

  const schedulingSelectionKey = (
    segmentId: string,
    siteId?: string,
    targetRef = visualDraft?.target_ref,
  ) => `${targetRef ?? "no-draft"}|${segmentId}${siteId ? `/${siteId}` : ""}`;

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
        command.site_id ?? undefined,
        result.draft.target_ref,
      );
    } else if (result.affected_kind === "ground" && preset !== null) {
      // A command that created a ground segment states the preset it wrote.
      key = schedulingSelectionKey(result.affected_id, undefined, result.draft.target_ref);
    }
    if (!key) return;
    setSchedulingSelections((current) => ({
      ...current,
      [key]: preset,
    }));
  };

  /** Run one command over the accepted draft, with every pending edit sent
   *  beside it. A command that created pending edits accepted nothing. */
  const runCommand = async (
    command: BuilderVisualDraftCommandRequest["command"],
  ): Promise<BuilderVisualDraftCommandResult> => {
    if (visualCommandInFlight.current) {
      throw new Error("another visual command is still being applied");
    }
    refuseUnlessSettled();
    visualCommandInFlight.current = true;
    try {
      const result = await runVisualCommand(command);
      rememberSchedulingSelection(command, result);
      setSaveState({ kind: "idle" });
      return result;
    } finally {
      visualCommandInFlight.current = false;
    }
  };

  /** Apply pending edits as the next accepted revision, through the route
   *  that applies them. A refused batch accepts nothing and leaves every edit
   *  pending with the backend's outcome. */
  const applyEdits = async (keys: ReadonlyArray<string>, reapply?: ReadonlySet<string>) => {
    const refusal = pendingEditRefusal();
    if (refusal !== null) throw new Error(refusal);
    return applyPendingEdits(keys, reapply);
  };

  /** Apply the pending edit of one window, re-applying it over the object as
   *  the session now holds it when `reapply`. Resolves whether it was applied. */
  const applyWindowEdit = async (key: string, reapply: boolean): Promise<boolean> => {
    const result = await applyEdits([key], reapply ? new Set([key]) : undefined);
    if (result.applied) setSaveState({ kind: "idle" });
    // A refusal the edit's own outcome does not explain is shown in its window.
    if (!result.applied && result.issues.length > 0) throw new Error(refusalMessage(result));
    return result.applied;
  };

  /** Remove a segment, link rule or routing domain at once, as one accepted
   *  revision, and close its window. A refusal changes nothing and is shown in
   *  the window. */
  const removeMember = async (target: EditorTarget, address: BuilderMemberAddress) => {
    const refusal = pendingEditRefusal();
    if (refusal !== null) throw new Error(refusal);
    await removeListMember(address.list_pointer, address.id);
    setSaveState({ kind: "idle" });
    closeWindow(targetKey(target));
  };
  const removeAtOnce = (target: EditorTarget, address: BuilderMemberAddress) => {
    const key = targetKey(target);
    setRemovalFailure(null);
    removeMember(target, address).catch((cause) =>
      setRemovalFailure({ key, message: cause instanceof Error ? cause.message : String(cause) }),
    );
  };

  /** Close a segment, link rule, routing domain or boundary list window. With
   *  `discard`, as Cancel and the title-bar X close it, its pending edit is
   *  dropped too; OK leaves a refused edit pending. */
  const closePendingEditWindow = (target: EditorTarget, discard: boolean) => {
    const key = targetKey(target);
    const address = pendingEditAddress(target);
    if (discard && address !== null) discardPendingEdit(address);
    setRemovalFailure((failure) => (failure?.key === key ? null : failure));
    closeWindow(key);
  };

  /** Undo the newest accepted change. A refusal changes nothing and is shown. */
  const undoLastChange = () => {
    setLibraryError(null);
    const undoOnce = async () => {
      const unsettled = unsettledReason();
      if (unsettled !== null) throw new Error(unsettled);
      const assembly = await undo();
      if (assembly === null) return;
      setSaveState({ kind: "idle" });
    };
    undoOnce().catch((cause) =>
      setLibraryError(cause instanceof Error ? cause.message : String(cause)),
    );
  };
  // Ctrl/Cmd+Z undoes the newest accepted change unless the user is typing in
  // a field (native input undo wins there). Gated on `active`: a
  // hidden-but-mounted builder must never intercept a Ctrl+Z the live view's
  // user intends for something else.
  useEffect(() => {
    if (!active) return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (!(event.ctrlKey || event.metaKey) || event.key.toLowerCase() !== "z") return;
      const target = event.target as HTMLElement | null;
      const tag = target?.tagName;
      if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") return;
      event.preventDefault();
      undoLastChange();
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [active, undoLastChange]);

  const discardCatalogDraft = useCallback(() => {
    setCatalogDraftDirty(false);
    setCatalogDraft(null);
    closeWindow("library");
  }, [closeWindow]);
  /** Discard the open component draft before a gesture replaces or closes it,
   *  asking first when it holds unsaved edits. Resolves false when the user
   *  keeps the draft. */
  const discardCatalogDraftFor = async (label: string): Promise<boolean> => {
    if (
      catalogDraft &&
      catalogDraftDirty &&
      !(await askToDiscard(`${label} discards the unsaved changes to ${catalogDraft.state.target_ref}.`))
    ) {
      return false;
    }
    discardCatalogDraft();
    return true;
  };

  // The Library's per-entry gestures. USE places a catalog reference in an
  // editable session; CUSTOMIZE opens a full backend draft at an explicit
  // user: target and persists it only when the backend save succeeds. Starting
  // an editable draft from a typed read-only open clears that preview.
  const clearReadOnlyWorldBeforeCreate = () => {
    if (world && !visualDraft) clear();
  };

  const handleLibraryUse = (entry: CatalogDocumentSummary) => {
    setLibraryError(null);
    const entryId = (entry.ref.split("/").pop() ?? entry.ref).replace(/\.ya?ml$/, "");
    const label = `using ${entry.display_name ?? entryId}`;
    const name = entry.display_name ?? entryId;
    if (entry.family === "constellations" || entry.family === "space-node-sets") {
      // No editor exists for a placed reference, so reveal its outline row
      // after placement.
      ensureThenCreate(() => {
        clearReadOnlyWorldBeforeCreate();
        void runCommand({
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
        void runCommand({
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
        void runCommand({
          operation: "add_generated_space",
          phasing_mode: authoring.default_phasing_mode,
          node_ref: entry.ref,
        })
          .then((result) => {
            openEditor({ kind: "segment", id: result.affected_id });
          })
          .catch((cause) =>
            setLibraryError(cause instanceof Error ? cause.message : String(cause)),
          );
      }, label);
    } else if (entry.family === "sites") {
      ensureThenCreate(() => {
        clearReadOnlyWorldBeforeCreate();
        void (async () => {
          // The site joins the last ground segment whose site set the session
          // proposes, or a new ground segment.
          const proposedGround = sessionSegments(visualDraft?.applied_session).filter((segment) => {
            const placement = segment.segment.placement;
            const siteSetRef = isJsonObject(placement) ? placement.from_site_set : null;
            return (visualDraft?.catalog_documents ?? []).some(
              (proposal) => proposal.ref === siteSetRef,
            );
          });
          const ground = proposedGround[proposedGround.length - 1] ?? null;
          const result = await runCommand({
            operation: "add_ground_site_reference",
            ...(ground ? { segment_id: ground.segment_id } : {}),
            site_ref: entry.ref,
          });
          openEditor({ kind: "segment", id: result.affected_id });
        })().catch((cause) =>
          setLibraryError(cause instanceof Error ? cause.message : String(cause)),
        );
      }, label);
    } else {
      setLibraryError(`cannot use "${name}": unsupported family "${entry.family}"`);
    }
  };

  const handleLibraryCustomize = async (
    entry: CatalogDocumentSummary,
    targetRef: string,
  ): Promise<boolean> => {
    setLibraryError(null);
    if (catalogDraft?.state.target_ref === targetRef) {
      openEditor({ kind: "library" });
      return true;
    }
    if (!(await discardCatalogDraftFor(`opening ${targetRef}`))) return false;
    try {
      const draft = await openCatalogDraft({
        source_ref: entry.ref,
        target_ref: targetRef,
      });
      setCatalogDraft(draft);
      openEditor({ kind: "library" });
      return true;
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

  const handleLibraryNew = async (family: string, objectId: string): Promise<boolean> => {
    setLibraryError(null);
    const targetRef = `user:${family}/${objectId}.yaml`;
    if (catalogDraft?.state.target_ref === targetRef) {
      openEditor({ kind: "library" });
      return true;
    }
    if (!(await discardCatalogDraftFor(`creating ${targetRef}`))) return false;
    try {
      const draft = await createCatalogDraft({
        family: family as CatalogComponentFamily,
        object_id: objectId,
      });
      setCatalogDraft(draft);
      openEditor({ kind: "library" });
      return true;
    } catch (cause) {
      setLibraryError(cause instanceof Error ? cause.message : String(cause));
      throw cause;
    }
  };
  const openRule = (ruleId: string) => {
    openEditor({ kind: "link", id: ruleId });
  };
  // The tree-row connect gesture: a spline IconButton (the app's link
  // glyph) opens a readable target list under the row — the port-picker
  // idiom. A select posing as the control failed twice: wide it read as a
  // blank broken field, narrow it read as glyph soup.
  const [connectFor, setConnectFor] = useState<string | null>(null);
  const connectTargets = (selfId: string) =>
    draftSegments.map((segment) => ({
      value: segment.segment_id,
      label:
        segment.segment_id === selfId
          ? `${segment.label} (mesh)`
          : `${segment.label} (${segment.kind})`,
    }));
  const connect = (fromSegmentId: string, targetSegmentId: string) => {
    setConnectFor(null);
    setLibraryError(null);
    void runCommand({
      operation: "connect_segments",
      from_segment_id: fromSegmentId,
      to_segment_id: targetSegmentId,
    })
      .then((result) => openEditor({ kind: "link", id: result.affected_id }))
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
   *  exists (undo, removal); the window simply doesn't render. `close`
   *  replaces the plain close of the title-bar X and Escape for a window
   *  whose closing can discard work. */
  function renderWindow(
    target: EditorTarget,
  ): { title: string; content: React.ReactNode; close?: () => void } | null {
    const standalone = [
      "inspect",
      "node-view",
      "open-session",
      "source-yaml",
      "customize-chain",
      "proposal",
      "catalog",
      "library",
    ].includes(target.kind);
    if (!visualDraft && !standalone) return null;
    if (
      !authoring &&
      ["segment", "new-ground", "link", "domain", "boundaries", "library", "proposal"].includes(
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
        const accepted = visualDraft?.applied_session;
        if (!accepted) return null;
        const sections = ["/session", "/time", "/simulation"] as const;
        const sectionOf = (pointer: (typeof sections)[number]) => {
          const value = pendingEdits.get(pointer)?.edit.working ?? accepted[pointer.slice(1)];
          return value !== undefined && value !== null && typeof value === "object" && !Array.isArray(value)
            ? (value as Readonly<Record<string, JsonValue>>)
            : null;
        };
        const pendingSections = sections.filter((pointer) => pendingEdits.has(pointer));
        const meta = sectionOf("/session") ?? {};
        const name = typeof meta.name === "string" ? meta.name : "";
        const report = (cause: unknown) => setSaveState({ kind: "failed", message: cause instanceof Error ? cause.message : String(cause) });
        return {
          title: `Session · ${name}`,
          content: (
            <SessionEditor
              name={name}
              meta={meta}
              time={sectionOf("/time") ?? {}}
              simulation={sectionOf("/simulation")}
              pending={pendingSections.length > 0}
              onStage={(pointer, working) => {
                try {
                  stageEdit({ kind: "section", pointer }, working);
                } catch (cause) {
                  report(cause);
                }
              }}
              onApply={() => void applyEdits(pendingSections).catch(report)}
              onDiscard={() =>
                pendingSections.forEach((pointer) => discardPendingEdit({ kind: "section", pointer }))
              }
            />
          ),
        };
      }
      case "segment": {
        const appliedRevision = visualDraft?.applied_revision ?? null;
        if (!visualDraft || appliedRevision === null) return null;
        const address = { kind: "member", list_pointer: "/segments", id: target.id } as const;
        const held = pendingEdits.get(pendingEditKey(address));
        // The segment as its pending edit holds it, or as the session holds it.
        const segment =
          held !== undefined
            ? sessionSegment(held.edit.working)
            : (draftSegments.find((candidate) => candidate.segment_id === target.id) ?? null);
        if (!segment) return null;
        const segWall = wallFor(target);
        const proposals = visualDraft.catalog_documents ?? [];
        const fields = (
          <PendingObjectFields
            pending={held?.answer ?? null}
            appliedRevision={appliedRevision}
            describe={() => describePendingObject(address)}
            onEdit={(change) => editPendingObject(address, change)}
          />
        );
        const customize = (rootRef: unknown) => {
          if (typeof rootRef !== "string") return;
          openEditor({ kind: "customize-chain", segmentId: segment.segment_id, rootRef });
        };
        const onRemove = writer !== null ? null : () => removeAtOnce(target, address);
        const placement = segment.segment.placement;
        return {
          title: held === undefined ? segment.label : `${segment.label} •`,
          close: () => closePendingEditWindow(target, true),
          content: (
            <>
              {segWall && (
                <div className="builder-wall" data-testid="wall-banner">
                  {segWall}
                </div>
              )}
              {segment.kind === "space" ? (
                <ConstellationEditor
                  key={segment.segment_id}
                  authoring={authoring!}
                  segment={segment}
                  fields={fields}
                  proposals={proposals}
                  onSetPopulation={async (change) => {
                    await runCommand({
                      operation: "set_space_population",
                      segment_id: segment.segment_id,
                      ...change,
                    });
                  }}
                  onEdit={(ref) => openEditor({ kind: "proposal", ref })}
                  onCustomize={() => customize(segment.segment.source)}
                  onCustomizeNode={async (nodeRef) => {
                    // Fork the node into a document the session proposes, as
                    // one accepted revision.
                    refuseUnlessSettled();
                    const result = await customizeChain({
                      segment_id: segment.segment_id,
                      leaf_ref: nodeRef,
                    });
                    if (!result.applied) {
                      throw new Error(result.issues.map((issue) => issue.message).join("; "));
                    }
                    setSaveState({ kind: "idle" });
                  }}
                  onRemove={onRemove}
                  placed={draftSegments}
                  linkRules={listed.linkRules}
                  onOpenRule={openRule}
                  onConnect={(other) => connect(segment.segment_id, other)}
                />
              ) : (
                <GroundEditor
                  key={segment.segment_id}
                  authoring={authoring!}
                  proposals={proposals}
                  ground={{
                    segment,
                    fields,
                    onCustomize: () =>
                      customize(isJsonObject(placement) ? placement.from_site_set : null),
                    schedulingPresets,
                    selectedSchedulingPreset:
                      schedulingSelections[schedulingSelectionKey(segment.segment_id)] ?? null,
                    siteSchedulingPreset: (siteId) =>
                      schedulingSelections[schedulingSelectionKey(segment.segment_id, siteId)] ??
                      null,
                    onSchedulingPreset: async (preset, siteId) => {
                      await runCommand({
                        operation: "set_scheduling_preset",
                        segment_id: segment.segment_id,
                        preset,
                        ...(siteId ? { site_id: siteId } : {}),
                      });
                    },
                    onRemove,
                    placed: draftSegments,
                    linkRules: listed.linkRules,
                    onOpenRule: openRule,
                    onConnect: (other) => connect(segment.segment_id, other),
                  }}
                  onMintSites={async (sites, nodeRef, bodyRef) => {
                    await runCommand({
                      operation: "mint_ground_members",
                      segment_id: segment.segment_id,
                      sites,
                      node_ref: nodeRef,
                      body_ref: bodyRef,
                    });
                  }}
                  onAddSiteReference={async (ref) => {
                    await runCommand({
                      operation: "add_ground_site_reference",
                      segment_id: segment.segment_id,
                      site_ref: ref,
                    });
                  }}
                  onEdit={(ref) => openEditor({ kind: "proposal", ref })}
                />
              )}
            </>
          ),
        };
      }
      case "new-ground": {
        if (!visualDraft) return null;
        // The first sites create the segment; its own window then edits it.
        const created = (result: BuilderVisualDraftCommandResult) => {
          closeWindow(targetKey(target));
          openEditor({ kind: "segment", id: result.affected_id });
        };
        return {
          title: "New ground segment",
          content: (
            <GroundEditor
              authoring={authoring!}
              ground={null}
              proposals={visualDraft.catalog_documents ?? []}
              onMintSites={async (sites, nodeRef, bodyRef) => {
                created(
                  await runCommand({
                    operation: "mint_ground_members",
                    sites,
                    node_ref: nodeRef,
                    body_ref: bodyRef,
                  }),
                );
              }}
              onAddSiteReference={async (ref) => {
                created(await runCommand({ operation: "add_ground_site_reference", site_ref: ref }));
              }}
              onEdit={(ref) => openEditor({ kind: "proposal", ref })}
            />
          ),
        };
      }
      case "link": {
        const rule = listed.linkRules.find((candidate) => candidate.id === target.id);
        const appliedRevision = visualDraft?.applied_revision ?? null;
        if (!rule || appliedRevision === null) return null;
        const address = { kind: "member", list_pointer: "/link_rules", id: rule.id } as const;
        const ruleWall = wallFor(target);
        return {
          title: rule.pending === null ? rule.id : `${rule.id} •`,
          close: () => closePendingEditWindow(target, true),
          content: (
            <>
            {ruleWall && (
              <div className="builder-wall" data-testid="wall-banner">
                {ruleWall}
              </div>
            )}
            <LinkRuleWindow
              authoring={authoring!}
              key={rule.id}
              listed={rule}
              appliedRevision={appliedRevision}
              onEdit={(change) => editPendingObject(address, change)}
              onDescribe={() => describePendingObject(address)}
              onHold={() => holdObjectCheck(address)}
              checking={checkingObjects.has(pendingEditKey(address))}
              placed={draftSegments}
              segments={world?.segments ?? null}
              allocation={world?.allocations.find((a) => a.rule_id === rule.id) ?? null}
              onRepoint={async (side, newSegmentId) => {
                // The rule's pending edit goes with the command, which
                // accepts both as one revision or returns them as one edit.
                const result = await runCommand({
                  operation: "rederive_link",
                  rule_id: rule.id,
                  side,
                  segment_id: newSegmentId,
                });
                return result.notice ?? "link physics rederived by VS-API";
              }}
              onRemove={writer !== null ? null : () => removeAtOnce(target, address)}
            />
            </>
          ),
        };
      }
      case "domain": {
        const domain = listed.routingDomains.find((candidate) => candidate.id === target.id);
        const appliedRevision = visualDraft?.applied_revision ?? null;
        if (!domain || appliedRevision === null) return null;
        const address = {
          kind: "member",
          list_pointer: "/routing/domains",
          id: domain.id,
        } as const;
        const domainWall = wallFor(target);
        return {
          title: domain.pending === null ? domain.id : `${domain.id} •`,
          close: () => closePendingEditWindow(target, true),
          content: (
            <>
            {domainWall && (
              <div className="builder-wall" data-testid="wall-banner">
                {domainWall}
              </div>
            )}
            <RoutingDomainWindow
              authoring={authoring!}
              key={domain.id}
              listed={domain}
              appliedRevision={appliedRevision}
              onEdit={(change) => editPendingObject(address, change)}
              onDescribe={() => describePendingObject(address)}
              onHold={() => holdObjectCheck(address)}
              checking={checkingObjects.has(pendingEditKey(address))}
              placed={draftSegments}
              onRemove={writer !== null ? null : () => removeAtOnce(target, address)}
            />
            </>
          ),
        };
      }
      case "boundaries": {
        const appliedRevision = visualDraft?.applied_revision ?? null;
        if (appliedRevision === null) return null;
        const address = { kind: "whole_list", pointer: "/routing/boundaries" } as const;
        return {
          title: listed.boundaries.pending === null ? "Boundaries" : "Boundaries •",
          close: () => closePendingEditWindow(target, true),
          content: (
            <BoundaryListWindow
              authoring={authoring!}
              listed={listed.boundaries}
              linkRules={listed.linkRules}
              routingDomains={listed.routingDomains}
              appliedRevision={appliedRevision}
              onEdit={(change) => editPendingObject(address, change)}
              onDescribe={() => describePendingObject(address)}
              onHold={() => holdObjectCheck(address)}
              checking={checkingObjects.has(pendingEditKey(address))}
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
        const owner = draftSegments.find((candidate) => candidate.segment_id === node.segment_id);
        return {
          title: node.node_id,
          content: (
            <div className="builder-inspector-stack">
              <BuilderInspector node={node} ephemeris={world.ephemeris} />
              {owner && (
                <Button onClick={() => openEditor({ kind: "segment", id: owner.segment_id })}>
                  Edit {owner.label}
                </Button>
              )}
            </div>
          ),
        };
      }
      case "source-yaml": {
        return null;
      }
      case "proposal": {
        const close = () => closeWindow(targetKey(target));
        return {
          title: `Proposed ${target.ref.slice(target.ref.lastIndexOf("/") + 1)}`,
          content: (
            <ProposalWindow
              proposalRef={target.ref}
              authoring={authoring!}
              session={{
                context: proposalSession,
                onStage: (working) => stageEdit({ kind: "proposal", ref: target.ref }, working),
              }}
              onClose={close}
            />
          ),
          close,
        };
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
              proposedRefs={new Set((visualDraft?.catalog_documents ?? []).map((item) => item.ref))}
              onEdit={(ref) => {
                closeWindow(targetKey(target));
                openEditor({ kind: "proposal", ref });
              }}
              onCustomize={async (leafRef, targetLeafRef) => {
                refuseUnlessSettled();
                const result = await customizeChain({
                  segment_id: target.segmentId,
                  leaf_ref: leafRef,
                  ...(targetLeafRef ? { target_leaf_ref: targetLeafRef } : {}),
                });
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
        // never parses persisted session grammar.
        const openEntry = (entry: CatalogDocumentSummary, targetRef?: string) => {
          setOpenSessionError(null);
          // Opening replaces the current draft; unsaved work is confirmed first.
          displace(() => {
            void openSession(entry, targetRef as SessionRef | undefined).then((result) => {
              if (!result.ok) {
                setOpenSessionError(result.reason);
                return;
              }
              markClean(result.draft);
              closeSessionWindows();
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
          !!visualDraft?.applied_session &&
          !loading &&
          assemblyResult?.visual_draft.draft_revision === visualDraft?.draft_revision &&
          saveState.kind !== "save-committed-unverified" &&
          compileResult?.save_verdict.allowed === true;
        const saveBlocker =
          compileResult?.save_verdict.blockers?.[0]?.message ?? error;
        return {
          title: "Save session",
          content: visualDraft?.applied_session ? (
            <SaveSessionDialog
              // Remount per session identity: the buffered name follows the
              // session being saved, not a previous one.
              key={visualDraft.target_ref}
              sessionName={refStem(visualDraft.target_ref)}
              canSave={canSave}
              blockedReason={saveBlocker}
              saveState={saveState}
              unappliedEdits={pendingEditCount}
              staleList={stalePendingEdits}
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
          (entry) => entry.family === catalogDraft.state.family,
        );
        if (!metadata) {
          return {
            title: "Catalog draft",
            content: <div className="builder-warning">Catalog family metadata is unavailable.</div>,
          };
        }
        const verb =
          catalogDraft.state.source_ref && catalogDraft.state.source_ref !== catalogDraft.state.target_ref
            ? "Customize"
            : catalogDraft.state.expected_target_revision
              ? "Edit"
              : "New";
        const objectKind = metadata.wrapper?.replace(/_/g, " ") ?? catalogDraft.state.family;
        const close = () => void discardCatalogDraftFor(`closing ${catalogDraft.state.target_ref}`);
        return {
          title: `${verb} ${objectKind}`,
          content: (
            <CatalogDraftEditorWindow
              authoring={authoring!}
              key={catalogDraft.state.target_ref}
              initialDraft={catalogDraft}
              onSaved={async (result) => {
                setCatalogDraft(result.draft);
                await announceCatalogDraftSaved(result);
              }}
              onDirtyChange={setCatalogDraftDirty}
              onDiscard={discardCatalogDraft}
              onClose={close}
            />
          ),
          close,
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

  const { snapshot, snapshotArrivalMs, snapshotError } = useMemo((): {
    snapshot: SceneSnapshot | null;
    snapshotArrivalMs: number | null;
    snapshotError: string | null;
  } => {
    if (!world) return { snapshot: null, snapshotArrivalMs: null, snapshotError: null };
    try {
      return {
        snapshot: builderSnapshotFromWorld(world),
        snapshotArrivalMs: performance.now(),
        snapshotError: null,
      };
    } catch (e) {
      return {
        snapshot: null,
        snapshotArrivalMs: null,
        snapshotError: e instanceof Error ? e.message : String(e),
      };
    }
  }, [world]);
  const regimeById = useMemo(() => buildRegimeIndex(world?.ephemeris ?? null), [world]);
  const areaColoring = useMemo(
    () => buildAreaColoring(snapshot?.nodes ?? [], areaInstanceId),
    [snapshot, areaInstanceId],
  );

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
  const darkRules = candidates ? candidates.darkRules : 0;
  const ruleNotes = candidates
    ? [...candidates.notes]
        .filter(([, note]) => note !== null)
        .map(([ruleId, note]) => `${ruleId}: ${note}`)
        .join("\n")
    : "";
  const assemblySettled =
    visualDraft !== null &&
    assemblyResult?.visual_draft.draft_revision === visualDraft.draft_revision;
  const hasSavableDraft = !!visualDraft?.applied_session;

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
            label="New session — blank sheet"
            onClick={() => {
              startNewSession();
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
          <RecordHistoryToggle
            className="builder-toolbar-toggle"
            checked={recordHistory}
            onChange={setRecordHistory}
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
                  record_history: recordHistory,
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
        </span>
        <IconButton
          className="builder-toolbar-btn"
          icon="library"
          size={17}
          label="Library — every block you could build with, shipped and yours"
          onClick={() => openEditor({ kind: "catalog" })}
        />
      </div>
      <div className="builder-outline" data-testid="builder-outline" inert={writer !== null}>
        <div className="builder-zone-title">World</div>
        {openedSession && visualDraft && (
          <div className="builder-warning" data-testid="opened-session-lossless">
            {openedSession.display_name} is open as a synchronized graphical and YAML draft.
            VS-API owns parsing, canonicalization, component-chain forking, and save assembly.
          </div>
        )}
        {visualDraft && (nodeCatalog.error || terminalCatalog.error) && (
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
        {visualDraft && (
          <BuildGuide
            compiled={compileResult}
            refusal={resolveError?.error ?? null}
            hasAcceptedSession={!!visualDraft?.applied_session}
            placeholderName={
              visualDraft?.session_name_is_placeholder
                ? visualDraft.target_ref
                    .slice(visualDraft.target_ref.lastIndexOf("/") + 1)
                    .replace(/\.ya?ml$/, "")
                : null
            }
            saved={
              saveState.kind === "saved" ||
              saveState.kind === "deploying" ||
              saveState.kind === "deploy-accepted"
                ? { name: saveState.name, deployRefusal: deployGate.ok ? null : deployGate.reason }
                : null
            }
            onAddConstellation={() => {
              ensureThenCreate(() => {
                setLibraryError(null);
                if (!authoring) {
                  setLibraryError("Builder authoring facts are unavailable");
                  return;
                }
                void runCommand({
                  operation: "add_generated_space",
                  phasing_mode: authoring.default_phasing_mode,
                })
                  .then((result) => {
                    openEditor({ kind: "segment", id: result.affected_id });
                  })
                  .catch((cause) =>
                    setLibraryError(cause instanceof Error ? cause.message : String(cause)),
                  );
              }, "adding a constellation");
            }}
            onAddGround={() => {
              ensureThenCreate(() => {
                setLibraryError(null);
                openEditor({ kind: "new-ground" });
              }, "adding a ground segment");
            }}
            onAddDomain={() => {
              setLibraryError(null);
              void runCommand({ operation: "add_routing_domain" })
                .then((result) => openEditor({ kind: "domain", id: result.affected_id }))
                .catch((cause) =>
                  setLibraryError(cause instanceof Error ? cause.message : String(cause)),
                );
            }}
            onOpenSession={() => openEditor({ kind: "session" })}
            onOpenSegment={(id) => openEditor({ kind: "segment", id })}
          />
        )}
        {visualDraft && (
          <div className="builder-outline-group" data-testid="builder-drafts">
            <div className="builder-library-entry">
              <span className="builder-outline-kind">
                Drafts · {refStem(visualDraft.target_ref)}
              </span>
              <span className="builder-library-actions">
                <span className="builder-outline-count">{sessionTimeRate}</span>
                <IconButton
                  icon="settings"
                  size={13}
                  label="Session settings — name, time, candidate budget"
                  onClick={() => openEditor({ kind: "session" })}
                />
              </span>
            </div>
            {draftSegments.map((segment) => (
              <Fragment key={segment.segment_id}>
              <div
                className={`builder-library-entry${
                  revealedSegment === segment.segment_id ? " builder-outline-row--revealed" : ""
                }`}
                data-segment-id={segment.segment_id}
              >
                <button
                  className={`builder-outline-row builder-outline-row--segment${
                    isOpen(`segment:${segment.segment_id}`) ? " builder-outline-row--selected" : ""
                  }`}
                  onClick={() => openEditor({ kind: "segment", id: segment.segment_id })}
                  title={`Edit ${segment.label}`}
                >
                  <span className={`builder-outline-name builder-outline-name--${segment.kind}`}>
                    <Icon name={segment.kind === "space" ? "orbit" : "satellite-dish"} size={12} />
                    {segment.label}
                  </span>
                </button>
                <span className="builder-library-actions">
                  {connectButton(segment.segment_id, segment.label)}
                  <IconButton
                    icon="x"
                    size={12}
                    label="Remove from the session"
                    disabled={writer !== null}
                    onClick={() => {
                      setLibraryError(null);
                      removeMember(
                        { kind: "segment", id: segment.segment_id },
                        { kind: "member", list_pointer: "/segments", id: segment.segment_id },
                      ).catch((cause) =>
                        setLibraryError(cause instanceof Error ? cause.message : String(cause)),
                      );
                    }}
                  />
                </span>
              </div>
              {connectPicker(segment.segment_id)}
              </Fragment>
            ))}
            <LinkAndRoutingOutline
              listed={listed}
              hasSegments={draftSegments.length > 0}
              resolvedNodes={world?.nodes ?? null}
              refusal={resolveError?.error ?? null}
              isOpen={isOpen}
              onOpen={openEditor}
            />
            {draftSegments.length > 0 && (
              <div className="builder-preset-row">
                <Button
                  title="A protocol over member segments — seeds over everything"
                  onClick={() => {
                    setLibraryError(null);
                    void runCommand({ operation: "add_routing_domain" })
                      .then((result) =>
                        openEditor({ kind: "domain", id: result.affected_id }),
                      )
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
                  title="A controlled exchange over a fixed link rule between two domains"
                  onClick={() => {
                    setLibraryError(null);
                    void runCommand({ operation: "add_boundary" })
                      .then(() => openEditor({ kind: "boundaries" }))
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
            <Button
              onClick={() => {
                ensureThenCreate(() => {
                  setLibraryError(null);
                  if (!authoring) {
                    setLibraryError("Builder authoring facts are unavailable");
                    return;
                  }
                  void runCommand({
                    operation: "add_generated_space",
                    phasing_mode: authoring.default_phasing_mode,
                  })
                    .then((result) => {
                      openEditor({ kind: "segment", id: result.affected_id });
                    })
                    .catch((cause) =>
                      setLibraryError(cause instanceof Error ? cause.message : String(cause)),
                    );
                }, "adding a constellation");
              }}
            >
              + Add constellation
            </Button>
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
            {visualDraft?.applied_session
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
          <>
          <VisualizationErrorBoundary onError={() => {}}>
            <Scene
              snapshot={snapshot}
              snapshotArrivalMs={snapshotArrivalMs}
              ephemeris={world.ephemeris}
              bodySamples={world.body_samples}
              colorMode={colorMode}
              globeMode={globeMode}
              referenceFrame={referenceFrame}
              playbackPaused={true}
              showIslLinks={showIslLinks}
              showGroundLinks={showGroundLinks}
              showSatPaths={showSatPaths}
              showGroundTracks={showGroundTracks}
              regimeById={regimeById}
              areaColoring={areaColoring}
              showTrails={showTrails}
              selection={selection}
              onSelect={(next) => {
                setSelection(next);
                if (next) openEditor({ kind: "node-view", nodeId: next.id });
              }}
              actionsRef={actionsRef}
              liveExplain={false}
              worldLayers={<CandidateLines pairs={visiblePairs} />}
            />
          </VisualizationErrorBoundary>
          {colorMode === "area" && (
            <AreaLegend coloring={areaColoring} onSelectInstance={onSelectAreaInstance} />
          )}
          </>
        ) : snapshotError ? (
          <div className="builder-zone-empty">{snapshotError}</div>
        ) : visualDraft ? (
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
                startNewSession();
              }}
            >
              <Icon name="file-plus" size={13} /> New session
            </Button>
            <div className="builder-zone-empty">
              The toolbar above holds the session verbs — New, Open, Save, Deploy,
              Library. Every step round-trips through the shared resolver;
              the YAML pane shows the session document live.
            </div>
          </div>
        )}
      </div>
      <div className="builder-windows" data-testid="builder-windows">
        {windows.map((win) => {
          const body = renderWindow(win.target);
          if (!body) return null;
          // The pending edit this window edits, by its address and the key it is
          // held under; null for a window that edits none.
          const address = pendingEditAddress(win.target);
          const edit = address === null ? null : { address, key: pendingEditKey(address) };
          const lockReason = edit !== null ? pendingEditRefusal() : null;
          return (
            <FloatingWindow
              key={win.key}
              raiseId={win.key}
              title={body.title}
              onClose={body.close ?? (() => closeWindow(win.key))}
              initial={{ x: win.x, y: win.y, w: 380, h: 560 }}
              minWidth={320}
              minHeight={240}
            >
              <div className="builder-window-body" inert={lockReason !== null}>
                {lockReason !== null && <div className="builder-warning">{lockReason}</div>}
                {body.content}
              </div>
              {edit !== null && (
                <>
                  {removalFailure?.key === win.key && (
                    <div className="builder-warning">{removalFailure.message}</div>
                  )}
                  <PendingEditApplyRow
                    pending={pendingEdits.has(edit.key)}
                    outcome={pendingEditOutcomes.get(edit.key) ?? null}
                    blocked={lockReason}
                    checking={checkingObjects.has(edit.key)}
                    onApply={() => applyWindowEdit(edit.key, false)}
                    onReapply={() => applyWindowEdit(edit.key, true)}
                    onDiscard={() => discardPendingEdit(edit.address)}
                    onClose={() => closePendingEditWindow(win.target, false)}
                  />
                </>
              )}
            </FloatingWindow>
          );
        })}
      </div>
      <div className="builder-inspector" data-testid="builder-yaml">
        <div className="builder-zone-title">Session YAML</div>
        {visualDraft?.control_tree?.root && (
          <details className="builder-generic-controls" data-testid="session-generic-controls">
            <summary>Additional graphical fields</summary>
            {unsettledReason() !== null && (
              <div className="builder-zone-empty">{unsettledReason()}</div>
            )}
            {pendingEditCount > 0 && (
              <div className="builder-zone-empty">
                Apply or discard the {pendingEditsPhrase(pendingEditCount)} before editing these
                fields.
              </div>
            )}
            <GraphicalControlTreeEditor
              tree={visualDraft.control_tree}
              disabled={
                yamlBlocksGui ||
                pendingEditCount > 0 ||
                unsettledReason() !== null
              }
              hideSpecialized
              onMutate={async (commands: ReadonlyArray<BuilderControlMutation>) => {
                setLibraryError(null);
                try {
                  await mutateControls(commands);
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
              className={`builder-yaml-revision${yamlHasErrors ? " builder-yaml-revision--error" : ""}`}
              data-testid="builder-yaml-revision"
            >
              {visualDraft.applied_revision !== null && visualDraft.applied_revision !== undefined
                ? `Showing applied revision ${visualDraft.applied_revision}; buffer generation ${yamlBuffer.generation}${
                    yamlHasErrors ? " has errors" : ""
                  }`
                : `Buffer generation ${yamlBuffer.generation} has no valid graphical projection`}
            </div>
            {pendingEditCount > 0 && (
              <div className="builder-site-derived">
                Apply or discard the {pendingEditsPhrase(pendingEditCount)} before editing YAML.
              </div>
            )}
            <PasteArea
              className="builder-yaml-editor"
              value={yamlBuffer.text}
              disabled={pendingEditCount > 0 || (writer !== null && writer !== "yaml")}
              onChange={editYamlBuffer}
              ariaLabel="Session YAML"
              rows={32}
            />
            <div className="builder-preset-row">
              <Button
                disabled={
                  pendingEditCount > 0 ||
                  writer !== null ||
                  (!yamlBuffer.dirty && yamlBuffer.applied)
                }
                // A failed apply is held in the buffer state, which this pane shows.
                onClick={() => void applySessionYaml(yamlBuffer.generation).catch(() => undefined)}
              >
                {writer === "yaml" ? "Applying…" : "Apply YAML"}
              </Button>
              {yamlBuffer.canonicalizationRequired && (
                <Button
                  variant="primary"
                  disabled={writer !== null || pendingEditCount > 0}
                  onClick={() => void useCanonicalSessionYaml()}
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
                    `${world?.session.name ?? (visualDraft ? refStem(visualDraft.target_ref) : "session")}.yaml`,
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
            {yamlBuffer.applyFailure !== null && (
              <div className="builder-warning" data-testid="builder-yaml-apply-failure">
                The YAML was not applied: {yamlBuffer.applyFailure}
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
      {visualDraft &&
        ((wall && !isOpen(wall.key)) || pendingEditFindings.length > 0) && (
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
          {pendingEditFindings.map((finding) => (
            <button
              key={finding.key}
              className="builder-rail-chip"
              title="Open the pending edit"
              onClick={() => openEditor(finding.target)}
            >
              {finding.message}
            </button>
          ))}
        </div>
      )}
      <div className="builder-status" data-testid="builder-status">
        <span className="builder-mode-badge">Session Builder</span>
        {pendingEditCount > 0 && (
          <span className="builder-preview-chip" data-testid="builder-preview-chip">
            previewing{" "}
            {pendingEditCount === 1 ? "the pending edit" : pendingEditsPhrase(pendingEditCount)}
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
            {visualDraft
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
        {draftError && (
          <span className="builder-status-item builder-status-item--error">{draftError}</span>
        )}
      </div>
      {pendingDisplace && (
        <div
          data-testid="builder-discard-choice"
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
            <div style={{ fontWeight: 600 }}>Discard unsaved changes?</div>
            <div style={{ fontSize: "13px", opacity: 0.85 }}>
              {pendingDisplace.message} The Builder keeps no copy of unsaved work.
            </div>
            <div style={{ display: "flex", gap: "8px", justifyContent: "flex-end" }}>
              <Button
                variant="primary"
                onClick={() => {
                  setPendingDisplace(null);
                  pendingDisplace.answer(true);
                }}
              >
                Discard and continue
              </Button>
              <Button
                onClick={() => {
                  setPendingDisplace(null);
                  pendingDisplace.answer(false);
                }}
              >
                Cancel
              </Button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
