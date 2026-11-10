// Copyright 2024-2026 .chance (dotchance)
// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms under AGPL-3.0 Section 7 are in the NOTICE file.
/** Link rule editor — comms intent between placed segments.
 *
 *  A rule declares who may link; OME computes feasibility from geometry,
 *  terminal limits, and runtime state. Role defaults seed the rule when two
 *  segments are connected (isl for a fabric, crosslink space↔space, access
 *  ground↔space); every value is then owned by the user. Tag scopes are how
 *  one ground segment serves multiple constellations differently. The LOS
 *  candidate preview on the canvas and the dark-rule notes come from the
 *  resolver's expansion of the rule the backend writes from this card.
 *
 *  The window edits the rule as a pending edit. It shows the card while the
 *  backend states a card value for the rule. Otherwise it shows the field
 *  editor over the rule's grammar JSON. An edit can move the window from one
 *  to the other.
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
import type {
  BuilderVisualAuthoringFacts,
  BuilderVisualLinkEndpoint,
  BuilderVisualLinkRule,
  BuilderVisualLinkRuleWidget,
  BuilderVisualObjectControlEdit,
  BuilderVisualObjectEditResult,
  MountRole,
  TerminalMedium,
} from "./generated/builderApi";
import {
  PendingObjectFieldEditor,
  PendingObjectIssues,
  RemoveAtOnceButton,
  useCardValueEdits,
  useLatestObjectChange,
} from "./pendingEditKit";
import type { ListedLinkRule } from "./sessionListing";
import type { SessionSegment } from "./sessionSegments";

/** One change of a link rule: its whole card value, or a field-editor batch. */
export type LinkRuleChange = BuilderVisualLinkRuleWidget | BuilderVisualObjectControlEdit;

/** Why a segment change waits while a change of the rule is checked. */
const REPOINT_WAITS_FOR_CHECK =
  "A segment change is sent with this rule's edit. Wait until the edit is checked.";

interface LinkRuleEditorProps {
  /** Every segment the session declares. */
  placed: ReadonlyArray<SessionSegment>;
  /** The rule's card value as the card shows it. */
  rule: BuilderVisualLinkRule;
  /** The allocator's own outcome for this rule from the last resolve —
   *  displays report it; nothing here re-derives capacity. Null while the
   *  world is unresolved (show nothing, the existing precedent). */
  allocation: BuilderRuleAllocation | null;
  /** Change the rule. A typed change waits for a pause in typing. */
  onUpdate: (patch: Partial<BuilderVisualLinkRule>, typed?: boolean) => void;
  onUpdateEndpoint: (
    side: "a" | "b",
    patch: Partial<BuilderVisualLinkEndpoint>,
    typed?: boolean,
  ) => void;
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
  placed,
  segments,
  onUpdate,
  onSegmentChange,
  authoring,
}: {
  title: string;
  endpoint: BuilderVisualLinkEndpoint;
  other: BuilderVisualLinkEndpoint;
  /** Every segment the session declares. */
  placed: ReadonlyArray<SessionSegment>;
  segments: ReadonlyArray<BuilderWorldSegment> | null;
  onUpdate: (patch: Partial<BuilderVisualLinkEndpoint>, typed?: boolean) => void;
  onSegmentChange: (newSegmentId: string) => void;
  authoring: BuilderVisualAuthoringFacts;
}) {
  const segmentId = endpoint.segment_id ?? "";
  const role = endpoint.role ?? null;
  const medium = endpoint.medium ?? null;
  const carries = (candidate: string, carriedRole: MountRole, carriedMedium: TerminalMedium) =>
    segments?.some(
      (segment) =>
        segment.segment_id === candidate &&
        segment.capabilities.some(
          (capability) =>
            capability.endpoint_role === carriedRole && capability.medium === carriedMedium,
        ),
    ) === true;
  const bothCarry = (carriedRole: MountRole, carriedMedium: TerminalMedium) =>
    carries(segmentId, carriedRole, carriedMedium) &&
    carries(other.segment_id ?? "", carriedRole, carriedMedium);
  // Combinations neither side can form remain visible and disabled with
  // the reason. An unresolved world means nothing is disabled.
  const known = segments !== null;
  const roleDisabled = (choice: MountRole) =>
    known && !authoring.link_media.some((media) => bothCarry(choice, media.id));
  const mediumDisabled = (choice: TerminalMedium) =>
    role !== null && known && !bothCarry(role, choice);
  return (
    <EditorCard
      title={title}
      open
      summary={
        <>
          {role ?? "role incomplete"} · {medium ?? "medium incomplete"}
          {endpoint.tag ? ` · tag ${endpoint.tag}` : ""}
        </>
      }
    >
        <SelectField
          stack
          label="segment"
          ariaLabel={`${title} segment`}
          value={segmentId}
          onChange={(segment_id) => onSegmentChange(segment_id)}
          options={[
            ...placed.map((segment) => ({
              value: segment.segment_id,
              label: `${segment.label} (${segment.kind})`,
            })),
            ...(segmentId === "" || placed.some((s) => s.segment_id === segmentId)
              ? []
              : [{ value: segmentId, label: `${segmentId} (removed)` }]),
          ]}
        />
        <Field
          label="scope to tag"
          placeholder="every node in the segment"
          value={endpoint.tag ?? ""}
          onChange={(value) => onUpdate({ tag: value.trim() || null }, true)}
        />
        <SelectField
          label="terminal role"
          ariaLabel={`${title} terminal role`}
          value={role}
          onChange={(value) => onUpdate({ role: value as MountRole })}
          options={authoring.mount_roles.map((choice) => ({
            value: choice.id,
            label: `${choice.label} — ${choice.description}`,
            disabled: roleDisabled(choice.id),
            title: roleDisabled(choice.id)
              ? `no ${choice.label} terminals on both ends`
              : choice.description,
          }))}
        />
        <SelectField
          label="medium"
          ariaLabel={`${title} medium`}
          value={medium}
          onChange={(value) => onUpdate({ medium: value as TerminalMedium })}
          options={authoring.link_media.map((choice) => ({
            value: choice.id,
            label: choice.label,
            disabled: mediumDisabled(choice.id),
            title: mediumDisabled(choice.id)
              ? `no ${role} ${choice.label} terminals on both ends`
              : undefined,
          }))}
        />
        <NullableNumberField
          label="min elevation"
          placeholder="none"
          suffix="deg"
          value={endpoint.min_elevation_deg ?? null}
          onChange={(min_elevation_deg) => onUpdate({ min_elevation_deg }, true)}
        />
    </EditorCard>
  );
}

export function LinkRuleEditor({
  placed,
  rule,
  onUpdate,
  onUpdateEndpoint,
  segments,
  onRepoint,
  allocation,
  authoring,
}: LinkRuleEditorProps) {
  const a = rule.a ?? {};
  const b = rule.b ?? {};
  // An omitted `enabled` means an enabled rule, as in the grammar.
  const enabled = rule.enabled !== false;
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
      <EndpointCard
        title="Endpoint A"
        endpoint={a}
        other={b}
        placed={placed}
        segments={segments}
        onUpdate={(patch, typed) => onUpdateEndpoint("a", patch, typed)}
        onSegmentChange={repoint("a")}
        authoring={authoring}
      />
      <EndpointCard
        title="Endpoint B"
        endpoint={b}
        other={a}
        placed={placed}
        segments={segments}
        onUpdate={(patch, typed) => onUpdateEndpoint("b", patch, typed)}
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
          value={rule.topology_n ?? null}
          min={1}
          integer
          suffix="neighbors"
          onChange={(topology_n) => onUpdate({ topology_n }, true)}
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
        value={rule.max_range_km ?? null}
        onChange={(max_range_km) => onUpdate({ max_range_km }, true)}
      />
      <div className="builder-preset-row">
        <Button active={enabled} onClick={() => onUpdate({ enabled: !enabled })}>
          {enabled ? "enabled" : "disabled"}
        </Button>
      </div>
    </div>
  );
}

/** The card over a rule the backend states a card value for. Each change is
 *  sent as the whole card value. */
function LinkRuleCard({
  card,
  send,
  hold,
  onRepoint,
  ...shared
}: Omit<LinkRuleEditorProps, "rule" | "onUpdate" | "onUpdateEndpoint"> & {
  card: BuilderVisualLinkRule;
  send: (rule: BuilderVisualLinkRule) => Promise<boolean>;
  hold: () => () => void;
}) {
  const edits = useCardValueEdits(card, send, hold);
  // A segment change is sent with the rule's pending edit, so it waits only
  // while a change of the rule is still being checked.
  return (
    <>
      <LinkRuleEditor
        {...shared}
        rule={edits.shown}
        onUpdate={(patch, typed) => edits.change((rule) => ({ ...rule, ...patch }), typed)}
        onUpdateEndpoint={(side, patch, typed) =>
          edits.change((rule) => ({ ...rule, [side]: { ...rule[side], ...patch } }), typed)
        }
        onRepoint={(side, newSegmentId) =>
          edits.checking
            ? Promise.reject(new Error(REPOINT_WAITS_FOR_CHECK))
            : onRepoint(side, newSegmentId)
        }
      />
      {edits.checking && <div className="builder-site-derived">Checking the edit…</div>}
      {edits.error && <div className="builder-warning">{edits.error}</div>}
    </>
  );
}

interface LinkRuleWindowProps extends Omit<
  LinkRuleEditorProps,
  "rule" | "onUpdate" | "onUpdateEndpoint"
> {
  /** The rule as the page lists it: the accepted rule with its pending edit
   *  laid over it. */
  listed: ListedLinkRule;
  /** The accepted revision. The field editor is built at it. */
  appliedRevision: number;
  /** Send one change of the rule. It settles once the backend's answer is
   *  held as the rule's pending edit. */
  onEdit: (change: LinkRuleChange) => Promise<unknown>;
  /** Ask the backend for the rule's field editor without changing the rule. */
  onDescribe: () => Promise<BuilderVisualObjectEditResult>;
  /** Mark the rule as holding a change not yet answered; call the function
   *  returned once it is answered or dropped. */
  onHold: () => () => void;
  /** Whether a change of the rule is being typed or checked. */
  checking: boolean;
  /** Remove the rule from the session at once, as one accepted change. Null
   *  while a change is being applied. */
  onRemove: (() => void) | null;
}

/** One link rule's window: the card while the backend states a card value for
 *  the rule, otherwise the field editor, then the issues of its pending edit
 *  and the rule's removal. */
export function LinkRuleWindow({
  listed,
  appliedRevision,
  onEdit,
  onDescribe,
  onHold,
  checking,
  onRemove,
  ...shared
}: LinkRuleWindowProps) {
  const send = useLatestObjectChange(onEdit);
  return (
    <div className="builder-inspector-stack" data-testid="builder-link-window">
      {/* The window shows the id read-only. A rename is a YAML edit. */}
      <KeyValueRow label="id">{listed.id}</KeyValueRow>
      {listed.pending !== null && (
        <div className="builder-site-derived">
          Pending edit. It is not applied to the session yet.
        </div>
      )}
      {listed.card !== null ? (
        <LinkRuleCard
          {...shared}
          card={listed.card}
          send={(rule) => send({ kind: "link_rule", rule })}
          hold={onHold}
        />
      ) : (
        <PendingObjectFieldEditor
          pending={listed.pending}
          appliedRevision={appliedRevision}
          describe={onDescribe}
          send={(commands) => send({ kind: "controls", commands })}
        />
      )}
      <PendingObjectIssues issues={listed.pending?.issues ?? []} />
      <RemoveAtOnceButton
        object="rule"
        onRemove={onRemove}
        pending={listed.pending !== null}
        checking={checking}
      />
    </div>
  );
}
