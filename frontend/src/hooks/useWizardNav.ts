// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
/** Wizard navigation logic — step group model.
 *
 * Group A: constellation, satellite type, ground stations (any order).
 * Preview gates on all three being selected.
 * Group B: protocol, extensions (after preview).
 *
 * The canPreview function is a pure export — testable without
 * mounting any component.
 */

import { useCallback } from "react";
import type {
  WizardState,
  WizardRuntimeState,
  WizardStep,
} from "../catalog/wizardTypes";

/** Pure function: can the user run coverage preview?
 *  True when all three Group A selections are made. */
export function canPreview(state: WizardState): boolean {
  return (
    state.constellation !== null &&
    state.satelliteType !== null &&
    state.groundStationSet !== null
  );
}

/** Pure function: can the user proceed to review?
 *  True when protocol is selected (extensions are optional). */
export function canReview(state: WizardRuntimeState): boolean {
  return state.protocol !== null;
}

/** Map step to the step before it for back navigation. */
const PREV_STEP: Partial<Record<WizardStep, WizardStep>> = {
  protocol: "selections",
  extensions: "protocol",
  review: "extensions",
};

/** Hook providing navigation actions for the wizard. */
export function useWizardNav(
  setState: React.Dispatch<React.SetStateAction<WizardRuntimeState>>,
) {
  const goToStep = useCallback(
    (step: WizardStep) => {
      setState((s) => ({ ...s, step }));
    },
    [setState],
  );

  const goBack = useCallback(() => {
    setState((s) => {
      if (s.step === "review" && s.protocol === "nodalpath") {
        return { ...s, step: "protocol" as WizardStep };
      }
      const prev = PREV_STEP[s.step];
      if (prev) return { ...s, step: prev };
      return s;
    });
  }, [setState]);

  const goToReview = useCallback(() => {
    setState((s) => ({ ...s, step: "review" as WizardStep }));
  }, [setState]);

  return { goToStep, goBack, goToReview };
}
