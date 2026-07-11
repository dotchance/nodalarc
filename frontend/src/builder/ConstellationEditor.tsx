// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
/** Constellation draft editor — the builder's first authoring surface.
 *
 *  Progressive disclosure like the read-only inspector: Orbit and Pattern
 *  cards, one open at a time, closed cards reading as the spec sheet. Every
 *  field is typeable; presets seed raw values the user then owns (never
 *  modes). The resolver's verdict arrives through backend compile and is
 *  shown verbatim in the status bar; this editor does not run separate
 *  orbital physics.
 */

import { useState } from "react";
import { Button } from "../ui/Button";
import { BodySelect, EditorCard, EditorName, NumberField, SelectField, SliderField } from "./editorKit";
import { NodeEditor } from "./NodeEditor";
import { SegmentLinksCard } from "./SegmentLinksCard";
import type { BuilderVisualAuthoringFacts } from "./generated/builderApi";
import {
  exportCatalogObject,
  useBuilderCatalog,
} from "./useBuilderWorld";
import {
  type DraftConstellation,
  type DraftNode,
  type DraftOrbit,
  type PhasingMode,
  type Workspace,
} from "./workspace";

interface ConstellationEditorProps {
  draft: DraftConstellation;
  /** Functional-only: reached from an async path (customizeNode's fetch,
   *  the node save-then-ref), so it reads the LATEST draft, never a stale
   *  closure. `onUpdateOrbit` stays value-form — orbit params are sync-only
   *  (NumberFields), never written from an async writer. */
  onUpdate: (update: (prev: DraftConstellation) => DraftConstellation) => void;
  onUpdateOrbit: (patch: Partial<DraftOrbit>) => void;
  onRemove: () => void;
  /** focus the name when a create gesture opened this editor. */
  autoFocusName?: boolean;
  /** Connect gesture context ("+ link to…" on the segment). */
  workspace: Workspace;
  onOpenRule: (ruleId: string) => void;
  onConnect: (targetSegmentId: string) => void;
  authoring: BuilderVisualAuthoringFacts;
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
  authoring,
}: ConstellationEditorProps) {
  const [openCard, setOpenCard] = useState<string | null>("orbit");
  const nodes = useBuilderCatalog("nodes");
  const bodies = useBuilderCatalog("bodies");
  const phasingLabel =
    authoring.phasing_modes.find((choice) => choice.id === draft.phasing_mode)?.label ??
    draft.phasing_mode;
  const toggle = (id: string) => setOpenCard((prev) => (prev === id ? null : id));

  const startInlineNode = () => {
    const node = structuredClone(authoring.default_node) as DraftNode;
    node.id = `${draft.segment_id}-node`;
    node.display_name = `${draft.display_name} node`;
    onUpdate((prev) => ({ ...prev, node_draft: node }));
  };

  return (
    <div className="builder-inspector-stack" data-testid="builder-editor">
      <EditorName
        value={draft.display_name}
        onChange={(display_name) => onUpdate((prev) => ({ ...prev, display_name }))}
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
            {draft.orbit.central_body !== authoring.default_body_ref &&
              ` · ${bodyShortName(draft.orbit.central_body)}`}
          </>
        }
      >
            <BodySelect
              label="around"
              ariaLabel="Central body"
              value={draft.orbit.central_body}
              onChange={(central_body) => onUpdateOrbit({ central_body })}
              bodies={bodies}
            />
            <div className="builder-preset-row" role="radiogroup" aria-label="Orbit shape">
              {authoring.orbit_shapes.map((choice) => (
                <Button
                  key={choice.id}
                  active={draft.orbit.shape_kind === choice.id}
                  onClick={() => onUpdateOrbit({ shape_kind: choice.id })}
                >
                  {choice.label}
                </Button>
              ))}
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
      </EditorCard>

      <EditorCard
        title="Pattern"
        open={openCard === "pattern"}
        onToggle={() => toggle("pattern")}
        summary={
          <>
            {draft.planes} × {draft.slots_per_plane} ={" "}
            {draft.planes * draft.slots_per_plane} sats · {phasingLabel}
          </>
        }
      >
            <SelectField
              label="phasing"
              value={draft.phasing_mode}
              onChange={(value) =>
                onUpdate((prev) => {
                  const phasing_mode = value as PhasingMode;
                  if (phasing_mode === authoring.single_plane_phasing_mode) {
                    return {
                      ...prev,
                      phasing_mode,
                      planes: 1,
                      raan_spacing_deg: 360,
                      phase_offset_deg: 0,
                    };
                  }
                  return {
                    ...prev,
                    phasing_mode,
                    planes: Math.max(2, prev.planes),
                  };
                })
              }
              options={authoring.phasing_modes.map((choice) => ({
                value: choice.id,
                label: choice.label,
              }))}
            />
            <NumberField
              label="planes"
              value={draft.planes}
              onChange={(planes) =>
                onUpdate((prev) => {
                  const count = Math.max(1, Math.round(planes));
                  if (count === 1) {
                    return {
                      ...prev,
                      planes: count,
                      phasing_mode: authoring.single_plane_phasing_mode,
                      phase_offset_deg: 0,
                    };
                  }
                  return {
                    ...prev,
                    planes: count,
                    phasing_mode:
                      prev.phasing_mode === authoring.single_plane_phasing_mode
                        ? authoring.default_phasing_mode
                        : prev.phasing_mode,
                  };
                })
              }
            />
            <NumberField
              label="slots per plane"
              value={draft.slots_per_plane}
              onChange={(slots_per_plane) =>
                onUpdate((prev) => ({
                  ...prev,
                  slots_per_plane: Math.max(1, Math.round(slots_per_plane)),
                }))
              }
            />
            <NumberField
              label="RAAN spacing"
              value={draft.raan_spacing_deg}
              suffix="deg"
              onChange={(raan_spacing_deg) => onUpdate((prev) => ({ ...prev, raan_spacing_deg }))}
            />
            {draft.phasing_mode === authoring.single_plane_phasing_mode ? (
              <div className="builder-site-derived">
                phase offset 0 deg — a single plane has no inter-plane offset
              </div>
            ) : (
              <NumberField
                label="phase offset"
                value={draft.phase_offset_deg}
                suffix="deg"
                onChange={(phase_offset_deg) =>
                  onUpdate((prev) => ({ ...prev, phase_offset_deg }))
                }
              />
            )}
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
                  authoring={authoring}
                  draft={draft.node_draft}
                  onChange={(update) =>
                    // Thread NodeEditor's functional update through the
                    // constellation's own, reading the LATEST node_draft so a
                    // concurrent edit during a terminal import/save survives.
                    onUpdate((prev) => ({
                      ...prev,
                      node_draft: prev.node_draft ? update(prev.node_draft) : prev.node_draft,
                    }))
                  }
                />
                <div className="builder-site-derived">
                  This inline node belongs to the session proposal. Create reusable node
                  components from the Library.
                </div>
                <div className="builder-preset-row">
                  <Button onClick={() => onUpdate((prev) => ({ ...prev, node_draft: null }))}>
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
                  onChange={(node_ref) => onUpdate((prev) => ({ ...prev, node_ref }))}
                  options={nodes.entries
                    .map((entry) => ({
                      value: entry.ref,
                      label:
                        (entry.ref.startsWith("user:") ? "\u2605 " : "") +
                        entry.display_name,
                    }))}
                />
                <div className="builder-preset-row">
                  <Button onClick={startInlineNode}>Author inline node</Button>
                  <Button onClick={() => void exportCatalogObject(draft.node_ref)}>
                    Export file
                  </Button>
                </div>
                {nodes.error && <div className="builder-warning">{nodes.error}</div>}
              </>
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
