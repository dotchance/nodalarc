// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
/** Link rule editor — comms intent between placed segments.
 *
 *  A rule declares who may link; OME computes feasibility from geometry,
 *  terminal limits, and runtime state. Role defaults seed the rule when two
 *  segments are connected (isl for a fabric, crosslink space↔space, access
 *  ground↔space); every value is then owned by the user. Tag scopes are how
 *  one ground segment serves multiple constellations differently. The LOS
 *  candidate preview on the canvas and the dark-rule notes come from the
 *  resolver's expansion of exactly what this editor emits.
 */

import { useState } from "react";
import { Button } from "../ui/Button";
import { KeyValueRow } from "../ui/KeyValueRow";
import {
  EditorCard,
  Field,
  NullableNumberField,
  NumberField,
  SelectField,
} from "./editorKit";
import type { BuilderRuleAllocation, BuilderWorldSegment } from "./builderTypes";
import type { BuilderVisualAuthoringFacts } from "./generated/builderApi";
import {
  linkWarnings,
  placedSegments,
  type DraftLinkEndpoint,
  type DraftLinkRule,
  type Workspace,
  type LinkMedium,
  type MountRole,
} from "./workspace";

interface LinkRuleEditorProps {
  workspace: Workspace;
  rule: DraftLinkRule;
  /** The allocator's own outcome for this rule from the last resolve —
   *  displays report it; nothing here re-derives capacity. Null while the
   *  world is unresolved (show nothing, the existing precedent). */
  allocation: BuilderRuleAllocation | null;
  onUpdate: (patch: Partial<DraftLinkRule>) => void;
  onUpdateEndpoint: (side: "a" | "b", patch: Partial<DraftLinkEndpoint>) => void;
  /** Null while a change is being applied: removing waits for it. */
  onRemove: (() => void) | null;
  /** The resolved world's segments, each with the terminal roles and media
   *  its nodes carry. Null while the world is unresolved. */
  segments: ReadonlyArray<BuilderWorldSegment> | null;
  /** Re-point an endpoint through VS-API and surface its derivation notice. */
  onRepoint: (side: "a" | "b", newSegmentId: string) => Promise<string>;
  authoring: BuilderVisualAuthoringFacts;
}

function EndpointCard({
  title,
  endpoint,
  other,
  workspace,
  segments,
  onUpdate,
  onSegmentChange,
  authoring,
}: {
  title: string;
  endpoint: DraftLinkEndpoint;
  other: DraftLinkEndpoint;
  workspace: Workspace;
  segments: ReadonlyArray<BuilderWorldSegment> | null;
  onUpdate: (patch: Partial<DraftLinkEndpoint>) => void;
  onSegmentChange: (newSegmentId: string) => void;
  authoring: BuilderVisualAuthoringFacts;
}) {
  const placed = placedSegments(workspace);
  const carries = (segmentId: string, role: MountRole, medium: LinkMedium) =>
    segments?.some(
      (segment) =>
        segment.segment_id === segmentId &&
        segment.capabilities.some(
          (capability) => capability.endpoint_role === role && capability.medium === medium,
        ),
    ) === true;
  const bothCarry = (role: MountRole, medium: LinkMedium) =>
    carries(endpoint.segment_id, role, medium) && carries(other.segment_id, role, medium);
  // Combinations neither side can form remain visible and disabled with
  // the reason. An unresolved world means nothing is disabled.
  const known = segments !== null;
  const roleDisabled = (role: MountRole) =>
    known && !authoring.link_media.some((choice) => bothCarry(role, choice.id));
  const mediumDisabled = (medium: LinkMedium) =>
    endpoint.role !== null && known && !bothCarry(endpoint.role, medium);
  return (
    <EditorCard
      title={title}
      open
      summary={
        <>
          {endpoint.role ?? "role incomplete"} · {endpoint.medium ?? "medium incomplete"}
          {endpoint.tag ? ` · tag ${endpoint.tag}` : ""}
        </>
      }
    >
        <SelectField
          stack
          label="segment"
          ariaLabel={`${title} segment`}
          value={endpoint.segment_id}
          onChange={(segment_id) => onSegmentChange(segment_id)}
          options={[
            ...placed.map((segment) => ({
              value: segment.segment_id,
              label: `${segment.label} (${segment.kind})`,
            })),
            ...(placed.some((s) => s.segment_id === endpoint.segment_id)
              ? []
              : [{ value: endpoint.segment_id, label: `${endpoint.segment_id} (removed)` }]),
          ]}
        />
        <Field
          label="scope to tag"
          placeholder="every node in the segment"
          value={endpoint.tag ?? ""}
          onChange={(value) => onUpdate({ tag: value.trim() || null })}
        />
        <SelectField
          label="terminal role"
          ariaLabel={`${title} terminal role`}
          value={endpoint.role}
          onChange={(value) => onUpdate({ role: value as DraftLinkEndpoint["role"] })}
          options={authoring.mount_roles.map((choice) => ({
            value: choice.id,
            label: `${choice.label} \u2014 ${choice.description}`,
            disabled: roleDisabled(choice.id),
            title: roleDisabled(choice.id)
              ? `no ${choice.label} terminals on both ends`
              : choice.description,
          }))}
        />
        <SelectField
          label="medium"
          ariaLabel={`${title} medium`}
          value={endpoint.medium}
          onChange={(value) => onUpdate({ medium: value as DraftLinkEndpoint["medium"] })}
          options={authoring.link_media.map((choice) => ({
            value: choice.id,
            label: choice.label,
            disabled: mediumDisabled(choice.id),
            title: mediumDisabled(choice.id)
              ? `no ${endpoint.role} ${choice.label} terminals on both ends`
              : undefined,
          }))}
        />
        <NullableNumberField
          label="min elevation"
          placeholder="none"
          suffix="deg"
          value={endpoint.min_elevation_deg}
          onChange={(min_elevation_deg) => onUpdate({ min_elevation_deg })}
        />
    </EditorCard>
  );
}

export function LinkRuleEditor({
  workspace,
  rule,
  onUpdate,
  onUpdateEndpoint,
  onRemove,
  segments,
  onRepoint,
  allocation,
  authoring,
}: LinkRuleEditorProps) {
  const warnings = linkWarnings(workspace);
  const topologyRequiresN = authoring.topology_modes.find(
    (choice) => choice.id === rule.topology_mode,
  )?.requires_n;
  const [rederiveNotice, setRederiveNotice] = useState<string | null>(null);
  const repoint = (side: "a" | "b") => (newSegmentId: string) => {
    setRederiveNotice("re-deriving through VS-API…");
    void onRepoint(side, newSegmentId).then(
      setRederiveNotice,
      (cause) => setRederiveNotice(cause instanceof Error ? cause.message : String(cause)),
    );
  };
  return (
    <div className="builder-inspector-stack" data-testid="builder-link-editor">
      {/* The card shows the id read-only. A rename is a YAML edit. */}
      <KeyValueRow label="id">{rule.rule_id}</KeyValueRow>

      <EndpointCard
        title="Endpoint A"
        endpoint={rule.a}
        other={rule.b}
        workspace={workspace}
        segments={segments}
        onUpdate={(patch) => onUpdateEndpoint("a", patch)}
        onSegmentChange={repoint("a")}
        authoring={authoring}
      />
      <EndpointCard
        title="Endpoint B"
        endpoint={rule.b}
        other={rule.a}
        workspace={workspace}
        segments={segments}
        onUpdate={(patch) => onUpdateEndpoint("b", patch)}
        onSegmentChange={repoint("b")}
        authoring={authoring}
      />
      {rederiveNotice && (
        <div className="builder-library-note" data-testid="rederive-note">
          {rederiveNotice}
        </div>
      )}

      <div className="builder-preset-row" role="radiogroup" aria-label="Topology">
        {authoring.topology_modes.map((choice) => (
          <Button
            key={choice.id}
            active={rule.topology_mode === choice.id}
            onClick={() => onUpdate({ topology_mode: choice.id })}
          >
            {choice.label}
          </Button>
        ))}
      </div>
      {topologyRequiresN && (
        <NumberField
          label="N"
          value={rule.topology_n}
          min={1}
          integer
          suffix="neighbors"
          onChange={(topology_n) => onUpdate({ topology_n })}
        />
      )}
      {allocation && allocation.per_node.length > 0 && (() => {
        // The tightest node is one node — report its own numbers, never
        // minima composed across different nodes (that can describe a node
        // that does not exist). Whether the ask fits is the allocator's
        // verdict: a fixed rule that cannot allocate walls the resolve;
        // access is runtime-scheduled. No client-side prediction here.
        const tightest = allocation.per_node.reduce((best, n) =>
          n.free < best.free || (n.free === best.free && n.matching < best.matching)
            ? n
            : best,
        );
        return (
          <div className="builder-site-derived" data-testid="allocation-facts">
            {allocation.kind === "access"
              ? `allocator: ${allocation.allocated_pairs} candidate pair${
                  allocation.allocated_pairs === 1 ? "" : "s"
                } · tightest node has ${tightest.matching} matching interface${
                  tightest.matching === 1 ? "" : "s"
                } — runtime schedules within them`
              : `allocator: ${allocation.allocated_pairs} pair${
                  allocation.allocated_pairs === 1 ? "" : "s"
                } · tightest node has ${tightest.free} of ${tightest.matching} matching interface${
                  tightest.matching === 1 ? "" : "s"
                } free`}
          </div>
        );
      })()}
      <NullableNumberField
        label="max range"
        placeholder="unlimited"
        suffix="km"
        min={1}
        value={rule.max_range_km}
        onChange={(max_range_km) => onUpdate({ max_range_km })}
      />
      <div className="builder-preset-row">
        <Button active={rule.enabled} onClick={() => onUpdate({ enabled: !rule.enabled })}>
          {rule.enabled ? "enabled" : "disabled"}
        </Button>
        <Button variant="danger" onClick={onRemove ?? undefined} disabled={onRemove === null}>
          Remove rule
        </Button>
      </div>

      {warnings.map((warning) => (
        <div className="builder-warning" key={warning}>
          {warning}
        </div>
      ))}
    </div>
  );
}
