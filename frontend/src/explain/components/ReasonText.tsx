// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
/** L1: human label for a reason code, from the single registry. Never invents text. */

import { REASON_REGISTRY } from "../reasons";

export function ReasonText({ code }: { code: string | null | undefined }) {
  if (!code) return null;
  const rec = REASON_REGISTRY[code];
  // Unknown code: show the raw code rather than fabricate a label.
  return <span className="reason-text">{rec ? rec.label : code}</span>;
}
