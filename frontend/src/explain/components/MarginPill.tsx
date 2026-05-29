// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
/** L1: a gate's numeric margin (actual / threshold / delta), unit-aware. */

import { formatMargin } from "../derive";
import type { LadderGate } from "../types";

export function MarginPill({ gate }: { gate: LadderGate }) {
  const text = formatMargin(gate);
  if (!text) return null;
  return <span className="margin-pill">{text}</span>;
}
