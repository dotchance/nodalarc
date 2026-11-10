// Copyright 2024-2026 .chance (dotchance)
// SPDX-License-Identifier: AGPL-3.0-only
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
