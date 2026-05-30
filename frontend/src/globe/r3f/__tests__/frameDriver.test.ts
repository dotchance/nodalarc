// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
/** The reference-frame rotation law: relative rotation is always +gmst; the mode decides
 *  which group carries it. (The legacy GlobeView render loop, extracted pure.) */

import { describe, it, expect } from "vitest";
import { frameRotations } from "../FrameDriver";

describe("frameRotations", () => {
  const gmst = 1.2345;

  it("earth-inertial: the Earth frame carries +gmst, stars are fixed", () => {
    expect(frameRotations(gmst, "earth-inertial")).toEqual({ earthRotY: gmst, starRotY: 0 });
  });

  it("earth-fixed: the Earth frame is fixed, the sky counter-rotates by -gmst", () => {
    expect(frameRotations(gmst, "earth-fixed")).toEqual({ earthRotY: 0, starRotY: -gmst });
  });

  it("the relative rotation between the frames is +gmst in both modes", () => {
    for (const mode of ["earth-inertial", "earth-fixed"] as const) {
      const { earthRotY, starRotY } = frameRotations(gmst, mode);
      expect(earthRotY - starRotY).toBeCloseTo(gmst);
    }
  });
});
