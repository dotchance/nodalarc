// Copyright 2024-2026 .chance (dotchance)
// Licensed under the NodalArc Source Available License 1.0. See LICENSE file.
import type { ConstellationPreset, OrbitPropagator } from "./wizardTypes";
import {
  ORBIT_MODEL_OPTIONS,
  constellationSupportsSgp4Tle,
} from "./orbitModels";

interface OrbitModelPanelProps {
  constellation: ConstellationPreset | null;
  selected: OrbitPropagator;
  onSelect: (model: OrbitPropagator) => void;
}

export function OrbitModelPanel({
  constellation,
  selected,
  onSelect,
}: OrbitModelPanelProps) {
  const supportsSgp4 = constellationSupportsSgp4Tle(constellation);

  return (
    <div className="wizard-orbit-models">
      {ORBIT_MODEL_OPTIONS.map((option) => {
        const disabled = option.id === "sgp4-tle" && !supportsSgp4;
        return (
          <button
            key={option.id}
            className={`wizard-orbit-model ${selected === option.id ? "wizard-orbit-model--selected" : ""}`}
            onClick={() => !disabled && onSelect(option.id)}
            disabled={disabled}
          >
            <div className="wizard-orbit-model-header">
              <span className="wizard-orbit-model-title">{option.label}</span>
              {option.id === "j2-mean-elements" && (
                <span className="wizard-orbit-model-badge">Default</span>
              )}
            </div>
            <div className="wizard-orbit-model-desc">{option.description}</div>
            {disabled && (
              <div className="wizard-orbit-model-disabled">
                Select a TLE-backed constellation before using SGP4.
              </div>
            )}
          </button>
        );
      })}
    </div>
  );
}
