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
  return isOccludedBySphere(
    satWorldX,
    satWorldY,
    satWorldZ,
    camX,
    camY,
    camZ,
    0,
    0,
    0,
    earthRadius,
  );
}

export function isOccludedBySphere(
  pointWorldX: number,
  pointWorldY: number,
  pointWorldZ: number,
  camX: number,
  camY: number,
  camZ: number,
  centerWorldX: number,
  centerWorldY: number,
  centerWorldZ: number,
  radius: number,
): boolean {
  const occR = radius * OCC_RADIUS_FACTOR;
  const relCamX = camX - centerWorldX;
  const relCamY = camY - centerWorldY;
  const relCamZ = camZ - centerWorldZ;
  const dx = pointWorldX - camX;
  const dy = pointWorldY - camY;
  const dz = pointWorldZ - camZ;
  const len = Math.sqrt(dx * dx + dy * dy + dz * dz);
  if (len < 0.001) return false;
  const dirX = dx / len;
  const dirY = dy / len;
  const dirZ = dz / len;

  const bHalf = relCamX * dirX + relCamY * dirY + relCamZ * dirZ;
  const c = relCamX * relCamX + relCamY * relCamY + relCamZ * relCamZ - occR * occR;
  const discrim = bHalf * bHalf - c;
  if (discrim <= 0) return false;

  const tNear = -bHalf - Math.sqrt(discrim);
  return tNear > 0 && tNear < len;
}
