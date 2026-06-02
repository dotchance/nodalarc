// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
/** Shared label toggles and Earth-limb occlusion math for the R3F labels layer. */

let labelsEnabled = true;

export function setLabelsEnabled(enabled: boolean): void {
  labelsEnabled = enabled;
}

export function getLabelsEnabled(): boolean {
  return labelsEnabled;
}

const OCC_RADIUS_FACTOR = 0.985;

export function isOccludedByEarth(
  satWorldX: number,
  satWorldY: number,
  satWorldZ: number,
  camX: number,
  camY: number,
  camZ: number,
  earthRadius: number,
): boolean {
  const occR = earthRadius * OCC_RADIUS_FACTOR;
  const dx = satWorldX - camX;
  const dy = satWorldY - camY;
  const dz = satWorldZ - camZ;
  const len = Math.sqrt(dx * dx + dy * dy + dz * dz);
  if (len < 0.001) return false;
  const dirX = dx / len;
  const dirY = dy / len;
  const dirZ = dz / len;

  const bHalf = camX * dirX + camY * dirY + camZ * dirZ;
  const c = camX * camX + camY * camY + camZ * camZ - occR * occR;
  const discrim = bHalf * bHalf - c;
  if (discrim <= 0) return false;

  const tNear = -bHalf - Math.sqrt(discrim);
  return tNear > 0 && tNear < len;
}
