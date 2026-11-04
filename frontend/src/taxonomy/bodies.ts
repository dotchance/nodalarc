// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
/**
 * Per-body identity on the page: the label and tint a body's nodes carry. Keyed by every body
 * the backend supports (the generated SupportedSurfaceBody), so a supported body without an
 * entry fails the type check. The orbit regime of a node that orbits a body other than Earth is
 * the body itself; Earth orbits keep their altitude classes.
 */

import type { SupportedSurfaceBody } from "../builder/generated/builderApi";
import { tokens } from "../styles/tokens";

export interface BodyIdentity {
  /** The label of the body's orbit regime and surface chips. */
  label: string;
  /** Tint of the body's surface chip and, for a body other than Earth, of its orbit regime. */
  tintCss: string;
}

export const BODY_IDENTITY: Record<SupportedSurfaceBody, BodyIdentity> = {
  earth: { label: "Earth", tintCss: tokens.mediumTerrestrial },
  luna: { label: "Lunar", tintCss: tokens.regimeLuna },
};

/** A body other than Earth, whose orbits form one regime each. */
export type BodyRegime = Exclude<SupportedSurfaceBody, "earth">;

export const BODY_REGIMES: readonly BodyRegime[] = (
  Object.keys(BODY_IDENTITY) as SupportedSurfaceBody[]
).filter((body): body is BodyRegime => body !== "earth");

export function isBodyRegime(body: string): body is BodyRegime {
  return (BODY_REGIMES as readonly string[]).includes(body);
}
