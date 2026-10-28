// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
/** Routing editors — domains and boundaries.
 *
 *  Link rules say what may communicate; routing domains are membership: a
 *  protocol over member segments (whole-segment membership — per-terminal
 *  membership is a gated grammar change). Timers are the expert card;
 *  engine defaults apply when unset. bgp/static are offered and the
 *  resolver's gate speaks verbatim on resolve. Boundaries are controlled
 *  exchanges over a fixed link rule (never access — those links schedule);
 *  v1 export is the shipped exchange: originated prefixes both ways via
 *  peer loopback.
 *
 *  A domain's window edits the domain as a pending edit. It shows the card
 *  while the backend states a card value for the domain. Otherwise it shows
 *  the field editor over the domain's grammar JSON. An edit can move the
 *  window from one to the other.
 */

import { Button } from "../ui/Button";
import { KeyValueRow } from "../ui/KeyValueRow";
import {
  CheckboxField,
  EditorCard,
  NullableNumberField,
  SelectField,
} from "./editorKit";
import type {
  BuilderVisualAuthoringFacts,
  BuilderVisualObjectControlEdit,
  BuilderVisualObjectEditResult,
  BuilderVisualRoutingDomain,
  BuilderVisualRoutingDomainWidget,
  RoutingProtocol,
} from "./generated/builderApi";
import {
  PendingObjectFieldEditor,
  PendingObjectIssues,
  useCardValueEdits,
  useLatestObjectChange,
} from "./pendingEditKit";
import type { ListedRoutingDomain } from "./sessionListing";
import {
  placedSegments,
  routingWarnings,
  type Adapter,
  type DraftBoundary,
  type Workspace,
} from "./workspace";

/** One change of a routing domain: its whole card value, or a field-editor batch. */
export type RoutingDomainChange = BuilderVisualRoutingDomainWidget | BuilderVisualObjectControlEdit;

interface DomainEditorProps {
  workspace: Workspace;
  /** The domain's card value as the card shows it. */
  domain: BuilderVisualRoutingDomain;
  /** Change the domain. A typed change waits for a pause in typing. */
  onUpdate: (patch: Partial<BuilderVisualRoutingDomain>, typed?: boolean) => void;
  /** Null while the domain cannot be removed: a change is being applied, or a
   *  link or routing edit is pending. */
  onRemove: (() => void) | null;
  authoring: BuilderVisualAuthoringFacts;
}

export function RoutingDomainEditor({
  workspace,
  domain,
  onUpdate,
  onRemove,
  authoring,
}: DomainEditorProps) {
  const placed = placedSegments(workspace);
  const members = domain.member_segment_ids ?? [];
  const hello = domain.hello_interval_s ?? null;
  const hold = domain.hold_interval_s ?? null;
  // A member the session no longer places stays listed, so it can be removed.
  const removedMembers = members.filter(
    (member) => !placed.some((segment) => segment.segment_id === member),
  );
  const timersUseDefaults = hello === null && hold === null;
  const timersComplete = hello !== null && hold !== null;
  const selectedProtocol = authoring.routing_protocols.find(
    (choice) => choice.id === domain.protocol,
  );
  const toggleMember = (segmentId: string) =>
    onUpdate({
      member_segment_ids: members.includes(segmentId)
        ? members.filter((id) => id !== segmentId)
        : [...members, segmentId],
    });
  return (
    <div className="builder-inspector-stack" data-testid="builder-domain-editor">
      <SelectField
        label="protocol"
        ariaLabel="Routing protocol"
        value={domain.protocol ?? null}
        onChange={(value) => {
          const protocol = value as RoutingProtocol;
          // Timers are IGP-only grammar; clear them on the way out.
          onUpdate(
            authoring.routing_protocols.find((choice) => choice.id === protocol)
              ?.timer_fields
              ? { protocol }
              : { protocol, hello_interval_s: null, hold_interval_s: null },
          );
        }}
        options={authoring.routing_protocols.map((choice) => ({
          value: choice.id,
          label:
            choice.label + (choice.runtime_supported ? "" : " — runtime-gated"),
          title: choice.support_note ?? undefined,
        }))}
      />

      <EditorCard
        title="Members"
        open
        summary={
          <>
            {members.length - removedMembers.length} of {placed.length} segments
            {removedMembers.length > 0 ? ` · ${removedMembers.length} removed` : ""}
          </>
        }
      >
          {placed.map((segment) => (
            <CheckboxField
              key={segment.segment_id}
              label={segment.label}
              checked={members.includes(segment.segment_id)}
              onChange={() => toggleMember(segment.segment_id)}
            />
          ))}
          {removedMembers.map((member) => (
            <CheckboxField
              key={member}
              label={`${member} (removed)`}
              checked
              onChange={() => toggleMember(member)}
            />
          ))}
          <div className="builder-site-derived">
            whole segments join a domain — per-terminal membership is a
            pending grammar change
          </div>
      </EditorCard>

      {selectedProtocol?.timer_fields && (
        <EditorCard
          title="Timers"
          open
          summary={
            timersComplete
              ? `hello ${hello}s · hold ${hold}s`
              : timersUseDefaults
                ? "engine defaults"
                : "timers incomplete"
          }
        >
            <div className="builder-preset-row">
              <Button
                active={timersUseDefaults}
                onClick={() => onUpdate({ hello_interval_s: null, hold_interval_s: null })}
              >
                engine defaults
              </Button>
            </div>
            <NullableNumberField
              label="hello"
              placeholder="engine default"
              value={hello}
              min={1}
              integer
              suffix="s"
              onChange={(hello_interval_s) => onUpdate({ hello_interval_s }, true)}
            />
            <NullableNumberField
              label="hold"
              placeholder="engine default"
              value={hold}
              min={1}
              integer
              suffix="s"
              onChange={(hold_interval_s) => onUpdate({ hold_interval_s }, true)}
            />
        </EditorCard>
      )}

      <div className="builder-preset-row">
        <Button
          variant="danger"
          onClick={onRemove ?? undefined}
          disabled={onRemove === null}
          title={
            onRemove === null
              ? "Removing waits while a change is being applied or a link or routing edit is pending."
              : undefined
          }
        >
          Remove domain
        </Button>
      </div>
    </div>
  );
}

/** The card over a domain the backend states a card value for. Each change is
 *  sent as the whole card value. */
function RoutingDomainCard({
  card,
  editPending,
  send,
  onRemove,
  ...shared
}: Omit<DomainEditorProps, "domain" | "onUpdate"> & {
  card: BuilderVisualRoutingDomain;
  /** Whether an edit of the domain is pending. */
  editPending: boolean;
  send: (domain: BuilderVisualRoutingDomain) => Promise<boolean>;
}) {
  const edits = useCardValueEdits(card, send);
  // A removal starts from the accepted domain, so it waits while an edit of
  // the domain is pending or still being checked.
  const edited = editPending || edits.checking;
  return (
    <>
      <RoutingDomainEditor
        {...shared}
        domain={edits.shown}
        onUpdate={(patch, typed) => edits.change((domain) => ({ ...domain, ...patch }), typed)}
        onRemove={edited ? null : onRemove}
      />
      {edits.checking && <div className="builder-site-derived">Checking the edit…</div>}
      {edits.error && <div className="builder-warning">{edits.error}</div>}
    </>
  );
}

interface RoutingDomainWindowProps extends Omit<DomainEditorProps, "domain" | "onUpdate"> {
  /** The domain as the page lists it: the accepted domain with its pending
   *  edit laid over it. */
  listed: ListedRoutingDomain;
  /** The accepted revision. The field editor is built at it. */
  appliedRevision: number;
  /** Send one change of the domain. It settles once the backend's answer is
   *  held as the domain's pending edit. */
  onEdit: (change: RoutingDomainChange) => Promise<unknown>;
  /** Ask the backend for the domain's field editor without changing the domain. */
  onDescribe: () => Promise<BuilderVisualObjectEditResult>;
}

/** One routing domain's window: the card while the backend states a card
 *  value for the domain, otherwise the field editor, then the issues of its
 *  pending edit. */
export function RoutingDomainWindow({
  listed,
  appliedRevision,
  onEdit,
  onDescribe,
  ...shared
}: RoutingDomainWindowProps) {
  const send = useLatestObjectChange(onEdit);
  return (
    <div className="builder-inspector-stack" data-testid="builder-domain-window">
      {/* The window shows the id read-only. A rename is a YAML edit. */}
      <KeyValueRow label="id">{listed.id}</KeyValueRow>
      {listed.pending !== null && (
        <div className="builder-site-derived">
          Pending edit. It is not applied to the session yet.
        </div>
      )}
      {listed.card !== null ? (
        <RoutingDomainCard
          {...shared}
          card={listed.card}
          editPending={listed.pending !== null}
          send={(domain) => send({ kind: "routing_domain", domain })}
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
    </div>
  );
}

interface BoundaryEditorProps {
  workspace: Workspace;
  boundary: DraftBoundary;
  onUpdate: (patch: Partial<DraftBoundary>) => void;
  /** Null while a change is being applied: removing waits for it. */
  onRemove: (() => void) | null;
  authoring: BuilderVisualAuthoringFacts;
}

export function BoundaryEditor({
  workspace,
  boundary,
  onUpdate,
  onRemove,
  authoring,
}: BoundaryEditorProps) {
  // Boundaries run over fixed links; access rules schedule on visibility
  // and are excluded here — the same wall the resolver enforces.
  const fixedRules = workspace.links.filter(
    (rule) => rule.a.role !== "access" && rule.b.role !== "access",
  );
  return (
    <div className="builder-inspector-stack" data-testid="builder-boundary-editor">
      <SelectField
        stack
        label="over link rule — fixed links only (access rules schedule)"
        ariaLabel="Boundary link rule"
        value={boundary.over_rule_id}
        onChange={(over_rule_id) => onUpdate({ over_rule_id })}
        options={[
          ...fixedRules.map((rule) => ({
            value: rule.rule_id,
            label: rule.rule_id,
          })),
          ...(fixedRules.some((rule) => rule.rule_id === boundary.over_rule_id)
            ? []
            : [
                {
                  value: boundary.over_rule_id,
                  label: boundary.over_rule_id || "(pick a fixed rule)",
                },
              ]),
        ]}
      />
      <SelectField
        label="adapter"
        ariaLabel="Boundary adapter"
        value={boundary.adapter}
        onChange={(value) => onUpdate({ adapter: value as Adapter })}
        options={authoring.boundary_adapters.map((choice) => ({
          value: choice.id,
          label:
            choice.label + (choice.runtime_supported ? "" : " — runtime-gated"),
          title: choice.support_note ?? undefined,
        }))}
      />
      {(["from_domain_id", "to_domain_id"] as const).map((side) => (
        <SelectField
          key={side}
          label={side === "from_domain_id" ? "between" : "and"}
          ariaLabel={side === "from_domain_id" ? "From domain" : "To domain"}
          value={boundary[side]}
          onChange={(value) => onUpdate({ [side]: value })}
          options={workspace.routing_domains.map((domain) => ({
            value: domain.domain_id,
            label: domain.domain_id,
          }))}
        />
      ))}
      <CheckboxField
        label="export loopbacks"
        checked={boundary.export_node_loopbacks}
        onChange={(export_node_loopbacks) => onUpdate({ export_node_loopbacks })}
      />
      <div className="builder-site-derived">
        exchanges each domain's originated prefixes both ways, installed via
        the peer's loopback
      </div>

      {routingWarnings(workspace).map((warning) => (
        <div className="builder-warning" key={warning}>
          {warning}
        </div>
      ))}
      <div className="builder-preset-row">
        <Button variant="danger" onClick={onRemove ?? undefined} disabled={onRemove === null}>
          Remove boundary
        </Button>
      </div>
    </div>
  );
}
