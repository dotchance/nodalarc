// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
/** L1: human label for a reason code, from the single registry. Never invents text. */

import { REASON_REGISTRY, type ReasonCode } from "../reasons";

export function ReasonText({ code }: { code: ReasonCode | null }) {
  if (code === null) return null;
  return <span className="reason-text">{REASON_REGISTRY[code].label}</span>;
}
