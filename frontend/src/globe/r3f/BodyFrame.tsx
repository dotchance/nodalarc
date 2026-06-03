// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
/**
 * BodyFrame: the per-body local coordinate frame. Satellites and ground stations
 * are positioned in their body's LOCAL coordinates (small numbers, full precision
 * near the body); the body itself sits in the universe frame at its scaled
 * position. This context lets a body's children resolve "where is local origin,
 * how big is this body" without assuming Earth — the same components render Earth,
 * Luna, or Mars.
 */

import { createContext, useContext } from "react";

export interface BodyFrameValue {
  /** Body id, e.g. "earth", "luna", "mars". */
  id: string;
  radiusKm: number;
  /** Body radius in render units. */
  radiusRender: number;
}

const BodyFrameContext = createContext<BodyFrameValue | null>(null);

export const BodyFrameProvider = BodyFrameContext.Provider;

/** Read the enclosing body's frame. Throws outside a <Body> — a scene-graph bug. */
export function useBodyFrame(): BodyFrameValue {
  const value = useContext(BodyFrameContext);
  if (!value) {
    throw new Error("useBodyFrame must be used within a <Body>");
  }
  return value;
}
