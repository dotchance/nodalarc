// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
/** The connect gesture, on the segment: a segment's
 *  editor lists the rules that touch it and offers "+ link to…" — pick the
 *  other end and the rule is created with physics derived from both
 *  faceplates, then opens for confirmation. Connecting
 *  to itself authors the intra-segment fabric (mesh).
 */

import { EditorCard, SelectField } from "./editorKit";
import type { ListedLinkRule } from "./sessionListing";
import { placedSegments, type Workspace } from "./workspace";

interface SegmentLinksCardProps {
  workspace: Workspace;
  /** Every link rule the session declares, as the backend lists them with
   *  the pending edits laid over them. */
  rules: ReadonlyArray<ListedLinkRule>;
  segmentId: string;
  onOpenRule: (ruleId: string) => void;
  onConnect: (targetSegmentId: string) => void;
}

export function SegmentLinksCard({
  workspace,
  rules,
  segmentId,
  onOpenRule,
  onConnect,
}: SegmentLinksCardProps) {
  const placed = placedSegments(workspace);
  const label = (id: string) => placed.find((s) => s.segment_id === id)?.label ?? id;
  const touching = rules.filter((rule) => rule.segmentIds.includes(segmentId));
  /** What the row states about the rule: its role and other end from its card
   *  value, or the other segments the accepted rule names when no card states
   *  it. A pending edit with no card value states neither. */
  const detail = (rule: ListedLinkRule): string => {
    const parts: string[] = [];
    const card = rule.card;
    if (card !== null) {
      const a = card.a?.segment_id;
      const b = card.b?.segment_id;
      const other = a === segmentId ? b : a;
      if (card.a?.role) parts.push(card.a.role);
      if (a !== undefined && a === b) parts.push("mesh");
      else if (other) parts.push(label(other));
    } else if (rule.pending === null) {
      const others = rule.segmentIds.filter((id) => id !== segmentId).map(label);
      if (others.length > 0) parts.push(others.join(", "));
    }
    if (rule.pending !== null) parts.push("pending edit");
    return parts.join(" · ");
  };
  return (
    <EditorCard
      title="Links"
      open
      summary={touching.length === 1 ? "1 rule" : `${touching.length} rules`}
    >
        {touching.map((rule) => (
          <button
            key={rule.id}
            className="builder-outline-row"
            title={`Edit ${rule.id}`}
            onClick={() => onOpenRule(rule.id)}
          >
            <span>{rule.id}</span>
            <span className="builder-outline-count">{detail(rule)}</span>
          </button>
        ))}
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
    </EditorCard>
  );
}
