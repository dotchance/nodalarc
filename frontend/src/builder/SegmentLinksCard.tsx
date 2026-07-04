// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
/** The connect gesture, on the segment: a segment's
 *  editor lists the rules that touch it and offers "+ link to…" — pick the
 *  other end and the rule is created with physics derived from both
 *  faceplates (IG-7), then opens for confirmation (IG-1/IG-2). Connecting
 *  to itself authors the intra-segment fabric (mesh).
 */

import { SelectField } from "./editorKit";
import { placedSegments, type Workspace } from "./workspace";

interface SegmentLinksCardProps {
  workspace: Workspace;
  segmentId: string;
  onOpenRule: (ruleId: string) => void;
  onConnect: (targetSegmentId: string) => void;
}

export function SegmentLinksCard({
  workspace,
  segmentId,
  onOpenRule,
  onConnect,
}: SegmentLinksCardProps) {
  const placed = placedSegments(workspace);
  const label = (id: string) => placed.find((s) => s.segment_id === id)?.label ?? id;
  const touching = workspace.links.filter(
    (rule) => rule.a.segment_id === segmentId || rule.b.segment_id === segmentId,
  );
  return (
    <div className="builder-card builder-card--open">
      <div className="builder-card-head">
        <span className="builder-card-title">Links</span>
        <span className="builder-card-summary">
          {touching.length === 1 ? "1 rule" : `${touching.length} rules`}
        </span>
      </div>
      <div className="builder-card-body">
        {touching.map((rule) => {
          const otherId =
            rule.a.segment_id === segmentId ? rule.b.segment_id : rule.a.segment_id;
          const mesh = rule.a.segment_id === rule.b.segment_id;
          return (
            <button
              key={rule.rule_id}
              className="builder-outline-row"
              title={`Edit ${rule.label || rule.rule_id}`}
              onClick={() => onOpenRule(rule.rule_id)}
            >
              <span>{rule.label || rule.rule_id}</span>
              <span className="builder-outline-count">
                {rule.a.role} · {mesh ? "mesh" : label(otherId)}
              </span>
            </button>
          );
        })}
        <SelectField
          stack
          label="+ link to…"
          ariaLabel="Connect to segment"
          value=""
          onChange={(target) => target && onConnect(target)}
          options={[
            { value: "", label: "pick the other end…" },
            ...placed.map((segment) => ({
              value: segment.segment_id,
              label:
                segment.segment_id === segmentId
                  ? `${segment.label} (this segment — mesh)`
                  : `${segment.label} (${segment.kind})`,
            })),
          ]}
        />
      </div>
    </div>
  );
}
