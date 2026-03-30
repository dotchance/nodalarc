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
  WizardPhase,
  ActiveCard,
  WizardState,
  LegacyWizardState,
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
export function canReview(state: LegacyWizardState): boolean {
  return state.protocol !== null;
}

/** Map legacy linear step to the step after it. */
const NEXT_STEP: Record<WizardStep, WizardStep> = {
  "satellite-type": "ground-stations",
  "ground-stations": "constellation",
  constellation: "protocol",
  protocol: "extensions",
  extensions: "review",
  review: "review",
};

/** Map legacy linear step to the step before it. */
const PREV_STEP: Record<WizardStep, WizardStep> = {
  "satellite-type": "satellite-type",
  "ground-stations": "satellite-type",
  constellation: "ground-stations",
  protocol: "constellation",
  extensions: "protocol",
  review: "extensions",
};

/** Hook providing navigation actions for the legacy linear wizard.
 *  Will be replaced with phase-based navigation when the step group
 *  model is wired into SessionWizard. */
export function useWizardNav(
  setState: React.Dispatch<React.SetStateAction<LegacyWizardState>>,
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
      return { ...s, step: PREV_STEP[s.step] };
    });
  }, [setState]);

  const goToReview = useCallback(() => {
    setState((s) => ({ ...s, step: "review" as WizardStep }));
  }, [setState]);

  return { goToStep, goBack, goToReview };
}
