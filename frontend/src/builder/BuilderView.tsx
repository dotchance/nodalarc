// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
/** Session builder — four-zone authoring shell.
 *
 *  Zones (validated in the discovery arc): outline tree (left, grouped by
 *  body) | world canvas (center) | docked card inspector (right) | status
 *  bar (bottom, the single home for counts/validation/gates). The builder
 *  renders the resolver's expansion of a session — never a builder-local
 *  view of what a session means.
 *
 *  Current scope: read-only. Loads an existing catalog session through the
 *  resolve-backed endpoint and renders the resolved world.
 */

import { useEffect, useMemo, useState, type MutableRefObject } from "react";
import { Scene } from "../globe/r3f/Scene";
import { VisualizationErrorBoundary } from "../globe/VisualizationErrorBoundary";
import { buildRegimeIndex } from "../taxonomy/regime";
import { Button } from "../ui/Button";
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
import { ConstellationEditor } from "./ConstellationEditor";
import { useBuilderCatalog, useBuilderWorld } from "./useBuilderWorld";
import { useWorkspace } from "./useWorkspace";
import { toSessionDocument } from "./workspace";
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
    close: closeWorkspace,
    addConstellation,
    removeConstellation,
    updateConstellation,
    updateOrbit,
    setGroundSiteSet,
  } = useWorkspace(resolveDocument);
  const nodeCatalog = useBuilderCatalog("nodes");
  const siteSetCatalog = useBuilderCatalog("site-sets");
  const [editingSegment, setEditingSegment] = useState<string | null>(null);
  const [showYaml, setShowYaml] = useState(false);
  const [saveState, setSaveState] = useState<
    | { kind: "idle" }
    | { kind: "saving" }
    | { kind: "saved"; name: string }
    | { kind: "failed"; message: string }
  >({ kind: "idle" });
  // Default node model for a fresh constellation: prefer the catalog's space
  // nodes (directory layout is authoring convention, so this is a display
  // heuristic only — the picker offers every node either way).
  const defaultNodeRef =
    nodeCatalog.entries.find((e) => !e.error && e.ref.includes("nodes/space/"))?.ref ??
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
        <div className="builder-session-picker">
          <select
            aria-label="Catalog session"
            value={selectedFile}
            onChange={(e) => setSelectedFile(e.target.value)}
          >
            <option value="" disabled>
              {sessions.length ? "Select a session…" : "No sessions found"}
            </option>
            {sessions.map((s) => (
              <option key={s.file} value={s.file}>
                {s.name}
              </option>
            ))}
          </select>
          <Button
            variant="primary"
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
        {!workspace && (
          <Button
            onClick={() => {
              clear();
              setSelection(null);
              setEditingSegment(null);
              startNew("untitled-session");
            }}
          >
            New session
          </Button>
        )}
        {workspace && (
          <div className="builder-outline-group" data-testid="builder-drafts">
            <div className="builder-outline-kind">Drafts · {workspace.name}</div>
            {workspace.space.map((draft) => (
              <button
                className={`builder-outline-row builder-outline-row--segment${
                  editingSegment === draft.segment_id
                    ? " builder-outline-row--selected"
                    : ""
                }`}
                key={draft.segment_id}
                onClick={() => setEditingSegment(draft.segment_id)}
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
            <label className="builder-field builder-field--stack">
              <span className="builder-field-label">ground sites</span>
              <select
                aria-label="Ground site set"
                value={workspace.ground_site_set_ref ?? ""}
                onChange={(e) => setGroundSiteSet(e.target.value || null)}
              >
                <option value="">none</option>
                {siteSetCatalog.entries
                  .filter((entry) => !entry.error)
                  .map((entry) => (
                    <option key={entry.ref} value={entry.ref}>
                      {entry.display_name ?? entry.id ?? entry.ref}
                    </option>
                  ))}
              </select>
            </label>
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
                  setSaveState({ kind: "saved", name: result.name });
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
          </div>
        )}
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
        ) : (
          <div className="builder-zone-empty">
            {snapshotError ??
              (workspace
                ? "Add a constellation to begin — the world renders as soon as the draft resolves"
                : "Load a catalog session or start a new one")}
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
            ✓ resolves: {world.nodes.length} nodes ({satelliteCount} satellites ·{" "}
            {groundCount} ground) · {segments.length} segments
            {candidates && world.link_rules.length > 0 && (
              <>
                {" "}
                · {world.link_rules.length} rules → {candidates.pairs.length} LOS candidates
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
