// Copyright 2024-2026 .chance (dotchance)
// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms under AGPL-3.0 Section 7 are in the NOTICE file.
/** A scene owns the body-sample store while it is mounted, and values a render derives from
 *  the store recompute when samples are accepted. Real backend windows
 *  (fixtures/bodySamples.json). */

import { cleanup, renderHook } from "@testing-library/react";
import { useMemo } from "react";
import { afterEach, describe, expect, it } from "vitest";
import type { BodySamples, SessionEphemeris } from "../../../builder/generated/builderApi";
import { peekSkyAt, resetBodySamples, windowStart } from "../../../sim/bodySamples";
import { BODY_SAMPLE_FIXTURE } from "../../../sim/__tests__/bodySampleFixture";
import { placedBodiesAt } from "../bodyPose";
import { useBodySampleStore } from "../useBodySampleStore";

const { ephemeris, windows } = BODY_SAMPLE_FIXTURE;
const [first, second] = windows;
const previewTime = ephemeris.epoch_unix;
const BODIES = [
  { id: "earth", radiusKm: 6378.137 },
  { id: "luna", radiusKm: 1738.1 },
];

/** Scene's pattern: the store hook, and a memo over the store read at the snapshot time. */
function useScene(e: SessionEphemeris, w: BodySamples, t: number) {
  const version = useBodySampleStore(e, w);
  return useMemo(
    () => placedBodiesAt(BODIES, peekSkyAt(t), "earth-inertial", 63.78137, "earth"),
    [t, version],
  );
}

afterEach(() => {
  cleanup();
  resetBodySamples();
});

describe("the Builder scene after the live scene", () => {
  it("keeps its own samples after the live session passed its first window", () => {
    // The live scene holds the replacement window, which starts after the preview's epoch.
    expect(windowStart(second)).toBeGreaterThan(previewTime);
    const live = renderHook(() => useScene(ephemeris, second, windowStart(second) + 60));
    live.unmount();

    const preview = renderHook(() => useScene(ephemeris, first, previewTime));
    expect(peekSkyAt(previewTime)).not.toBeNull();
    expect(preview.result.current.map((body) => body.id)).toEqual(["earth", "luna"]);
  });

  it("keeps its own samples after the live session seeked to a later epoch", () => {
    const seekedEphemeris = { ...ephemeris, epoch_id: ephemeris.epoch_id + 1 };
    const seekedWindow = { ...second, epoch_id: ephemeris.epoch_id + 1 };
    const live = renderHook(() =>
      useScene(seekedEphemeris, seekedWindow, windowStart(second) + 60),
    );
    live.unmount();

    const preview = renderHook(() => useScene(ephemeris, first, previewTime));
    expect(peekSkyAt(previewTime)).not.toBeNull();
    expect(preview.result.current).toHaveLength(2);
  });
});

describe("render-time placement", () => {
  it("places the bodies once samples are accepted, with no other prop change", () => {
    const { result } = renderHook(() => useScene(ephemeris, first, previewTime));
    const [earth, luna] = result.current;
    expect(Math.hypot(...(earth?.position ?? [1, 1, 1]))).toBe(0);
    const distanceKm = Math.hypot(...(luna?.position ?? [0, 0, 0])) * 63.78137;
    expect(distanceKm).toBeGreaterThan(350_000);
    expect(distanceKm).toBeLessThan(410_000);
  });
});
