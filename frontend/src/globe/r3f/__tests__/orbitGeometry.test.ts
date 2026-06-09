import { describe, expect, it } from "vitest";
import { supportsStaticOrbitRing } from "../orbitGeometry";

describe("supportsStaticOrbitRing", () => {
  it("allows circular and near-circular orbit overlays", () => {
    expect(supportsStaticOrbitRing(0)).toBe(true);
    expect(supportsStaticOrbitRing(1e-7)).toBe(true);
  });

  it("rejects eccentric orbit overlays instead of drawing a false circular path", () => {
    expect(supportsStaticOrbitRing(0.01)).toBe(false);
    expect(supportsStaticOrbitRing(0.737)).toBe(false);
  });

  it("rejects nodes without Keplerian eccentricity facts", () => {
    expect(supportsStaticOrbitRing(null)).toBe(false);
    expect(supportsStaticOrbitRing(undefined)).toBe(false);
  });
});
