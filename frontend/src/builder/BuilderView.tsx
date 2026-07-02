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

import { useMemo, useState, type MutableRefObject } from "react";
import { Scene } from "../globe/r3f/Scene";
import { VisualizationErrorBoundary } from "../globe/VisualizationErrorBoundary";
import { buildRegimeIndex } from "../taxonomy/regime";
import { Button } from "../ui/Button";
import type { GlobeActions } from "../globe/actions";
import type { ColorMode, GlobeMode, ReferenceFrame, StateSnapshot } from "../types";
import { builderSnapshotFromWorld } from "./builderSnapshot";
import { useBuilderWorld } from "./useBuilderWorld";
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
      summary = { segment_id: node.segment_id, body, satellites: 0, grounds: 0, relays: 0 };
      bySegment.set(node.segment_id, summary);
    }
    if (node.kind === "satellite") summary.satellites += 1;
    else if (node.kind === "ground_station") summary.grounds += 1;
    else summary.relays += 1;
  }
  return [...bySegment.values()].sort((a, b) => a.segment_id.localeCompare(b.segment_id));
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
  const { sessions, sessionsError, world, loading, error, loadSession } = useBuilderWorld();
  const [selectedFile, setSelectedFile] = useState("");

  const segments = useMemo(() => (world ? summarizeSegments(world) : []), [world]);
  const satelliteCount = world
    ? world.nodes.filter((n) => n.kind === "satellite").length
    : 0;
  const groundCount = world ? world.nodes.length - satelliteCount : 0;

  // Scene input derived from the resolved world, frozen at the session epoch.
  // A derivation failure is a real finding — surfaced in the status bar, and
  // nothing renders (never a partially-wrong world).
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

  return (
    <div className="builder-shell" data-testid="builder-shell">
      <div className="builder-outline" data-testid="builder-outline">
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
            onClick={() => loadSession(selectedFile)}
          >
            {loading ? "Resolving…" : "Load"}
          </Button>
        </div>
        {sessionsError && (
          <div className="builder-zone-empty builder-status-item--error">
            session list unavailable: {sessionsError}
          </div>
        )}
        {world ? (
          <div className="builder-outline-group" data-testid="builder-segments">
            {segments.map((seg) => (
              <div className="builder-outline-row" key={seg.segment_id}>
                <span>
                  {seg.segment_id}
                  {seg.body ? ` · ${seg.body}` : ""}
                </span>
                <span className="builder-outline-count">
                  {seg.satellites > 0 && `${seg.satellites} sat`}
                  {seg.satellites > 0 && seg.grounds + seg.relays > 0 && " · "}
                  {seg.grounds > 0 && `${seg.grounds} gs`}
                  {seg.relays > 0 && ` · ${seg.relays} relay`}
                </span>
              </div>
            ))}
          </div>
        ) : (
          <div className="builder-zone-empty">No session loaded</div>
        )}
      </div>
      <div className="builder-canvas" data-testid="builder-canvas">
        {world && snapshot ? (
          /* Read-only for now: selection stays null so the Scene's explain hooks
             (which answer for the RUNNING session) can never fire here. */
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
              selection={null}
              onSelect={() => {}}
              actionsRef={actionsRef}
            />
          </VisualizationErrorBoundary>
        ) : (
          <div className="builder-zone-empty">
            {snapshotError ?? "Load a catalog session to render its resolved world"}
          </div>
        )}
      </div>
      <div className="builder-inspector" data-testid="builder-inspector">
        <div className="builder-zone-title">Inspector</div>
        <div className="builder-zone-empty">Nothing selected</div>
      </div>
      <div className="builder-status" data-testid="builder-status">
        {error ? (
          <span className="builder-status-item builder-status-item--error">{error}</span>
        ) : snapshotError ? (
          <span className="builder-status-item builder-status-item--error">
            {snapshotError}
          </span>
        ) : world ? (
          <span className="builder-status-item">
            ✓ resolves: {world.nodes.length} nodes ({satelliteCount} satellites ·{" "}
            {groundCount} ground) · {segments.length} segments
          </span>
        ) : (
          <span className="builder-status-item">builder — read-only shell</span>
        )}
      </div>
    </div>
  );
}
