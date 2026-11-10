// Copyright 2024-2026 .chance (dotchance)
// SPDX-License-Identifier: AGPL-3.0-only
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
 *  window from one to the other. The boundary list has one window and is
 *  edited as one pending edit, each boundary through its card or its part of
 *  the list's field editor.
 */

import { useMemo, useState } from "react";
import { Button } from "../ui/Button";
import { KeyValueRow } from "../ui/KeyValueRow";
import {
  CheckboxField,
  EditorCard,
  NullableNumberField,
  SelectField,
  type SelectOption,
} from "./editorKit";
import {
  GraphicalControlTreeEditor,
  type BuilderControlMutation,
} from "./GraphicalControlTreeEditor";
import type {
  BuilderControlTree,
  BuilderObjectControl,
  BuilderObjectField,
  BuilderSequenceControl,
  BuilderVisualAuthoringFacts,
  BuilderVisualObjectControlEdit,
  BuilderVisualObjectEditResult,
  BuilderVisualRoutingBoundariesWidget,
  BuilderVisualRoutingBoundary,
  BuilderVisualRoutingDomain,
  BuilderVisualRoutingDomainWidget,
  RoutingBoundaryAdapter,
  RoutingProtocol,
} from "./generated/builderApi";
import {
  FieldEditorTreeWaiting,
  PendingObjectFieldEditor,
  PendingObjectIssues,
  RemoveAtOnceButton,
  useCardValueEdits,
  useFieldEditorTree,
  useLatestObjectChange,
  type FieldEditorTree,
} from "./pendingEditKit";
import type {
  ListedLinkRule,
  ListedRoutingBoundaries,
  ListedRoutingBoundary,
  ListedRoutingDomain,
} from "./sessionListing";
import type { SessionSegment } from "./sessionSegments";

/** One change of a routing domain: its whole card value, or a field-editor batch. */
export type RoutingDomainChange = BuilderVisualRoutingDomainWidget | BuilderVisualObjectControlEdit;

interface DomainEditorProps {
  /** Every segment the session declares. */
  placed: ReadonlyArray<SessionSegment>;
  /** The domain's card value as the card shows it. */
  domain: BuilderVisualRoutingDomain;
  /** Change the domain. A typed change waits for a pause in typing. */
  onUpdate: (patch: Partial<BuilderVisualRoutingDomain>, typed?: boolean) => void;
  authoring: BuilderVisualAuthoringFacts;
}

export function RoutingDomainEditor({
  placed,
  domain,
  onUpdate,
  authoring,
}: DomainEditorProps) {
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
    </div>
  );
}

/** The card over a domain the backend states a card value for. Each change is
 *  sent as the whole card value. */
function RoutingDomainCard({
  card,
  send,
  hold,
  ...shared
}: Omit<DomainEditorProps, "domain" | "onUpdate"> & {
  card: BuilderVisualRoutingDomain;
  send: (domain: BuilderVisualRoutingDomain) => Promise<boolean>;
  hold: () => () => void;
}) {
  const edits = useCardValueEdits(card, send, hold);
  return (
    <>
      <RoutingDomainEditor
        {...shared}
        domain={edits.shown}
        onUpdate={(patch, typed) => edits.change((domain) => ({ ...domain, ...patch }), typed)}
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
  /** Mark the domain as holding a change not yet answered; call the function
   *  returned once it is answered or dropped. */
  onHold: () => () => void;
  /** Whether a change of the domain is being typed or checked. */
  checking: boolean;
  /** Remove the domain from the session at once, as one accepted change. Null
   *  while a change is being applied. */
  onRemove: (() => void) | null;
}

/** One routing domain's window: the card while the backend states a card
 *  value for the domain, otherwise the field editor, then the issues of its
 *  pending edit and the domain's removal. */
export function RoutingDomainWindow({
  listed,
  appliedRevision,
  onEdit,
  onDescribe,
  onHold,
  checking,
  onRemove,
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
          send={(domain) => send({ kind: "routing_domain", domain })}
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
        object="domain"
        onRemove={onRemove}
        pending={listed.pending !== null}
        checking={checking}
      />
    </div>
  );
}

interface BoundaryEditorProps {
  /** The boundary's card value as the card shows it. */
  boundary: BuilderVisualRoutingBoundary;
  /** The link rules and routing domains as the page lists them: the
   *  boundary's choices. */
  linkRules: ReadonlyArray<ListedLinkRule>;
  routingDomains: ReadonlyArray<ListedRoutingDomain>;
  onUpdate: (patch: Partial<BuilderVisualRoutingBoundary>) => void;
  authoring: BuilderVisualAuthoringFacts;
}

/** A reference field's options, with the value the boundary names kept when
 *  no listed object holds it, so the field shows what the boundary states. */
function withNamedValue(options: SelectOption[], named: string | null): SelectOption[] {
  return named === null || options.some((option) => option.value === named)
    ? options
    : [...options, { value: named, label: `${named} (not in the session)` }];
}

function BoundaryEditor({
  boundary,
  linkRules,
  routingDomains,
  onUpdate,
  authoring,
}: BoundaryEditorProps) {
  // Boundaries run over fixed links. Access rules schedule on visibility, so
  // the resolver refuses a boundary over one. A rule whose card value states
  // an access endpoint is offered disabled.
  const ruleOptions = linkRules.map((rule) => {
    const access = rule.card?.a?.role === "access" || rule.card?.b?.role === "access";
    return {
      value: rule.id,
      label: access ? `${rule.id} (access)` : rule.id,
      disabled: access,
      title: access
        ? "Access links schedule on visibility; a boundary runs over a fixed link."
        : undefined,
    };
  });
  const domainOptions = routingDomains.map((domain) => ({ value: domain.id, label: domain.id }));
  const overRuleId = boundary.over_rule_id || null;
  // The card value's default for an omitted export_node_loopbacks is true.
  const exportLoopbacks = boundary.export_node_loopbacks !== false;
  return (
    <div className="builder-inspector-stack" data-testid="builder-boundary-editor">
      <SelectField
        stack
        label="over link rule — fixed links only (access rules schedule)"
        ariaLabel="Boundary link rule"
        value={overRuleId}
        onChange={(over_rule_id) => onUpdate({ over_rule_id })}
        options={withNamedValue(ruleOptions, overRuleId)}
      />
      <SelectField
        label="adapter"
        ariaLabel="Boundary adapter"
        value={boundary.adapter ?? null}
        onChange={(value) => onUpdate({ adapter: value as RoutingBoundaryAdapter })}
        options={authoring.boundary_adapters.map((choice) => ({
          value: choice.id,
          label:
            choice.label + (choice.runtime_supported ? "" : " — runtime-gated"),
          title: choice.support_note ?? undefined,
        }))}
      />
      {(["from_domain_id", "to_domain_id"] as const).map((side) => {
        const named = boundary[side] || null;
        return (
          <SelectField
            key={side}
            label={side === "from_domain_id" ? "between" : "and"}
            ariaLabel={side === "from_domain_id" ? "From domain" : "To domain"}
            value={named}
            onChange={(value) => onUpdate({ [side]: value })}
            options={withNamedValue(domainOptions, named)}
          />
        );
      })}
      <CheckboxField
        label="export loopbacks"
        checked={exportLoopbacks}
        onChange={(export_node_loopbacks) => onUpdate({ export_node_loopbacks })}
      />
      <div className="builder-site-derived">
        exchanges each domain's originated prefixes both ways, installed via
        the peer's loopback
      </div>
    </div>
  );
}

/** One change of the boundary list: the card value of every boundary, or a
 *  field-editor batch over the list. */
export type RoutingBoundariesChange =
  | BuilderVisualRoutingBoundariesWidget
  | BuilderVisualObjectControlEdit;

type BoundaryCards = ReadonlyArray<BuilderVisualRoutingBoundary | null>;

type TreeControl = BuilderObjectField["control"];

function isSequenceControl(control: TreeControl): control is BuilderSequenceControl {
  return "can_reorder" in control;
}

function isObjectControl(control: TreeControl): control is BuilderObjectControl {
  return "fields" in control || "empty_parameters" in control || "model_name" in control;
}

/** The list control of the boundary list's field editor; null when the tree
 *  holds none. */
function boundaryListControl(tree: BuilderControlTree): BuilderSequenceControl | null {
  const list = tree.root.fields?.find((field) => field.wire_name === "boundaries")?.control;
  return list !== undefined && isSequenceControl(list) ? list : null;
}

/** The field editor over one boundary: the subtree of the list's field editor
 *  at that boundary. Null when the list's field editor holds no such boundary. */
function boundarySubtree(tree: BuilderControlTree, index: number): BuilderControlTree | null {
  const item = boundaryListControl(tree)?.items?.find((entry) => entry.index === index)?.control;
  if (item === undefined || !isObjectControl(item)) return null;
  return { projection_revision: tree.projection_revision, root: item };
}

/** The field editor of a boundary no card writes back unchanged. */
function BoundaryFieldEditor({
  field,
  index,
  disabled,
  send,
}: {
  field: FieldEditorTree;
  index: number;
  disabled: boolean;
  send: (commands: ReadonlyArray<BuilderControlMutation>) => Promise<void>;
}) {
  if (field.kind !== "tree") return <FieldEditorTreeWaiting state={field} />;
  const subtree = boundarySubtree(field.tree, index);
  if (subtree === null) {
    return (
      <div className="builder-warning">
        The field editor of the boundary list holds no boundary {index + 1}.
      </div>
    );
  }
  return <GraphicalControlTreeEditor tree={subtree} disabled={disabled} onMutate={send} />;
}

interface BoundaryListShared {
  linkRules: ReadonlyArray<ListedLinkRule>;
  routingDomains: ReadonlyArray<ListedRoutingDomain>;
  authoring: BuilderVisualAuthoringFacts;
}

/** Each boundary of the list: its card while a card value states it,
 *  otherwise its part of the list's field editor. A card change is sent as
 *  the card value of every boundary. One change of the list is made at a
 *  time, since each builds on the list the last answer states. The cards wait
 *  while a field-editor batch or a removal is sent. The field editor and the
 *  removals wait while a card change is checked. */
function BoundaryItems({
  items,
  field,
  sendCards,
  sendControls,
  fieldBusy,
  checking,
  hold,
  onRemove,
  ...shared
}: BoundaryListShared & {
  items: ReadonlyArray<ListedRoutingBoundary>;
  field: FieldEditorTree;
  sendCards: (cards: BoundaryCards) => Promise<boolean>;
  sendControls: (commands: ReadonlyArray<BuilderControlMutation>) => Promise<void>;
  /** Whether a field-editor batch or a removal is being sent. */
  fieldBusy: boolean;
  /** Whether a change of the list is being typed or checked. */
  checking: boolean;
  hold: () => () => void;
  /** Remove the boundary at an index of the list as it now stands. */
  onRemove: (index: number) => void;
}) {
  const answered = useMemo(() => items.map((item) => item.card), [items]);
  const edits = useCardValueEdits<BoundaryCards>(answered, sendCards, hold);
  // A removal is a change of the list, so it waits for the one being made.
  const removalWaits = edits.checking || fieldBusy || checking;
  return (
    <>
      {edits.shown.map((card, index) => {
        const overRuleId =
          card === null ? (items[index]?.overRuleId ?? null) : card.over_rule_id || null;
        return (
          <EditorCard
            key={index}
            title={`Boundary ${index + 1}`}
            summary={overRuleId === null ? undefined : `over ${overRuleId}`}
            open
          >
            {card !== null ? (
              <fieldset className="catalog-draft-fieldset" disabled={fieldBusy}>
                <BoundaryEditor
                  {...shared}
                  boundary={card}
                  onUpdate={(patch) =>
                    edits.change((cards) =>
                      cards.map((other, at) =>
                        at === index && other !== null ? { ...other, ...patch } : other,
                      ),
                    )
                  }
                />
              </fieldset>
            ) : (
              <BoundaryFieldEditor
                field={field}
                index={index}
                disabled={edits.checking}
                send={sendControls}
              />
            )}
            <div className="builder-preset-row">
              <Button
                variant="danger"
                onClick={removalWaits ? undefined : () => onRemove(index)}
                disabled={removalWaits}
                title={
                  removalWaits
                    ? "Removing waits while another change of the list is being checked."
                    : "Removes the boundary from the list's pending edit"
                }
              >
                Remove boundary
              </Button>
            </div>
          </EditorCard>
        );
      })}
      {edits.checking && <div className="builder-site-derived">Checking the edit…</div>}
      {edits.error && <div className="builder-warning">{edits.error}</div>}
    </>
  );
}

interface BoundaryListWindowProps extends BoundaryListShared {
  /** The boundary list as the page lists it: the accepted boundaries, or the
   *  list's pending edit. */
  listed: ListedRoutingBoundaries;
  /** The accepted revision. The field editor is built at it. */
  appliedRevision: number;
  /** Send one change of the list. It settles once the backend's answer is
   *  held as the list's pending edit. */
  onEdit: (change: RoutingBoundariesChange) => Promise<unknown>;
  /** Ask the backend for the list's field editor without changing the list. */
  onDescribe: () => Promise<BuilderVisualObjectEditResult>;
  /** Mark the list as holding a change not yet answered; call the function
   *  returned once it is answered or dropped. */
  onHold: () => () => void;
  /** Whether a change of the list is being typed or checked. */
  checking: boolean;
}

/** The window of the routing boundary list. Its boundaries have no ids, so the
 *  list is edited as a whole, as one pending edit, and removing a boundary is
 *  a change of that edit. Each boundary shows its card or its part of the
 *  list's field editor. The issues of the list's pending edit follow. */
export function BoundaryListWindow({
  listed,
  appliedRevision,
  onEdit,
  onDescribe,
  onHold,
  checking,
  ...shared
}: BoundaryListWindowProps) {
  const send = useLatestObjectChange(onEdit);
  const items = listed.items;
  const field = useFieldEditorTree(
    listed.pending,
    appliedRevision,
    onDescribe,
    items === null || items.some((item) => item.card === null),
  );
  const [fieldBusy, setFieldBusy] = useState(false);
  const [removeFailure, setRemoveFailure] = useState<string | null>(null);
  const sendControls = async (commands: ReadonlyArray<BuilderControlMutation>) => {
    setFieldBusy(true);
    try {
      await send({ kind: "controls", commands });
    } finally {
      setFieldBusy(false);
    }
  };
  // A removal is the field editor's removal of the boundary's item, over the
  // tree the backend builds for the list as it now stands. That tree is asked
  // for first when none is known.
  const removeBoundary = async (index: number) => {
    setFieldBusy(true);
    setRemoveFailure(null);
    const release = onHold();
    try {
      const tree = field.kind === "tree" ? field.tree : (await onDescribe()).control_tree;
      const list = boundaryListControl(tree);
      if (list === null || !list.can_remove) {
        throw new Error("The field editor of the boundary list offers no removal.");
      }
      await send({
        kind: "controls",
        commands: [{ operation: "remove_item", control_id: list.control_id, index }],
      });
    } catch (cause) {
      setRemoveFailure(cause instanceof Error ? cause.message : String(cause));
    } finally {
      release();
      setFieldBusy(false);
    }
  };
  return (
    <div className="builder-inspector-stack" data-testid="builder-boundaries-window">
      {listed.pending !== null && (
        <div className="builder-site-derived">
          Pending edit. It is not applied to the session yet.
        </div>
      )}
      {items === null ? (
        // No card value states the list, so its boundaries cannot be told apart.
        field.kind === "tree" ? (
          <GraphicalControlTreeEditor tree={field.tree} onMutate={sendControls} />
        ) : (
          <FieldEditorTreeWaiting state={field} />
        )
      ) : items.length === 0 ? (
        <div className="builder-zone-empty">The list holds no boundary.</div>
      ) : (
        <BoundaryItems
          {...shared}
          items={items}
          field={field}
          sendCards={(boundaries) => send({ kind: "routing_boundaries", boundaries })}
          sendControls={sendControls}
          fieldBusy={fieldBusy}
          checking={checking}
          hold={onHold}
          onRemove={(index) => void removeBoundary(index)}
        />
      )}
      {removeFailure && <div className="builder-warning">{removeFailure}</div>}
      <PendingObjectIssues issues={listed.pending?.issues ?? []} />
    </div>
  );
}
