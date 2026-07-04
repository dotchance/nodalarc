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
 *  Authoring: client-side drafts + library refs, serialized through the ONE
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
import { builderSnapshotFromWorld } from "./builderSnapshot";
import { CandidateLines } from "./CandidateLines";
import { computeCandidates } from "./candidates";
import { EditorApplyRow } from "./editorKit";
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
  readCatalogObject,
  saveUserObject,
  useBuilderCatalog,
  useBuilderWorld,
} from "./useBuilderWorld";
import { useWorkspace } from "./useWorkspace";
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
  type Workspace,
} from "./workspace";
import type {
  BuilderCatalogEntry,
  BuilderSessionListEntry,
  BuilderWorld,
} from "./builderTypes";

interface BuilderViewProps {
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

/** A floating editor window's target — one per object, keyed. */
type EditorTarget =
  | { kind: "session" }
  | { kind: "segment"; id: string }
  | { kind: "ground"; id: string }
  | { kind: "link"; id: string }
  | { kind: "domain"; id: string }
  | { kind: "boundary"; id: string }
  | { kind: "inspect"; ref: string; document: Record<string, unknown> }
  | { kind: "node-view"; nodeId: string }
  | { kind: "library" }
  | { kind: "catalog" };

interface EditorWindow {
  key: string;
  target: EditorTarget;
  x: number;
  y: number;
}

/** One-line form of a refusal for the compact surfaces (canvas note,
 *  status bar) — the OWNING window's wall carries the full text. */
function truncateError(text: string, max = 180): string {
  return text.length > max ? `${text.slice(0, max)}\u2026` : text;
}

function targetKey(target: EditorTarget): string {
  switch (target.kind) {
    case "session":
    case "library":
    case "catalog":
      return target.kind;
    case "inspect":
      return `inspect:${target.ref}`;
    case "node-view":
      return `node:${target.nodeId}`;
    default:
      return `${target.kind}:${target.id}`;
  }
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

export function BuilderView({
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
    loadSession,
    resolveDocument,
    saveSession,
    deploySession,
    clear,
  } = useBuilderWorld();
  const [selectedFile, setSelectedFile] = useState("");
  // Builder-local selection: inspect-only, never shared with the live view's
  // selection (two different worlds must not share a pointer).
  const [selection, setSelection] = useState<Selection | null>(null);
  // The authoring workspace: client-side drafts, resolve-checked on every
  // edit; the world on screen is always the resolver's expansion of it.
  const {
    workspace,
    startNew,
    openWorkspace,
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

  // --- Editing the RUNNING session ------------------------------------
  // Entering the builder beside a running session loads THAT session as
  // the workspace — rapid iteration between builder and cluster. The one
  // exception is an unsaved browser draft: autosave overwrites its slot
  // as soon as any workspace exists, so auto-importing over a draft would
  // silently destroy it — that case gets an explicit choice instead.
  const runningSession = sessions.find((s) => s.active) ?? null;
  const [importPending, setImportPending] = useState<BuilderSessionListEntry | null>(null);
  const [importIssues, setImportIssues] = useState<string[] | null>(null);
  // The file the current workspace was imported from (provenance marker).
  const [importedFrom, setImportedFrom] = useState<string | null>(null);
  const importTriedRef = useRef<string | null>(null);
  const startImport = (entry: BuilderSessionListEntry) => {
    setImportIssues(null);
    setImportPending(entry);
    loadSession(entry.file);
  };
  useEffect(() => {
    // The running session ALWAYS loads — that is what entering the builder
    // beside a running cluster means. A browser draft never silently stands
    // in for it (a stale draft wearing the running session's name showed an
    // empty world while thirty nodes ran); a displaced draft is preserved
    // to the backup slot and restorable below.
    if (workspace || importPending || !runningSession) return;
    if (importTriedRef.current === runningSession.file) return;
    importTriedRef.current = runningSession.file;
    startImport(runningSession);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [workspace, importPending, runningSession]);
  useEffect(() => {
    if (!importPending || loadedDocument === null || loadedFile !== importPending.file) return;
    if (workspace) {
      // The user started something while the load was in flight — theirs wins.
      setImportPending(null);
      return;
    }
    const result = workspaceFromSessionDocument(loadedDocument);
    if (result.workspace) {
      stashAutosaveToBackup();
      openWorkspace(result.workspace);
      setImportedFrom(importPending.file);
    } else {
      // The world/YAML stay on screen read-only; the note says why the
      // session cannot be edited — never a silently lossy workspace.
      setImportIssues(result.issues);
    }
    setImportPending(null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [importPending, loadedDocument, loadedFile, workspace]);
  useEffect(() => {
    // The import must END with its resolve: a failed fetch or a competing
    // action (clear/+ New discards the in-flight response) would otherwise
    // leave "Loading…" claimed forever — a false in-progress display — and
    // permanently disable the edit-running path for this mount.
    if (!importPending || loading) return;
    if (loadedDocument !== null && loadedFile === importPending.file) return;
    setImportPending(null);
  }, [importPending, loading, loadedDocument, loadedFile]);
  // The diagram workspace: editors are floating, anchored windows — many
  // can be open at once, keyed per object (re-open focuses, IG-4/IG-12).
  const [windows, setWindows] = useState<EditorWindow[]>([]);
  const openEditor = (target: EditorTarget) => {
    const key = targetKey(target);
    setWindows((prev) => {
      const existing = prev.find((w) => w.key === key);
      if (existing) {
        // Focus = move to the top of the stack; refresh the target payload.
        return [...prev.filter((w) => w.key !== key), { ...existing, target }];
      }
      const n = prev.length;
      return [
        ...prev,
        { key, target, x: 440 + (n % 6) * 40, y: 84 + (n % 6) * 32 },
      ];
    });
  };
  const closeWindow = (key: string) => {
    setWindows((prev) => prev.filter((w) => w.key !== key));
    setBuffers((prev) => {
      if (!(key in prev)) return prev;
      const next = { ...prev };
      delete next[key];
      return next;
    });
  };
  const isOpen = (key: string) => windows.some((w) => w.key === key);

  // Buffered editing: an editor window works on a copy of its object; the
  // session only changes on Apply/OK. Cancel and the title-bar X discard —
  // the window says which state it is in, so closing is never a guess.
  const [buffers, setBuffers] = useState<
    Record<string, { draft: unknown; opened: unknown; dirty: boolean }>
  >({});
  type SessionBuffer = Pick<
    Workspace,
    | "name"
    | "start_time"
    | "step_seconds"
    | "compression"
    | "max_pairs_per_rule"
    | "max_pairs_per_tick"
  >;
  /** First edit creates the buffer from the object as rendered ("base");
   *  later edits build on the working copy. "opened" — the Defaults target —
   *  is the object as it stood before the first edit. The session buffer is
   *  a pick, never the whole workspace: applying a stale whole-workspace
   *  clone would silently revert every other window's applied work. */
  const patchBuffer = <T,>(key: string, base: T, fn: (draft: T) => T) => {
    setBuffers((prev) => {
      const buf = prev[key];
      const current = (buf?.draft as T | undefined) ?? structuredClone(base);
      const opened = buf?.opened ?? structuredClone(base);
      return { ...prev, [key]: { draft: fn(current), opened, dirty: true } };
    });
  };
  const revertBuffer = (key: string) => {
    setBuffers((prev) => {
      const buf = prev[key];
      if (!buf) return prev;
      return {
        ...prev,
        [key]: { ...buf, draft: structuredClone(buf.opened), dirty: false },
      };
    });
  };
  const applyBuffer = (target: EditorTarget) => {
    const key = targetKey(target);
    const buf = buffers[key];
    if (!buf || !buf.dirty) return;
    switch (target.kind) {
      case "session":
        updateSession(buf.draft as SessionBuffer);
        break;
      case "segment":
        updateConstellation(target.id, buf.draft as DraftConstellation);
        break;
      case "ground":
        updateGroundDraft(target.id, buf.draft as DraftGroundSet);
        break;
      case "link":
        updateLinkRule(target.id, buf.draft as DraftLinkRule);
        break;
      case "domain":
        updateRoutingDomain(target.id, buf.draft as DraftRoutingDomain);
        break;
      case "boundary":
        updateBoundary(target.id, buf.draft as DraftBoundary);
        break;
      default:
        return;
    }
    setBuffers((prev) => {
      const cur = prev[key];
      if (!cur) return prev;
      return {
        ...prev,
        [key]: { ...cur, opened: structuredClone(cur.draft), dirty: false },
      };
    });
  };
  /** The session as the canvas should show it: the applied workspace with
   *  every dirty window's working copy substituted in. Editing previews live
   *  (drag a slider, the sats move); the workspace itself still only changes
   *  on Apply. */
  const previewWorkspace = (): Workspace | null => {
    if (!workspace) return null;
    let out = workspace;
    for (const [key, buf] of Object.entries(buffers)) {
      if (!buf.dirty) continue;
      const sep = key.indexOf(":");
      const kind = sep === -1 ? key : key.slice(0, sep);
      const id = sep === -1 ? "" : key.slice(sep + 1);
      switch (kind) {
        case "session":
          out = { ...out, ...(buf.draft as SessionBuffer) };
          break;
        case "segment":
          out = {
            ...out,
            space: out.space.map((d) =>
              d.segment_id === id ? (buf.draft as DraftConstellation) : d,
            ),
          };
          break;
        case "ground":
          out = {
            ...out,
            ground: out.ground.map((d) =>
              d.segment_id === id ? (buf.draft as DraftGroundSet) : d,
            ),
          };
          break;
        case "link":
          out = {
            ...out,
            links: out.links.map((r) =>
              r.rule_id === id ? (buf.draft as DraftLinkRule) : r,
            ),
          };
          break;
        case "domain":
          out = {
            ...out,
            routing_domains: out.routing_domains.map((d) =>
              d.domain_id === id ? (buf.draft as DraftRoutingDomain) : d,
            ),
          };
          break;
        case "boundary":
          out = {
            ...out,
            boundaries: out.boundaries.map((b) =>
              b.boundary_id === id ? (buf.draft as DraftBoundary) : b,
            ),
          };
          break;
      }
    }
    return out;
  };
  const dirtyWindows = Object.values(buffers).filter((b) => b.dirty).length;
  /** The wall's owning editor target, from the resolver's OWN scope — the
   *  serialized subject id maps to drafts via the same identifier()
   *  transform the serializer uses; never by parsing prose or runtime ids.
   *  Matched against the PREVIEW overlay: the refused document was
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
  // land here too (buffers change) and re-resolve the applied truth.
  useEffect(() => {
    if (!workspace) return;
    const hasContent =
      workspace.space.length +
        workspace.space_refs.length +
        workspace.ground.length +
        workspace.ground_refs.length >
      0;
    if (!hasContent) return;
    const timer = setTimeout(() => {
      const preview = previewWorkspace();
      if (preview) resolveDocument(toSessionDocument(preview));
    }, 300);
    return () => clearTimeout(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [workspace, buffers]);

  // Trust mechanics: Ctrl/Cmd+Z undoes the last workspace mutation unless
  // the user is typing in a field (native input undo wins there).
  useEffect(() => {
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
  }, [undo]);
  // IG-2: the object a create gesture just made — its editor focuses the
  // name once.
  const [freshId, setFreshId] = useState<string | null>(null);
  const [saveState, setSaveState] = useState<
    | { kind: "idle" }
    | { kind: "saving" }
    | { kind: "saved"; name: string; file: string }
    | { kind: "deploying"; name: string }
    | { kind: "deployed"; name: string }
    | { kind: "failed"; message: string }
  >({ kind: "idle" });
  // Standalone component authoring (Your library) — independent of sessions.
  const [libraryEditor, setLibraryEditor] = useState<
    | { kind: "terminal"; draft: DraftTerminal }
    | { kind: "node"; draft: DraftNode }
    | { kind: "site"; draft: DraftSiteObject }
    | null
  >(null);
  const [libraryNodeSave, setLibraryNodeSave] = useState<
    { kind: "idle" } | { kind: "conflict" } | { kind: "failed"; message: string }
  >({ kind: "idle" });
  const [libraryError, setLibraryError] = useState<string | null>(null);

  // The Library's per-entry gestures. USE places the block in the session
  // (self-ensuring: no open workspace starts one); EDIT forks it into an
  // editable draft; clicking the row inspects it.
  const handleLibraryUse = (entry: BuilderCatalogEntry) => {
    setLibraryError(null);
    if (entry.family === "constellations") {
      addConstellationRef(entry.ref, entry.display_name ?? entry.id ?? entry.ref);
    } else if (entry.family === "site-sets") {
      addGroundRef(entry.ref, entry.display_name ?? entry.id ?? entry.ref);
    } else if (entry.family === "nodes") {
      addConstellation(entry.ref);
    } else if (entry.family === "sites" && entry.id) {
      addGroundMember(
        refGroundMember(entry.ref, entry.id, entry.display_name ?? entry.id, entry.summary),
        () => newDraftGroundSet(defaultGroundNodeRef ?? "", {}),
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
        addDraft(draft);
        openEditor({ kind: "segment", id: draft.segment_id });
      } else if (entry.family === "site-sets") {
        const draft = await forkGroundSet(entry.ref);
        addGroundDraft(draft);
        openEditor({ kind: "ground", id: draft.segment_id });
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
      setLibraryEditor({ kind: "node", draft: defaultDraftNode() });
      openEditor({ kind: "library" });
    } else if (family === "constellations" && defaultNodeRef) {
      const draft = newDraftConstellation(defaultNodeRef);
      addDraft(draft);
      openEditor({ kind: "segment", id: draft.segment_id });
      setFreshId(draft.segment_id);
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
          addGroundDraft(draft);
          openEditor({ kind: "ground", id: draft.segment_id });
          setFreshId(draft.segment_id);
        } catch (e) {
          setLibraryError(e instanceof Error ? e.message : String(e));
        }
      })();
    }
  };
  // The connect gesture (IG-7): both endpoints known BEFORE the rule
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
              workspace={workspace}
              onOpenRule={openRule}
              onConnect={(other) => connect(draft.segment_id, other)}
              draft={draft}
              onUpdate={(patch) =>
                patchBuffer(key, applied, (d) => ({ ...d, ...patch }))
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
              onUpdate={(patch) =>
                patchBuffer(key, applied, (d) => ({ ...d, ...patch }))
              }
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
      case "catalog": {
        // The library is EVERYTHING you could use — a separate surface on
        // purpose. The rail lists only what this session IS using; the two
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
                onChange={(draft) => setLibraryEditor({ kind: "terminal", draft })}
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
                onUpdate={(patch) =>
                  setLibraryEditor({
                    kind: "site",
                    draft: { ...libraryEditor.draft, ...patch },
                  })
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
                onChange={(draft) => setLibraryEditor({ kind: "node", draft })}
              />
              <div className="builder-preset-row">
                <Button
                  variant="primary"
                  onClick={async () => {
                    try {
                      await saveUserObject(
                        "nodes",
                        { node: nodeObjectFromDraft(libraryEditor.draft) },
                        { overwrite: libraryNodeSave.kind === "conflict" },
                      );
                      setLibraryEditor(null);
                      closeWindow("library");
                      setLibraryNodeSave({ kind: "idle" });
                      void nodeCatalog.refresh();
                    } catch (e) {
                      const status = (e as Error & { status?: number }).status;
                      if (status === 409 && libraryNodeSave.kind !== "conflict") {
                        setLibraryNodeSave({ kind: "conflict" });
                      } else {
                        setLibraryNodeSave({
                          kind: "failed",
                          message: e instanceof Error ? e.message : String(e),
                        });
                      }
                    }
                  }}
                >
                  {libraryNodeSave.kind === "conflict"
                    ? "Overwrite in library?"
                    : "Save node to library"}
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
              {libraryNodeSave.kind === "failed" && (
                <div className="builder-warning">{libraryNodeSave.message}</div>
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

  // Rule-scoped LOS candidates at the epoch: permission + geometry, decided
  // in km space by the shared preview math. Toolbar link toggles gate the
  // overlay per kind, exactly as they gate the live view's link layers.
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
      <div className="builder-outline" data-testid="builder-outline">
        <div className="builder-mode-badge">Session Builder</div>
        <div className="builder-zone-title">World</div>
        {/* Building is the point; blank-first. Loading an existing session is
            the secondary path. */}
        {!workspace && (
          <>
            {runningSession && (
              <Button
                variant="primary"
                disabled={!!importPending}
                title="Load the session currently running on the cluster for editing"
                onClick={() => {
                  setSelection(null);
                  startImport(runningSession);
                }}
              >
                {importPending
                  ? "Loading…"
                  : `Edit running session — ${runningSession.name}`}
              </Button>
            )}
            <Button
              variant={runningSession ? undefined : "primary"}
              onClick={() => {
                clear();
                setSelection(null);
                setImportedFrom(null);
                setImportIssues(null);
                startNew("untitled-session");
              }}
            >
              + New session
            </Button>
            {hasAutosave() && (
              <Button
                title="Restore the autosaved draft from this browser"
                onClick={() => {
                  clear();
                  setSelection(null);
                  setImportedFrom(null);
                  setImportIssues(null);
                  restoreAutosave();
                }}
              >
                Restore draft
              </Button>
            )}
            {importIssues && (
              <div className="builder-warning" data-testid="import-issues">
                {runningSession?.name ?? "this session"} cannot be edited in the
                builder yet:
                {importIssues.map((issue) => (
                  <div key={issue}>· {issue}</div>
                ))}
              </div>
            )}
          </>
        )}
        <div className="builder-session-picker">
          <select
            aria-label="Catalog session"
            value={selectedFile}
            onChange={(e) => setSelectedFile(e.target.value)}
          >
            <option value="" disabled>
              {sessions.length ? "or load an existing session…" : "No sessions found"}
            </option>
            {sessions.map((s) => (
              <option key={s.file} value={s.file}>
                {s.name}
              </option>
            ))}
          </select>
          <Button
            disabled={!selectedFile || loading}
            onClick={() => {
              closeWorkspace();
              setWindows([]);
              setImportedFrom(null);
              setImportIssues(null);
              loadSession(selectedFile);
            }}
          >
            {loading ? "Resolving…" : "Load"}
          </Button>
        </div>
        {workspace && (
          <BuildGuide
            workspace={workspace}
            saved={saveState.kind === "saved" || saveState.kind === "deploying" || saveState.kind === "deployed" ? ("name" in saveState ? saveState.name : null) : null}
            deployed={saveState.kind === "deployed"}
            onAddConstellation={() => {
              if (!defaultNodeRef) return;
              const draft = newDraftConstellation(defaultNodeRef);
              addDraft(draft);
              openEditor({ kind: "segment", id: draft.segment_id });
              setFreshId(draft.segment_id);
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
              <div className="builder-library-entry">
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
              <div className="builder-library-entry">
                <span className="builder-outline-name builder-outline-name--ground builder-outline-row--segment">
                  <Icon name="satellite-dish" size={12} />
                  {placed.label}
                </span>
                <span className="builder-library-actions">
                  {connectButton(placed.segment_id, placed.label)}
                  <select
                    aria-label={`Scheduling for ${placed.label}`}
                    title="Scheduling intent — writes the full explicit block"
                    className="builder-ground-preset"
                    value={placed.scheduling_preset}
                    onChange={(e) =>
                      updateGroundRef(placed.segment_id, {
                        scheduling_preset: e.target.value as SchedulingPresetKey,
                      })
                    }
                  >
                    {Object.entries(SCHEDULING_PRESETS).map(([key, preset]) => (
                      <option key={key} value={key}>
                        {preset.label}
                      </option>
                    ))}
                  </select>
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
                  title="A controlled exchange over a FIXED link rule between two domains"
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
                addDraft(draft);
                openEditor({ kind: "segment", id: draft.segment_id });
                setFreshId(draft.segment_id);
              }}
            >
              + Add constellation
            </Button>
            {hasBackup() && (
              <Button
                title="Bring back the draft this workspace displaced — the running session stays re-importable"
                onClick={() => {
                  setWindows([]);
                  setImportedFrom(null);
                  setImportIssues(null);
                  restoreBackup();
                }}
              >
                Restore previous draft
              </Button>
            )}
            <Button
              variant="primary"
              disabled={!world || !!error || loading || saveState.kind === "saving"}
              title="Resolve-checked server-side, written exclusively, listed with the sessions"
              onClick={async () => {
                if (!workspace) return;
                setSaveState({ kind: "saving" });
                try {
                  const result = await saveSession(toSessionDocument(workspace));
                  setSaveState({ kind: "saved", name: result.name, file: result.file });
                } catch (e) {
                  setSaveState({
                    kind: "failed",
                    message: e instanceof Error ? e.message : String(e),
                  });
                }
              }}
            >
              {saveState.kind === "saving" ? "Saving…" : "Save session"}
            </Button>
            {dirtyWindows > 0 && (
              <div className="builder-zone-empty">
                {count(dirtyWindows, "window")} with unapplied edits — Apply to
                include them in the save
              </div>
            )}
            {saveState.kind === "saved" && (
              <Button
                variant="primary"
                title="Switch the cluster to this saved session — same path as the app's session picker"
                onClick={async () => {
                  const saved = saveState;
                  setSaveState({ kind: "deploying", name: saved.name });
                  try {
                    await deploySession(saved.file);
                    setSaveState({ kind: "deployed", name: saved.name });
                  } catch (e) {
                    setSaveState({
                      kind: "failed",
                      message: e instanceof Error ? e.message : String(e),
                    });
                  }
                }}
              >
                Deploy to cluster
              </Button>
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
        <Button
          title="Every block you could build with — shipped and yours. Opens its own window; this rail lists only what THIS session uses."
          onClick={() => openEditor({ kind: "catalog" })}
        >
          Library…
        </Button>
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
        {world && snapshot ? (
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
              ? `The session does not resolve — the canvas returns when it does. ${truncateError(resolveError.error)}`
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
                clear();
                setSelection(null);
                setImportedFrom(null);
                setImportIssues(null);
                startNew("untitled-session");
              }}
            >
              + New session
            </Button>
            <div className="builder-zone-empty">
              Every step round-trips through the real resolver; the YAML pane shows the
              artifact live.
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
                  onApply={() => applyBuffer(win.target)}
                  onOk={() => {
                    applyBuffer(win.target);
                    closeWindow(win.key);
                  }}
                  onDefaults={() => revertBuffer(win.key)}
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
              <Button onClick={() => navigator.clipboard?.writeText(documentYaml)}>
                Copy
              </Button>
              <Button
                onClick={() => {
                  const blob = new Blob([documentYaml], { type: "text/yaml" });
                  const url = URL.createObjectURL(blob);
                  const a = document.createElement("a");
                  a.href = url;
                  a.download = `${world?.session.name ?? "session"}.yaml`;
                  a.click();
                  URL.revokeObjectURL(url);
                }}
              >
                Download
              </Button>
            </div>
            <pre className="builder-yaml-body">{documentYaml}</pre>
          </>
        ) : (
          <div className="builder-zone-empty">
            The artifact appears here as soon as a draft resolves.
          </div>
        )}
      </div>
      {workspace &&
        ((wallTarget && !isOpen(wallTarget.key)) ||
          completenessFindings(workspace).length > 0) && (
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
          {completenessFindings(workspace).map((finding) => (
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
        {error ? (
          <span className="builder-status-item builder-status-item--error" title={error}>
            {truncateError(error)}
          </span>
        ) : snapshotError ? (
          <span className="builder-status-item builder-status-item--error">
            {snapshotError}
          </span>
        ) : world ? (
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
        ) : (
          <span className="builder-status-item">
            {workspace
              ? "draft — not resolved yet"
              : importPending
                ? `loading running session ${importPending.name}…`
                : runningSession
                  ? `running: ${runningSession.name} — not loaded`
                  : "no session loaded"}
          </span>
        )}
        {saveState.kind === "saved" && (
          <span className="builder-status-item">saved as {saveState.name}</span>
        )}
        {saveState.kind === "failed" && (
          <span className="builder-status-item builder-status-item--error">
            save failed: {saveState.message}
          </span>
        )}
      </div>
    </div>
  );
}
