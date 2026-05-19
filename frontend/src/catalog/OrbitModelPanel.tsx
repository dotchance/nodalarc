// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
import type { ConstellationPreset, OrbitPropagator } from "./wizardTypes";
import {
  ORBIT_MODEL_OPTIONS,
  constellationSupportsSgp4Tle,
  defaultOrbitPropagatorForConstellation,
  supportedOrbitModelsForConstellation,
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
  const supported = new Set(
    supportedOrbitModelsForConstellation(constellation).map((option) => option.id),
  );
  const defaultModel = defaultOrbitPropagatorForConstellation(constellation);

  return (
    <div className="wizard-orbit-models">
      {ORBIT_MODEL_OPTIONS.map((option) => {
        const disabled = !supported.has(option.id);
        return (
          <button
            key={option.id}
            className={`wizard-orbit-model ${selected === option.id ? "wizard-orbit-model--selected" : ""}`}
            onClick={() => !disabled && onSelect(option.id)}
            disabled={disabled}
          >
            <div className="wizard-orbit-model-header">
              <span className="wizard-orbit-model-title">{option.label}</span>
              {option.id === defaultModel && (
                <span className="wizard-orbit-model-badge">Default</span>
              )}
            </div>
            <div className="wizard-orbit-model-desc">{option.description}</div>
            {disabled && (
              <div className="wizard-orbit-model-disabled">
                {supportsSgp4
                  ? "TLE-backed constellations require SGP4 propagation."
                  : "Select a TLE-backed constellation before using SGP4."}
              </div>
            )}
          </button>
        );
      })}
    </div>
  );
}
