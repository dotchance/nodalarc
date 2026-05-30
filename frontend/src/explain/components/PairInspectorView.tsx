// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
/** Hook wrapper: fetches the GS<->sat pair facts and renders the Per-Pair Inspector.
 * Shared by the GS and satellite detail panels so the data-fetch lives in one place. */

import { useDecisionExplanation } from "../useDecisionExplanation";
import { PairInspector } from "./PairInspector";

export function PairInspectorView({
  gsId,
  satId,
  onBack,
}: {
  gsId: string;
  satId: string;
  onBack: () => void;
}) {
  const { facts } = useDecisionExplanation(gsId, satId);
  return <PairInspector gsId={gsId} satId={satId} facts={facts} onBack={onBack} />;
}
