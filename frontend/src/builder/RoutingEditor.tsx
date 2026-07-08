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
 */

import { Button } from "../ui/Button";
import { CheckboxField, EditorCard, EditorName, NumberField, SelectField } from "./editorKit";
import {
  placedSegments,
  routingWarnings,
  type DraftBoundary,
  type DraftRoutingDomain,
  type Workspace,
} from "./workspace";

interface DomainEditorProps {
  workspace: Workspace;
  domain: DraftRoutingDomain;
  onUpdate: (patch: Partial<DraftRoutingDomain>) => void;
  onRemove: () => void;
  /** IG-2: focus the name when a create gesture opened this editor. */
  autoFocusName?: boolean;
}

export function RoutingDomainEditor({
  workspace,
  domain,
  onUpdate,
  onRemove,
  autoFocusName = false,
}: DomainEditorProps) {
  const placed = placedSegments(workspace);
  const isIgp = domain.protocol === "isis" || domain.protocol === "ospf";
  const explicitTimers = domain.hello_interval_s !== null;
  return (
    <div className="builder-inspector-stack" data-testid="builder-domain-editor">
      <EditorName
        value={domain.label}
        onChange={(label) => onUpdate({ label })}
        autoFocus={autoFocusName}
      />
      <SelectField
        label="protocol"
        ariaLabel="Routing protocol"
        value={domain.protocol}
        onChange={(value) => {
          const protocol = value as DraftRoutingDomain["protocol"];
          // Timers are IGP-only grammar; clear them on the way out.
          onUpdate(
            protocol === "isis" || protocol === "ospf"
              ? { protocol }
              : { protocol, hello_interval_s: null, hold_interval_s: null },
          );
        }}
        options={[
          { value: "isis", label: "IS-IS" },
          { value: "ospf", label: "OSPF" },
          { value: "bgp", label: "BGP" },
          { value: "static", label: "static" },
        ]}
      />

      <EditorCard
        title="Members"
        open
        summary={
          <>
            {domain.member_segment_ids.length} of {placed.length} segments
          </>
        }
      >
          {placed.map((segment) => {
            const member = domain.member_segment_ids.includes(segment.segment_id);
            return (
              <CheckboxField
                key={segment.segment_id}
                label={segment.label}
                checked={member}
                onChange={() =>
                  onUpdate({
                    member_segment_ids: member
                      ? domain.member_segment_ids.filter((id) => id !== segment.segment_id)
                      : [...domain.member_segment_ids, segment.segment_id],
                  })
                }
              />
            );
          })}
          <div className="builder-site-derived">
            whole segments join a domain — per-terminal membership is a
            pending grammar change
          </div>
      </EditorCard>

      {isIgp && (
        <EditorCard
          title="Timers"
          open
          summary={
            explicitTimers
              ? `hello ${domain.hello_interval_s}s · hold ${domain.hold_interval_s}s`
              : "engine defaults"
          }
        >
            <div className="builder-preset-row">
              <Button
                active={!explicitTimers}
                onClick={() => onUpdate({ hello_interval_s: null, hold_interval_s: null })}
              >
                engine defaults
              </Button>
              <Button
                active={explicitTimers}
                onClick={() => onUpdate({ hello_interval_s: 1, hold_interval_s: 3 })}
              >
                explicit
              </Button>
            </div>
            {explicitTimers && (
              <>
                <NumberField
                  label="hello"
                  value={domain.hello_interval_s ?? 1}
                  min={1}
                  integer
                  suffix="s"
                  onChange={(hello_interval_s) => onUpdate({ hello_interval_s })}
                />
                <NumberField
                  label="hold"
                  value={domain.hold_interval_s ?? 3}
                  min={2}
                  integer
                  suffix="s"
                  onChange={(hold_interval_s) => onUpdate({ hold_interval_s })}
                />
              </>
            )}
        </EditorCard>
      )}

      {routingWarnings(workspace).map((warning) => (
        <div className="builder-warning" key={warning}>
          {warning}
        </div>
      ))}
      <div className="builder-preset-row">
        <Button variant="danger" onClick={onRemove}>
          Remove domain
        </Button>
      </div>
    </div>
  );
}

interface BoundaryEditorProps {
  workspace: Workspace;
  boundary: DraftBoundary;
  onUpdate: (patch: Partial<DraftBoundary>) => void;
  onRemove: () => void;
}

export function BoundaryEditor({
  workspace,
  boundary,
  onUpdate,
  onRemove,
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
            label: rule.label || rule.rule_id,
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
        onChange={(value) => onUpdate({ adapter: value as DraftBoundary["adapter"] })}
        options={[
          { value: "static_ip", label: "static_ip" },
          { value: "bgp", label: "bgp" },
          { value: "dtn_bundle", label: "dtn_bundle" },
        ]}
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
            label: domain.label,
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
        <Button variant="danger" onClick={onRemove}>
          Remove boundary
        </Button>
      </div>
    </div>
  );
}
