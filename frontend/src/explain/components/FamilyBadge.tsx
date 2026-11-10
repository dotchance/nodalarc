// Copyright 2024-2026 .chance (dotchance)
// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms under AGPL-3.0 Section 7 are in the NOTICE file.
/** L1: the canonical family pill. Tone comes from the single FAMILY_TONE source. */

import { FAMILY_TONE, type Family } from "../families";

export function FamilyBadge({ family }: { family: Family }) {
  const tone = FAMILY_TONE[family];
  return (
    <span className="family-badge" style={{ color: tone.css, borderColor: tone.css }}>
      {tone.label}
    </span>
  );
}
