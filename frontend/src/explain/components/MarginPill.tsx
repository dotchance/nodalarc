// Copyright 2024-2026 .chance (dotchance)
// SPDX-License-Identifier: AGPL-3.0-only
/** L1: a gate's numeric margin (actual / threshold / delta), unit-aware. */

import { formatMargin } from "../derive";
import type { LadderGate } from "../../generated/vsApi";

export function MarginPill({ gate }: { gate: LadderGate }) {
  const text = formatMargin(gate);
  if (!text) return null;
  return <span className="margin-pill">{text}</span>;
}
