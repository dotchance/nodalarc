// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
/** Routing editors — domains and boundaries.
 *
 *  Link rules say what MAY communicate; routing domains are membership: a
 *  protocol over member segments (whole-segment membership — per-terminal
 *  membership is a gated grammar change). Timers are the expert card;
 *  engine defaults apply when unset. bgp/static are offered and the
 *  resolver's gate speaks verbatim on resolve. Boundaries are controlled
 *  exchanges OVER a fixed link rule (never access — those links schedule);
 *  v1 export is the shipped exchange: originated prefixes both ways via
 *  peer loopback.
 */

import { Button } from "../ui/Button";
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
}

export function RoutingDomainEditor({
  workspace,
  domain,
  onUpdate,
  onRemove,
}: DomainEditorProps) {
  const placed = placedSegments(workspace);
  const isIgp = domain.protocol === "isis" || domain.protocol === "ospf";
  const explicitTimers = domain.hello_interval_s !== null;
  return (
    <div className="builder-inspector-stack" data-testid="builder-domain-editor">
      <label className="builder-field">
        <span className="builder-field-label">name</span>
        <span className="builder-field-input">
          <input
            type="text"
            value={domain.label}
            onChange={(e) => onUpdate({ label: e.target.value })}
          />
        </span>
      </label>
      <label className="builder-field">
        <span className="builder-field-label">protocol</span>
        <span className="builder-field-input">
          <select
            aria-label="Routing protocol"
            value={domain.protocol}
            onChange={(e) => {
              const protocol = e.target.value as DraftRoutingDomain["protocol"];
              // Timers are IGP-only grammar; clear them on the way out.
              onUpdate(
                protocol === "isis" || protocol === "ospf"
                  ? { protocol }
                  : { protocol, hello_interval_s: null, hold_interval_s: null },
              );
            }}
          >
            <option value="isis">IS-IS</option>
            <option value="ospf">OSPF</option>
            <option value="bgp">BGP</option>
            <option value="static">static</option>
          </select>
        </span>
      </label>

      <div className="builder-card builder-card--open">
        <div className="builder-card-head">
          <span className="builder-card-title">Members</span>
          <span className="builder-card-summary">
            {domain.member_segment_ids.length} of {placed.length} segments
          </span>
        </div>
        <div className="builder-card-body">
          {placed.map((segment) => {
            const member = domain.member_segment_ids.includes(segment.segment_id);
            return (
              <label className="builder-field" key={segment.segment_id}>
                <span className="builder-field-label">{segment.label}</span>
                <span className="builder-field-input">
                  <input
                    type="checkbox"
                    checked={member}
                    onChange={() =>
                      onUpdate({
                        member_segment_ids: member
                          ? domain.member_segment_ids.filter(
                              (id) => id !== segment.segment_id,
                            )
                          : [...domain.member_segment_ids, segment.segment_id],
                      })
                    }
                  />
                </span>
              </label>
            );
          })}
          <div className="builder-site-derived">
            whole segments join a domain — per-terminal membership is a
            pending grammar change
          </div>
        </div>
      </div>

      {isIgp && (
        <div className="builder-card builder-card--open">
          <div className="builder-card-head">
            <span className="builder-card-title">Timers</span>
            <span className="builder-card-summary">
              {explicitTimers
                ? `hello ${domain.hello_interval_s}s · hold ${domain.hold_interval_s}s`
                : "engine defaults"}
            </span>
          </div>
          <div className="builder-card-body">
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
                <label className="builder-field">
                  <span className="builder-field-label">hello</span>
                  <span className="builder-field-input">
                    <input
                      type="number"
                      min={1}
                      value={domain.hello_interval_s ?? 1}
                      onChange={(e) => {
                        const parsed = Math.max(1, Math.round(Number(e.target.value)));
                        if (Number.isFinite(parsed)) onUpdate({ hello_interval_s: parsed });
                      }}
                    />
                    <span className="builder-field-suffix">s</span>
                  </span>
                </label>
                <label className="builder-field">
                  <span className="builder-field-label">hold</span>
                  <span className="builder-field-input">
                    <input
                      type="number"
                      min={2}
                      value={domain.hold_interval_s ?? 3}
                      onChange={(e) => {
                        const parsed = Math.max(2, Math.round(Number(e.target.value)));
                        if (Number.isFinite(parsed)) onUpdate({ hold_interval_s: parsed });
                      }}
                    />
                    <span className="builder-field-suffix">s</span>
                  </span>
                </label>
              </>
            )}
          </div>
        </div>
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
  // Boundaries run over FIXED links; access rules schedule on visibility
  // and are excluded here — the same wall the resolver enforces.
  const fixedRules = workspace.links.filter(
    (rule) => rule.a.role !== "access" && rule.b.role !== "access",
  );
  return (
    <div className="builder-inspector-stack" data-testid="builder-boundary-editor">
      <label className="builder-field builder-field--stack">
        <span className="builder-field-label">
          over link rule — fixed links only (access rules schedule)
        </span>
        <select
          aria-label="Boundary link rule"
          value={boundary.over_rule_id}
          onChange={(e) => onUpdate({ over_rule_id: e.target.value })}
        >
          {fixedRules.map((rule) => (
            <option key={rule.rule_id} value={rule.rule_id}>
              {rule.label || rule.rule_id}
            </option>
          ))}
          {!fixedRules.some((rule) => rule.rule_id === boundary.over_rule_id) && (
            <option value={boundary.over_rule_id}>
              {boundary.over_rule_id || "(pick a fixed rule)"}
            </option>
          )}
        </select>
      </label>
      <label className="builder-field">
        <span className="builder-field-label">adapter</span>
        <span className="builder-field-input">
          <select
            aria-label="Boundary adapter"
            value={boundary.adapter}
            onChange={(e) =>
              onUpdate({ adapter: e.target.value as DraftBoundary["adapter"] })
            }
          >
            <option value="static_ip">static_ip</option>
            <option value="bgp">bgp</option>
            <option value="dtn_bundle">dtn_bundle</option>
          </select>
        </span>
      </label>
      {(["from_domain_id", "to_domain_id"] as const).map((side) => (
        <label className="builder-field" key={side}>
          <span className="builder-field-label">
            {side === "from_domain_id" ? "between" : "and"}
          </span>
          <span className="builder-field-input">
            <select
              aria-label={side === "from_domain_id" ? "From domain" : "To domain"}
              value={boundary[side]}
              onChange={(e) => onUpdate({ [side]: e.target.value })}
            >
              {workspace.routing_domains.map((domain) => (
                <option key={domain.domain_id} value={domain.domain_id}>
                  {domain.label}
                </option>
              ))}
            </select>
          </span>
        </label>
      ))}
      <label className="builder-field">
        <span className="builder-field-label">export loopbacks</span>
        <span className="builder-field-input">
          <input
            type="checkbox"
            checked={boundary.export_node_loopbacks}
            onChange={(e) => onUpdate({ export_node_loopbacks: e.target.checked })}
          />
        </span>
      </label>
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
