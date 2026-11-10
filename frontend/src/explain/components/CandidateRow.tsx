// Copyright 2024-2026 .chance (dotchance)
// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms under AGPL-3.0 Section 7 are in the NOTICE file.
/** L1: one clickable candidate row (a sat under a GS, or a GS under a sat),
 * family-toned, opening the Per-Pair Inspector. Shared by both detail panels. */

import { FAMILY_TONE, type Family } from "../families";

export function CandidateRow({
  node,
  family,
  label,
  detail,
  onClick,
}: {
  node: string;
  family: Family;
  label: string;
  detail?: string | null;
  onClick: () => void;
}) {
  return (
    <div
      className="candidate-row"
      style={{ borderLeft: `3px solid ${FAMILY_TONE[family].css}` }}
      onClick={onClick}
      title={`Inspect ${node}`}
    >
      <span className="candidate-sat">{node}</span>
      <span className="candidate-status">{label}</span>
      {detail ? <span className="candidate-detail">{detail}</span> : null}
    </div>
  );
}
