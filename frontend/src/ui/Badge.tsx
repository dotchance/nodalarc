// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
/**
 * Status language primitives.
 *
 * `StatusDot` — small health/severity light. Status slots only (ok/warn/fail/
 * neutral); taxonomy colors never flow through it (color-slot law).
 * `Badge` — compact label chip; `tone` follows the status slots plus `accent`
 * for ownership/identity chips.
 */

import type { ReactNode } from "react";

export type StatusTone = "ok" | "warn" | "fail" | "neutral";

export function StatusDot({ tone, title }: { tone: StatusTone; title?: string }) {
  return <span className={`ui-dot ui-dot--${tone}`} title={title} aria-hidden={title ? undefined : true} />;
}

interface BadgeProps {
  tone?: StatusTone | "accent";
  children: ReactNode;
  title?: string;
}

export function Badge({ tone = "neutral", children, title }: BadgeProps) {
  return (
    <span className={`ui-badge ui-badge--${tone}`} title={title}>
      {children}
    </span>
  );
}

/** Taxonomy identity chip (regime, body, medium): a tinted dot + label.
 *  Taxonomy slot only — never reuse for health/severity (color-slot law). */
export function TaxonomyChip({ color, children, title }: { color: string; children: ReactNode; title?: string }) {
  return (
    <span className="ui-taxchip" title={title}>
      <span className="ui-taxchip-dot" style={{ background: color }} aria-hidden="true" />
      {children}
    </span>
  );
}
