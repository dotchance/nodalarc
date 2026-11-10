// Copyright 2024-2026 .chance (dotchance)
// SPDX-License-Identifier: AGPL-3.0-only
/** L1: human label for a reason code, from the single registry. Never invents text. */

import { REASON_REGISTRY, type ReasonCode } from "../reasons";

export function ReasonText({ code }: { code: ReasonCode | null }) {
  if (code === null) return null;
  return <span className="reason-text">{REASON_REGISTRY[code].label}</span>;
}
