// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
import type {
  ConstellationPreset,
  OrbitModel,
  OrbitPropagator,
} from "./wizardTypes";

export function constellationUnsupportedReason(
  preset: ConstellationPreset | null,
): string | null {
  return preset?.capability.unavailable_reason ?? null;
}

export function orbitModelDisabledReason(
  option: OrbitModel,
  preset: ConstellationPreset | null,
): string | null {
  if (!preset) {
    return "Select a constellation before choosing its orbit model.";
  }
  if (preset.capability.unavailable_reason) {
    return preset.capability.unavailable_reason;
  }
  return preset.capability.runtime_supported_propagators.includes(option.id)
    ? null
    : `${option.label} is not runtime-supported for this constellation source.`;
}

export function supportedOrbitModelsForConstellation(
  preset: ConstellationPreset | null,
  orbitModels: readonly OrbitModel[],
): OrbitModel[] {
  return orbitModels.filter(
    (option) => orbitModelDisabledReason(option, preset) === null,
  );
}

export function defaultOrbitPropagatorForConstellation(
  preset: ConstellationPreset | null,
): OrbitPropagator | null {
  return preset?.capability.default_propagator ?? null;
}

export function orbitModelLabel(
  orbitModels: readonly OrbitModel[],
  propagator: OrbitPropagator | null,
): string | null {
  if (propagator === null) return null;
  return orbitModels.find((option) => option.id === propagator)?.label ?? null;
}
