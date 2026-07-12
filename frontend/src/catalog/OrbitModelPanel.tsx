// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
import type { ConstellationPreset, OrbitModel, OrbitPropagator } from "./wizardTypes";
import {
  defaultOrbitPropagatorForConstellation,
  orbitModelDisabledReason,
  supportedOrbitModelsForConstellation,
} from "./orbitModels";

interface OrbitModelPanelProps {
  constellation: ConstellationPreset | null;
  orbitModels: readonly OrbitModel[];
  selected: OrbitPropagator | null;
  onSelect: (model: OrbitPropagator) => void;
}

export function OrbitModelPanel({
  constellation,
  orbitModels,
  selected,
  onSelect,
}: OrbitModelPanelProps) {
  const supported = new Set(
    supportedOrbitModelsForConstellation(constellation, orbitModels).map((option) => option.id),
  );
  const defaultModel = defaultOrbitPropagatorForConstellation(constellation);

  return (
    <div className="wizard-orbit-models">
      {orbitModels.map((option) => {
        const disabledReason = orbitModelDisabledReason(option, constellation);
        const disabled = !supported.has(option.id);
        return (
          <button
            key={option.id}
            aria-label={option.label}
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
                {disabledReason}
              </div>
            )}
          </button>
        );
      })}
    </div>
  );
}
