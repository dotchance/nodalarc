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

import { useEffect, useMemo, useState, type MutableRefObject } from "react";
import { Scene } from "../globe/r3f/Scene";
import { VisualizationErrorBoundary } from "../globe/VisualizationErrorBoundary";
import { buildRegimeIndex } from "../taxonomy/regime";
import { Button, IconButton } from "../ui/Button";
import type { GlobeActions } from "../globe/actions";
import type {
  ColorMode,
  GlobeMode,
  ReferenceFrame,
  Selection,
  StateSnapshot,
} from "../types";
import { Icon } from "../ui/icons/Icon";
import { BuilderInspector } from "./BuilderInspector";
import { builderSnapshotFromWorld } from "./builderSnapshot";
import { CandidateLines } from "./CandidateLines";
import { computeCandidates } from "./candidates";
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
  draftConstellationFromDocuments,
  draftGroundSetFromDocuments,
  draftNodeFromDocument,
  draftSiteFromDocument,
  draftTerminalFromDocument,
  completenessFindings,
  defaultBoundary,
  defaultLinkRule,
  defaultRoutingDomain,
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
  type DraftGroundSet,
  type DraftNode,
  type DraftSiteObject,
  type DraftTerminal,
  type SchedulingPresetKey,
} from "./workspace";
import type { BuilderCatalogEntry } from "./builderTypes";
import type { BuilderWorld } from "./builderTypes";

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
  /** First member (sorted by node_id) — the tree row's fly-to target. */
  first_node_id: string;
}

function summarizeSegments(world: BuilderWorld): SegmentSummary[] {
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
    loading,
    error,
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
    updateSession,
    undo,
    hasAutosave,
    restoreAutosave,
    close: closeWorkspace,
    addConstellation,
    addConstellationRef,
    addDraft,
    removeRefSegment,
    removeConstellation,
    updateConstellation,
    updateOrbit,
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
    updateLinkEndpoint,
    removeLinkRule,
    addRoutingDomain,
    updateRoutingDomain,
    removeRoutingDomain,
    addBoundary,
    updateBoundary,
    removeBoundary,
  } = useWorkspace(resolveDocument);
  const nodeCatalog = useBuilderCatalog("nodes");
  const terminalCatalog = useBuilderCatalog("terminals");
  const [editingSegment, setEditingSegment] = useState<string | null>(null);
  const [editingLink, setEditingLink] = useState<string | null>(null);
  const [editingDomain, setEditingDomain] = useState<string | null>(null);
  const [editingBoundary, setEditingBoundary] = useState<string | null>(null);
  const [editingSession, setEditingSession] = useState(false);
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
  const closeEditors = () => {
    setEditingSegment(null);
    setEditingLink(null);
    setEditingDomain(null);
    setEditingBoundary(null);
    setEditingSession(false);
  };
  const [showYaml, setShowYaml] = useState(false);
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
  const [catalogInspect, setCatalogInspect] = useState<{
    ref: string;
    document: Record<string, unknown>;
  } | null>(null);
  const [libraryError, setLibraryError] = useState<string | null>(null);

  // The Library's per-entry gestures. USE places the block in the session
  // (self-ensuring: no open workspace starts one); EDIT forks it into an
  // editable draft; clicking the row inspects it.
  const handleLibraryUse = (entry: BuilderCatalogEntry) => {
    setLibraryError(null);
    setLibraryEditor(null);
    setCatalogInspect(null);
    setEditingSession(false);
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
    setCatalogInspect(null);
    setEditingSession(false);
    try {
      const { document } = await readCatalogObject(entry.ref);
      if (entry.family === "terminals") {
        const seeded = draftTerminalFromDocument(document);
        setEditingSegment(null);
        setLibraryEditor({
          kind: "terminal",
          draft: {
            ...seeded,
            id: identifier(`${seeded.id}-custom`),
            display_name: `${seeded.display_name} (custom)`,
          },
        });
      } else if (entry.family === "nodes") {
        const seeded = draftNodeFromDocument(document);
        setEditingSegment(null);
        setLibraryEditor({
          kind: "node",
          draft: {
            ...seeded,
            id: identifier(`${seeded.id}-custom`),
            display_name: `${seeded.display_name} (custom)`,
          },
        });
      } else if (entry.family === "constellations") {
        const constellation = (document as { constellation?: { orbit?: unknown } })
          .constellation;
        const orbitRef =
          typeof constellation?.orbit === "string" ? constellation.orbit : null;
        const orbitDocument = orbitRef
          ? (await readCatalogObject(orbitRef)).document
          : null;
        const draft = draftConstellationFromDocuments(document, orbitDocument);
        setLibraryEditor(null);
        addDraft(draft);
        setEditingSegment(draft.segment_id);
      } else if (entry.family === "site-sets") {
        const draft = await forkGroundSet(entry.ref);
        setLibraryEditor(null);
        addGroundDraft(draft);
        setEditingSegment(draft.segment_id);
      } else if (entry.family === "sites") {
        const seeded = draftSiteFromDocument(document);
        setEditingSegment(null);
        setLibraryEditor({
          kind: "site",
          draft: {
            ...seeded,
            site_id: identifier(`${seeded.site_id}-custom`),
            display_name: `${seeded.display_name} (custom)`,
          },
        });
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
    setEditingSession(false);
    try {
      const { document } = await readCatalogObject(entry.ref);
      setLibraryEditor(null);
      setEditingSegment(null);
      setCatalogInspect({ ref: entry.ref, document });
    } catch (e) {
      setLibraryError(e instanceof Error ? e.message : String(e));
    }
  };

  const handleLibraryNew = (family: string) => {
    setLibraryError(null);
    setCatalogInspect(null);
    setEditingSession(false);
    if (family === "terminals") {
      setEditingSegment(null);
      setLibraryEditor({ kind: "terminal", draft: defaultDraftTerminal() });
    } else if (family === "nodes") {
      setEditingSegment(null);
      setLibraryEditor({ kind: "node", draft: defaultDraftNode() });
    } else if (family === "constellations" && defaultNodeRef) {
      setLibraryEditor(null);
      addConstellation(defaultNodeRef);
    } else if (family === "sites" && defaultGroundNodeRef) {
      void (async () => {
        try {
          const { document } = await readCatalogObject(defaultGroundNodeRef);
          const node = (document as { node?: Record<string, unknown> }).node;
          const mounts = (
            (node?.terminals as Record<string, unknown>[] | undefined) ?? []
          ).map((mount) => [String(mount.id), Number(mount.count ?? 1)] as const);
          setEditingSegment(null);
          setLibraryEditor({
            kind: "site",
            draft: newDraftSiteObject(defaultGroundNodeRef, Object.fromEntries(mounts)),
          });
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
          setLibraryEditor(null);
          addGroundDraft(draft);
          setEditingSegment(draft.segment_id);
        } catch (e) {
          setLibraryError(e instanceof Error ? e.message : String(e));
        }
      })();
    }
  };
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
            <Button
              variant="primary"
              onClick={() => {
                clear();
                setSelection(null);
                setEditingSegment(null);
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
                  closeEditors();
                  restoreAutosave();
                }}
              >
                Restore draft
              </Button>
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
              setEditingSegment(null);
              loadSession(selectedFile);
            }}
          >
            {loading ? "Resolving…" : "Load"}
          </Button>
        </div>
        {workspace && (
          <div className="builder-outline-group" data-testid="builder-drafts">
            <button
              className={`builder-outline-row${editingSession ? " builder-outline-row--selected" : ""}`}
              title="Session settings — name, time, candidate budget"
              onClick={() => {
                setLibraryEditor(null);
                closeEditors();
                setEditingSession(true);
              }}
            >
              <span className="builder-outline-kind">Drafts · {workspace.name}</span>
              <span className="builder-outline-count">
                {workspace.step_seconds === 1 && workspace.compression === 1
                  ? "real time"
                  : `×${workspace.compression}`}
              </span>
            </button>
            {workspace.space_refs.map((placed) => (
              <div className="builder-library-entry" key={placed.segment_id}>
                <span className="builder-outline-name builder-outline-name--space builder-outline-row--segment">
                  <Icon name="orbit" size={12} />
                  {placed.label}
                </span>
                <span className="builder-library-actions">
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
                          setLibraryEditor(null);
                          setEditingSegment(draft.segment_id);
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
            ))}
            {workspace.space.map((draft) => (
              <button
                className={`builder-outline-row builder-outline-row--segment${
                  editingSegment === draft.segment_id
                    ? " builder-outline-row--selected"
                    : ""
                }`}
                key={draft.segment_id}
                onClick={() => {
                  setLibraryEditor(null);
                  closeEditors();
                  setEditingSegment(draft.segment_id);
                }}
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
            ))}
            {workspace.ground_refs.map((placed) => (
              <div className="builder-library-entry" key={placed.segment_id}>
                <span className="builder-outline-name builder-outline-name--ground builder-outline-row--segment">
                  <Icon name="satellite-dish" size={12} />
                  {placed.label}
                </span>
                <span className="builder-library-actions">
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
                          setLibraryEditor(null);
                          setEditingSegment(draft.segment_id);
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
            ))}
            {workspace.ground.map((draft) => (
              <button
                className={`builder-outline-row builder-outline-row--segment${
                  editingSegment === draft.segment_id
                    ? " builder-outline-row--selected"
                    : ""
                }`}
                key={draft.segment_id}
                onClick={() => {
                  setLibraryEditor(null);
                  closeEditors();
                  setEditingSegment(draft.segment_id);
                }}
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
            ))}
            {(workspace.links.length > 0 || placedSegments(workspace).length > 0) && (
              <div className="builder-outline-kind">Links</div>
            )}
            {workspace.links.map((rule) => (
              <button
                className={`builder-outline-row builder-outline-row--segment${
                  editingLink === rule.rule_id ? " builder-outline-row--selected" : ""
                }`}
                key={rule.rule_id}
                onClick={() => {
                  setLibraryEditor(null);
                  closeEditors();
                  setEditingLink(rule.rule_id);
                }}
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
            {placedSegments(workspace).length > 0 && (
              <Button
                title="Connect two placed segments — role defaults seed the rule"
                onClick={() => {
                  const placed = placedSegments(workspace);
                  const a =
                    placed.find((s) => s.segment_id === editingSegment) ?? placed[0];
                  const b = placed.find((s) => s.segment_id !== a?.segment_id) ?? a;
                  if (!a || !b) return;
                  const rule = defaultLinkRule(a, b, workspace.links);
                  addLinkRule(rule);
                  setLibraryEditor(null);
                  setEditingSegment(null);
                  setEditingLink(rule.rule_id);
                }}
              >
                + link
              </Button>
            )}
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
                  editingDomain === domain.domain_id
                    ? " builder-outline-row--selected"
                    : ""
                }`}
                key={domain.domain_id}
                onClick={() => {
                  setLibraryEditor(null);
                  closeEditors();
                  setEditingDomain(domain.domain_id);
                }}
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
                    editingBoundary === boundary.boundary_id
                      ? " builder-outline-row--selected"
                      : ""
                  }`}
                  key={boundary.boundary_id}
                  onClick={() => {
                    setLibraryEditor(null);
                    closeEditors();
                    setEditingBoundary(boundary.boundary_id);
                  }}
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
                    setLibraryEditor(null);
                    closeEditors();
                    setEditingDomain(domain.domain_id);
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
                    setLibraryEditor(null);
                    closeEditors();
                    setEditingBoundary(boundary.boundary_id);
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
              onClick={() => defaultNodeRef && addConstellation(defaultNodeRef)}
            >
              + Add constellation
            </Button>
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
        <LibraryPanel
          onUse={handleLibraryUse}
          onCustomize={(entry) => void handleLibraryCustomize(entry)}
          onInspect={(entry) => void handleLibraryInspect(entry)}
          onNew={handleLibraryNew}
        />
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
                      title={`Fly to ${seg.segment_id}`}
                    >
                      <span
                        className={`builder-outline-name builder-outline-name--${space ? "space" : "ground"}`}
                      >
                        <Icon name={space ? "orbit" : "satellite-dish"} size={12} />
                        {seg.segment_id}
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
          <div className="builder-zone-empty">No session loaded</div>
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
              onSelect={setSelection}
              actionsRef={actionsRef}
              liveExplain={false}
              worldLayers={<CandidateLines pairs={visiblePairs} />}
            />
          </VisualizationErrorBoundary>
        ) : snapshotError ? (
          <div className="builder-zone-empty">{snapshotError}</div>
        ) : workspace ? (
          <div className="builder-zone-empty">
            Add a constellation to begin — the world renders as soon as the draft resolves
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
                setEditingSegment(null);
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
        {showYaml && documentYaml && (
          <div className="builder-yaml-pane" data-testid="builder-yaml">
            <div className="builder-yaml-head">
              <span className="builder-zone-title">Session YAML</span>
              <span className="builder-yaml-actions">
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
              </span>
            </div>
            <pre className="builder-yaml-body">{documentYaml}</pre>
          </div>
        )}
      </div>
      <div className="builder-inspector" data-testid="builder-inspector">
        <div className="builder-zone-title">Inspector</div>
        {(() => {
          if (libraryEditor?.kind === "terminal") {
            return (
              <div className="builder-inspector-stack">
                <div className="builder-outline-kind">New terminal</div>
                <TerminalEditor
                  draft={libraryEditor.draft}
                  onChange={(draft) => setLibraryEditor({ kind: "terminal", draft })}
                  catalog={terminalCatalog.entries}
                  onSaved={() => {
                    setLibraryEditor(null);
                    void terminalCatalog.refresh();
                  }}
                  onCancel={() => setLibraryEditor(null)}
                />
              </div>
            );
          }
          if (libraryEditor?.kind === "site") {
            return (
              <div className="builder-inspector-stack">
                <div className="builder-outline-kind">New site</div>
                <SiteEditor
                  site={libraryEditor.draft}
                  onUpdate={(patch) =>
                    setLibraryEditor({
                      kind: "site",
                      draft: { ...libraryEditor.draft, ...patch },
                    })
                  }
                  onClose={() => setLibraryEditor(null)}
                />
              </div>
            );
          }
          if (libraryEditor?.kind === "node") {
            return (
              <div className="builder-inspector-stack">
                <div className="builder-outline-kind">New node</div>
                <NodeEditor
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
                  <Button onClick={() => setLibraryEditor(null)}>Cancel</Button>
                </div>
                {libraryNodeSave.kind === "failed" && (
                  <div className="builder-warning">{libraryNodeSave.message}</div>
                )}
              </div>
            );
          }
          if (catalogInspect) {
            return (
              <CatalogObjectView
                refStr={catalogInspect.ref}
                document={catalogInspect.document}
              />
            );
          }
          if (workspace && editingSession) {
            return <SessionEditor workspace={workspace} onUpdate={updateSession} />;
          }
          const draft = workspace?.space.find((d) => d.segment_id === editingSegment);
          if (draft) {
            return (
              <ConstellationEditor
                draft={draft}
                onUpdate={(patch) => updateConstellation(draft.segment_id, patch)}
                onUpdateOrbit={(patch) => updateOrbit(draft.segment_id, patch)}
                onRemove={() => {
                  removeConstellation(draft.segment_id);
                  setEditingSegment(null);
                }}
              />
            );
          }
          const groundDraft = workspace?.ground.find(
            (d) => d.segment_id === editingSegment,
          );
          if (groundDraft) {
            return (
              <GroundEditor
                draft={groundDraft}
                onUpdate={(patch) => updateGroundDraft(groundDraft.segment_id, patch)}
                onRemove={() => {
                  removeGroundDraft(groundDraft.segment_id);
                  setEditingSegment(null);
                }}
              />
            );
          }
          const linkRule = workspace?.links.find((r) => r.rule_id === editingLink);
          if (workspace && linkRule) {
            return (
              <LinkRuleEditor
                workspace={workspace}
                rule={linkRule}
                onUpdate={(patch) => updateLinkRule(linkRule.rule_id, patch)}
                onUpdateEndpoint={(side, patch) =>
                  updateLinkEndpoint(linkRule.rule_id, side, patch)
                }
                onRemove={() => {
                  removeLinkRule(linkRule.rule_id);
                  setEditingLink(null);
                }}
              />
            );
          }
          const routingDomain = workspace?.routing_domains.find(
            (d) => d.domain_id === editingDomain,
          );
          if (workspace && routingDomain) {
            return (
              <RoutingDomainEditor
                workspace={workspace}
                domain={routingDomain}
                onUpdate={(patch) => updateRoutingDomain(routingDomain.domain_id, patch)}
                onRemove={() => {
                  removeRoutingDomain(routingDomain.domain_id);
                  setEditingDomain(null);
                }}
              />
            );
          }
          const boundary = workspace?.boundaries.find(
            (b) => b.boundary_id === editingBoundary,
          );
          if (workspace && boundary) {
            return (
              <BoundaryEditor
                workspace={workspace}
                boundary={boundary}
                onUpdate={(patch) => updateBoundary(boundary.boundary_id, patch)}
                onRemove={() => {
                  removeBoundary(boundary.boundary_id);
                  setEditingBoundary(null);
                }}
              />
            );
          }
          const node =
            selection && world
              ? world.nodes.find((n) => n.node_id === selection.id)
              : undefined;
          return node && world ? (
            <BuilderInspector node={node} ephemeris={world.ephemeris} />
          ) : (
            <div className="builder-zone-empty">Nothing selected</div>
          );
        })()}
      </div>
      {workspace && completenessFindings(workspace).length > 0 && (
        <div className="builder-rail" data-testid="builder-rail">
          {completenessFindings(workspace).map((finding) => (
            <button
              key={finding.message}
              className="builder-rail-chip"
              disabled={finding.target === null}
              title={finding.target ? "Jump to the owning editor" : undefined}
              onClick={() => {
                const target = finding.target;
                if (!target) return;
                setLibraryEditor(null);
                closeEditors();
                if (target.kind === "session") setEditingSession(true);
                else if (target.kind === "link") setEditingLink(target.id);
                else setEditingSegment(target.id);
              }}
            >
              {finding.message}
            </button>
          ))}
        </div>
      )}
      <div className="builder-status" data-testid="builder-status">
        <span className="builder-mode-badge">Session Builder</span>
        {error ? (
          <span className="builder-status-item builder-status-item--error">{error}</span>
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
            {workspace ? "draft — not resolved yet" : "no session loaded"}
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
        {documentYaml && (
          <button
            className={`builder-status-toggle${showYaml ? " builder-status-toggle--active" : ""}`}
            onClick={() => setShowYaml((v) => !v)}
          >
            YAML
          </button>
        )}
      </div>
    </div>
  );
}
