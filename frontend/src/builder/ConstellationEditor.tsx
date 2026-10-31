// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
/** Space segment editor. The segment's own fields are its pending edit,
 *  edited in the field editor the caller passes in. The constellation it
 *  places, with its orbit and node, is a document: one the session proposes
 *  opens in the component window, and a catalog document is customized
 *  first. The population gestures change the proposed constellation as one
 *  accepted revision, with the plane spacing and phase offset the backend
 *  derives. The resolver's verdict arrives through backend compile.
 */

import { useState, type ReactNode } from "react";
import { Button } from "../ui/Button";
import { KeyValueRow } from "../ui/KeyValueRow";
import { EditorCard, NumberField, SelectField } from "./editorKit";
import { SegmentLinksCard } from "./SegmentLinksCard";
import type { ListedLinkRule } from "./sessionListing";
import {
  isJsonObject,
  proposedBody,
  refStem,
  type SessionSegment,
} from "./sessionSegments";
import type {
  BuilderProposedCatalogDocument,
  BuilderVisualAuthoringFacts,
  BuilderVisualPhasingMode,
  BuilderVisualSetSpacePopulationCommand,
} from "./generated/builderApi";

interface ConstellationEditorProps {
  segment: SessionSegment;
  /** The segment's field editor over its pending edit. */
  fields: ReactNode;
  /** The documents the session proposes. */
  proposals: ReadonlyArray<BuilderProposedCatalogDocument>;
  onSetPopulation: (
    change: Omit<BuilderVisualSetSpacePopulationCommand, "operation" | "segment_id">,
  ) => Promise<void>;
  /** Open the document the session proposes at `ref` in the component window. */
  onEdit: (ref: string) => void;
  /** Open the component chain under the segment's source for customizing. */
  onCustomize: () => void;
  /** Fork the node into a document the session proposes. */
  onCustomizeNode: (nodeRef: string) => Promise<void>;
  /** Remove the segment from the session; null while a change is being applied. */
  onRemove: (() => void) | null;
  placed: ReadonlyArray<SessionSegment>;
  /** Every link rule the session declares, as the backend lists them with
   *  the pending edits laid over them. */
  linkRules: ReadonlyArray<ListedLinkRule>;
  onOpenRule: (ruleId: string) => void;
  onConnect: (targetSegmentId: string) => void;
  authoring: BuilderVisualAuthoringFacts;
}

function numberOf(value: unknown): number | null {
  return typeof value === "number" ? value : null;
}

function textOf(value: unknown): string | null {
  return typeof value === "string" ? value : null;
}

export function ConstellationEditor({
  segment,
  fields,
  proposals,
  onSetPopulation,
  onEdit,
  onCustomize,
  onCustomizeNode,
  onRemove,
  placed,
  linkRules,
  onOpenRule,
  onConnect,
  authoring,
}: ConstellationEditorProps) {
  const [editorError, setEditorError] = useState<string | null>(null);
  const report = (cause: unknown) =>
    setEditorError(cause instanceof Error ? cause.message : String(cause));
  const sourceRef = textOf(segment.segment.source);
  const constellation = proposedBody(proposals, sourceRef, "constellation");
  const planesBlock = isJsonObject(constellation?.planes) ? constellation.planes : {};
  const phasingBlock = isJsonObject(constellation?.phasing) ? constellation.phasing : {};
  const planes = numberOf(planesBlock.count);
  const slots = numberOf(constellation?.slots_per_plane);
  const phasingMode = textOf(phasingBlock.mode);
  const orbitRef = textOf(constellation?.orbit);
  const nodeRef = textOf(constellation?.node);
  const orbitProposed = proposedBody(proposals, orbitRef, "orbit") !== null;
  const nodeProposed = proposedBody(proposals, nodeRef, "node") !== null;
  const setPopulation = (
    change: Omit<BuilderVisualSetSpacePopulationCommand, "operation" | "segment_id">,
  ) => {
    setEditorError(null);
    void onSetPopulation(change).catch(report);
  };

  return (
    <div className="builder-inspector-stack" data-testid="builder-editor">
      <EditorCard title="Segment" open summary={segment.segment_id}>
        {fields}
      </EditorCard>

      <EditorCard
        title="Constellation"
        open
        summary={
          constellation === null
            ? (sourceRef === null ? "no source" : refStem(sourceRef))
            : `${planes ?? "?"} × ${slots ?? "?"} = ${
                planes === null || slots === null ? "incomplete" : planes * slots
              } sats`
        }
      >
        <KeyValueRow label="source">{sourceRef ?? "none"}</KeyValueRow>
        {constellation === null ? (
          <>
            <div className="builder-site-derived">
              a library component — customize it to edit it in this session
            </div>
            <Button disabled={sourceRef === null} onClick={onCustomize}>
              Customize…
            </Button>
          </>
        ) : (
          <>
            <SelectField
              label="phasing"
              value={phasingMode}
              onChange={(value) =>
                setPopulation({ phasing_mode: value as BuilderVisualPhasingMode })
              }
              options={authoring.phasing_modes.map((choice) => ({
                value: choice.id,
                label: choice.label,
              }))}
            />
            <NumberField
              label="planes"
              value={planes}
              min={1}
              integer
              onChange={(value) => setPopulation({ planes: value })}
            />
            <NumberField
              label="slots per plane"
              value={slots}
              min={1}
              integer
              onChange={(value) => setPopulation({ slots_per_plane: value })}
            />
            <div className="builder-site-derived">
              RAAN spacing {numberOf(planesBlock.raan_spacing_deg) ?? "?"} deg · phase offset{" "}
              {numberOf(phasingBlock.phase_offset_deg) ?? "?"} deg, derived from the pattern
            </div>
            <div className="builder-preset-row">
              <Button onClick={() => sourceRef && onEdit(sourceRef)}>Edit constellation</Button>
            </div>
            <KeyValueRow label="orbit">{orbitRef ?? "none"}</KeyValueRow>
            <div className="builder-preset-row">
              {orbitProposed ? (
                <Button onClick={() => orbitRef && onEdit(orbitRef)}>Edit orbit</Button>
              ) : (
                <Button disabled={orbitRef === null} onClick={onCustomize}>
                  Customize orbit…
                </Button>
              )}
            </div>
            <KeyValueRow label="node">{nodeRef ?? "none"}</KeyValueRow>
            <div className="builder-preset-row">
              {nodeProposed ? (
                <Button onClick={() => nodeRef && onEdit(nodeRef)}>Edit node</Button>
              ) : (
                <Button
                  disabled={nodeRef === null}
                  onClick={() => {
                    if (nodeRef === null) return;
                    setEditorError(null);
                    void onCustomizeNode(nodeRef).catch(report);
                  }}
                >
                  Customize node
                </Button>
              )}
            </div>
          </>
        )}
      </EditorCard>

      {editorError && <div className="builder-warning">{editorError}</div>}

      <SegmentLinksCard
        placed={placed}
        rules={linkRules}
        segmentId={segment.segment_id}
        onOpenRule={onOpenRule}
        onConnect={onConnect}
      />

      <Button variant="danger" onClick={onRemove ?? undefined} disabled={onRemove === null}>
        Remove segment
      </Button>
    </div>
  );
}
