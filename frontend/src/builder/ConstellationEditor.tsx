// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
/** Constellation draft editor — the builder's first authoring surface.
 *
 *  Progressive disclosure like the read-only inspector: Orbit and Pattern
 *  cards, one open at a time, closed cards reading as the spec sheet. Every
 *  field is typeable; presets seed raw values the user then owns (never
 *  modes). Orbit sanity findings warn in plain language and never block —
 *  the resolver's verdict arrives through the live resolve-check and is
 *  shown verbatim in the status bar.
 */

import { useState } from "react";
import { Button } from "../ui/Button";
import { EditorCard, EditorName, NumberField, SelectField, SliderField } from "./editorKit";
import { NodeEditor } from "./NodeEditor";
import { SegmentLinksCard } from "./SegmentLinksCard";
import {
  LIBRARY_SAVE_COPY,
  exportCatalogObject,
  readCatalogObject,
  useBuilderCatalog,
  useLibrarySave,
} from "./useBuilderWorld";
import {
  EARTH_BODY_REF,
  ORBIT_PRESETS,
  draftNodeFromDocument,
  dwellLongitudeDeg,
  identifier,
  isGeosynchronous,
  nodeObjectFromDraft,
  orbitWarnings,
  type DraftConstellation,
  type DraftOrbit,
  type Workspace,
} from "./workspace";

interface ConstellationEditorProps {
  draft: DraftConstellation;
  onUpdate: (patch: Partial<DraftConstellation>) => void;
  onUpdateOrbit: (patch: Partial<DraftOrbit>) => void;
  onRemove: () => void;
  /** IG-2: focus the name when a create gesture opened this editor. */
  autoFocusName?: boolean;
  /** Connect gesture context (IG-7: "+ link to…" on the segment). */
  workspace: Workspace;
  onOpenRule: (ruleId: string) => void;
  onConnect: (targetSegmentId: string) => void;
}

/** "nodalarc:bodies/luna.yaml" -> "luna" for the card's spec line. */
function bodyShortName(ref: string): string {
  const base = ref.split("/").pop() ?? ref;
  return base.replace(/\.ya?ml$/, "");
}

export function ConstellationEditor({
  draft,
  onUpdate,
  onUpdateOrbit,
  onRemove,
  autoFocusName = false,
  workspace,
  onOpenRule,
  onConnect,
}: ConstellationEditorProps) {
  const [openCard, setOpenCard] = useState<string | null>("orbit");
  const nodes = useBuilderCatalog("nodes");
  const bodies = useBuilderCatalog("bodies");
  const warnings = orbitWarnings(draft.orbit);
  const toggle = (id: string) => setOpenCard((prev) => (prev === id ? null : id));
  const [forkError, setForkError] = useState<string | null>(null);
  const librarySave = useLibrarySave("nodes");

  // Fork-to-draft: read the referenced node's document and edit it inline.
  // The origin ref stays as the fallback; discard just clears the draft.
  const customizeNode = async () => {
    setForkError(null);
    try {
      const { document } = await readCatalogObject(draft.node_ref);
      const node_draft = draftNodeFromDocument(document);
      // A forked copy is a new object: never claim the original's identity.
      node_draft.id = identifier(`${node_draft.id}-custom`);
      node_draft.display_name = `${node_draft.display_name} (custom)`;
      onUpdate({ node_draft });
      librarySave.reset();
    } catch (e) {
      setForkError(e instanceof Error ? e.message : String(e));
    }
  };

  // Save the draft to the user catalog and let the segment reference it —
  // draft → user: ref, the library direction of tweak-ours→yours. The node-ref
  // rewrite (draft → the new user ref) is this save's own consequence.
  const saveNodeToLibrary = () => {
    if (!draft.node_draft) return;
    void librarySave.save({ node: nodeObjectFromDraft(draft.node_draft) }, (ref) =>
      onUpdate({ node_ref: ref, node_draft: null }),
    );
  };

  return (
    <div className="builder-inspector-stack" data-testid="builder-editor">
      <EditorName
        value={draft.display_name}
        onChange={(display_name) => onUpdate({ display_name })}
        autoFocus={autoFocusName}
      />

      <EditorCard
        title="Orbit"
        open={openCard === "orbit"}
        onToggle={() => toggle("orbit")}
        summary={
          <>
            {draft.orbit.shape_kind === "circular"
              ? `${Math.round(draft.orbit.altitude_km)} km circular`
              : `${Math.round(draft.orbit.perigee_altitude_km)} × ${Math.round(draft.orbit.apogee_altitude_km)} km`}{" "}
            · {draft.orbit.inclination_deg.toFixed(1)}°
            {draft.orbit.central_body !== EARTH_BODY_REF &&
              ` · ${bodyShortName(draft.orbit.central_body)}`}
          </>
        }
      >
            <SelectField
              label="around"
              ariaLabel="Central body"
              value={draft.orbit.central_body}
              onChange={(central_body) => onUpdateOrbit({ central_body })}
              options={
                bodies.entries.filter((entry) => !entry.error).length > 0
                  ? bodies.entries
                      .filter((entry) => !entry.error)
                      .map((entry) => ({
                        value: entry.ref,
                        label: entry.display_name ?? entry.id ?? entry.ref,
                      }))
                  : [{ value: draft.orbit.central_body, label: draft.orbit.central_body }]
              }
            />
            {draft.orbit.central_body === EARTH_BODY_REF && (
              <div className="builder-preset-row">
                {ORBIT_PRESETS.map((preset) => (
                  <Button key={preset.label} onClick={() => onUpdateOrbit(preset.orbit)}>
                    {preset.label}
                  </Button>
                ))}
              </div>
            )}
            <div className="builder-preset-row" role="radiogroup" aria-label="Orbit shape">
              <Button
                active={draft.orbit.shape_kind === "circular"}
                onClick={() => onUpdateOrbit({ shape_kind: "circular" })}
              >
                circular
              </Button>
              <Button
                active={draft.orbit.shape_kind === "elliptical"}
                onClick={() => onUpdateOrbit({ shape_kind: "elliptical" })}
              >
                elliptical
              </Button>
            </div>
            {draft.orbit.shape_kind === "circular" ? (
              <SliderField
                label="altitude"
                value={draft.orbit.altitude_km}
                min={150}
                max={40000}
                step={10}
                suffix="km"
                onChange={(altitude_km) => onUpdateOrbit({ altitude_km })}
              />
            ) : (
              <>
                <SliderField
                  label="perigee"
                  value={draft.orbit.perigee_altitude_km}
                  min={150}
                  max={40000}
                  step={10}
                  suffix="km"
                  onChange={(perigee_altitude_km) => onUpdateOrbit({ perigee_altitude_km })}
                />
                <SliderField
                  label="apogee"
                  value={draft.orbit.apogee_altitude_km}
                  min={150}
                  max={45000}
                  step={10}
                  suffix="km"
                  onChange={(apogee_altitude_km) => onUpdateOrbit({ apogee_altitude_km })}
                />
                <SliderField
                  label="arg of perigee"
                  value={draft.orbit.argument_of_perigee_deg}
                  min={0}
                  max={360}
                  suffix="deg"
                  onChange={(argument_of_perigee_deg) =>
                    onUpdateOrbit({ argument_of_perigee_deg })
                  }
                />
              </>
            )}
            <SliderField
              label="inclination"
              value={draft.orbit.inclination_deg}
              min={0}
              max={180}
              step={0.5}
              suffix="deg"
              onChange={(inclination_deg) => onUpdateOrbit({ inclination_deg })}
            />
            <SliderField
              label="RAAN"
              value={draft.orbit.raan_deg}
              min={0}
              max={360}
              suffix="deg"
              onChange={(raan_deg) => onUpdateOrbit({ raan_deg })}
            />
            <SliderField
              label="mean anomaly"
              value={draft.orbit.mean_anomaly_deg}
              min={0}
              max={360}
              suffix="deg"
              onChange={(mean_anomaly_deg) => onUpdateOrbit({ mean_anomaly_deg })}
            />
            {isGeosynchronous(draft.orbit) && (
              <div className="builder-site-derived">
                first slot dwells over{" "}
                {(Math.round(dwellLongitudeDeg(draft.orbit, workspace.start_time) * 10) / 10).toFixed(1)}
                °E at session start (negative is west) — drag mean anomaly to
                move it; remaining slots space around the ring
              </div>
            )}
            {warnings.map((warning) => (
              <div className="builder-warning" key={warning}>
                {warning}
              </div>
            ))}
      </EditorCard>

      <EditorCard
        title="Pattern"
        open={openCard === "pattern"}
        onToggle={() => toggle("pattern")}
        summary={
          <>
            {draft.planes} × {draft.slots_per_plane} ={" "}
            {draft.planes * draft.slots_per_plane} sats
          </>
        }
      >
            <NumberField
              label="planes"
              value={draft.planes}
              onChange={(planes) => onUpdate({ planes: Math.max(1, Math.round(planes)) })}
            />
            <NumberField
              label="slots per plane"
              value={draft.slots_per_plane}
              onChange={(slots_per_plane) =>
                onUpdate({ slots_per_plane: Math.max(1, Math.round(slots_per_plane)) })
              }
            />
            <NumberField
              label="RAAN spacing"
              value={draft.raan_spacing_deg}
              suffix="deg"
              onChange={(raan_spacing_deg) => onUpdate({ raan_spacing_deg })}
            />
            <NumberField
              label="phase offset"
              value={draft.phase_offset_deg}
              suffix="deg"
              onChange={(phase_offset_deg) => onUpdate({ phase_offset_deg })}
            />
      </EditorCard>

      <EditorCard
        title="Node"
        open={openCard === "node"}
        onToggle={() => toggle("node")}
        summary={
          draft.node_draft
            ? `${draft.node_draft.display_name} (custom) · ${draft.node_draft.terminals.reduce((s, m) => s + m.count, 0)} terminals`
            : draft.node_ref.split("/").pop()?.replace(".yaml", "")
        }
      >
            {draft.node_draft ? (
              <>
                <NodeEditor
                  draft={draft.node_draft}
                  onChange={(node_draft) => onUpdate({ node_draft })}
                />
                <div className="builder-preset-row">
                  <Button
                    variant="primary"
                    onClick={saveNodeToLibrary}
                    disabled={librarySave.saving}
                  >
                    {librarySave.label("Save to library")}
                  </Button>
                  <Button onClick={() => onUpdate({ node_draft: null })}>
                    Discard customization
                  </Button>
                </div>
              </>
            ) : (
              <>
                <SelectField
                  stack
                  label="model"
                  ariaLabel="Node primitive"
                  value={draft.node_ref}
                  onChange={(node_ref) => onUpdate({ node_ref })}
                  options={nodes.entries
                    .filter((entry) => !entry.error)
                    .map((entry) => ({
                      value: entry.ref,
                      label:
                        (entry.ref.startsWith("user:") ? "\u2605 " : "") +
                        (entry.display_name ?? entry.id ?? entry.ref),
                    }))}
                />
                <div className="builder-preset-row">
                  <Button onClick={() => void customizeNode()}>Customize node</Button>
                  <Button onClick={() => void exportCatalogObject(draft.node_ref)}>
                    Export file
                  </Button>
                </div>
                {forkError && <div className="builder-warning">{forkError}</div>}
                {nodes.error && <div className="builder-warning">{nodes.error}</div>}
              </>
            )}
            {librarySave.state.kind === "failed" && (
              <div className="builder-warning">{librarySave.state.message}</div>
            )}
            {librarySave.state.kind === "saved" && (
              <div className="builder-library-note" data-testid="library-note">
                {LIBRARY_SAVE_COPY.savedNote(librarySave.state.ref)}
              </div>
            )}
      </EditorCard>

      <SegmentLinksCard
        workspace={workspace}
        segmentId={draft.segment_id}
        onOpenRule={onOpenRule}
        onConnect={onConnect}
      />

      <Button variant="danger" onClick={onRemove}>
        Remove constellation
      </Button>
    </div>
  );
}
