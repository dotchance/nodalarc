// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
/**
 * Feeds the page's body-sample store from one scene's SessionEphemeris and body-sample
 * windows, and returns the store's version.
 *
 * The store is a module singleton, like the sim clock and the propagation worker, because
 * one Scene is mounted at a time: the live scene unmounts while the Builder's is shown. The
 * scene that mounts owns the store. It starts empty on mount and is emptied on unmount, so a
 * Builder preview never inherits the live session's epoch or windows (which would refuse the
 * preview's own samples as older), and epoch fencing applies within each scene.
 *
 * Samples are accepted in effects, after a render; the returned version changes whenever the
 * accepted samples do, so values a render derives from the store (camera bounds, body picking)
 * recompute without waiting for another prop to change.
 */

import { useEffect, useSyncExternalStore } from "react";
import type { BodySamples, SessionEphemeris } from "../../builder/generated/builderApi";
import {
  acceptBodySampleWindow,
  acceptEphemerisEpoch,
  bodySamplesVersion,
  resetBodySamples,
  subscribeBodySamples,
} from "../../sim/bodySamples";

export function useBodySampleStore(
  ephemeris: SessionEphemeris | null,
  bodySamples: BodySamples | null,
): number {
  // Declared first: on mount the store is emptied before this scene's facts go in.
  useEffect(() => {
    resetBodySamples();
    return () => resetBodySamples();
  }, []);
  // Effects run in declaration order, so an ephemeris and a window of one render are taken
  // in that order.
  useEffect(() => {
    if (ephemeris === null) resetBodySamples();
    else acceptEphemerisEpoch(ephemeris);
  }, [ephemeris]);
  useEffect(() => {
    if (bodySamples !== null) acceptBodySampleWindow(bodySamples);
  }, [bodySamples]);
  return useSyncExternalStore(subscribeBodySamples, bodySamplesVersion);
}
