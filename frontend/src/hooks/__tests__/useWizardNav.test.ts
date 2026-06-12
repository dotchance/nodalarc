// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
/** The wizard launch gate: constellation + ground stations, nothing else.
 *
 * The satellite-type override is retired — the constellation primitive owns
 * its node model. A gate that demands a selection no backend can supply
 * deadlocks the wizard; these tests pin the exact Group A requirement.
 */
import { describe, it, expect } from "vitest";
import { canPreview } from "../useWizardNav";
import type { WizardState } from "../../catalog/wizardTypes";

function baseState(overrides: Partial<WizardState>): WizardState {
  return {
    phase: "selections",
    activeCard: null,
    constellation: null,
    groundStationSet: null,
    coveragePreview: null,
    orbitPropagator: "j2_mean_elements",
    protocol: null,
    extensions: [],
    areaStrategy: "flat",
    routingTimers: { helloInterval: 10, deadInterval: 40, spfDelay: 0 },
    ...overrides,
  } as WizardState;
}

const constellation = {
  name: "earth-leo-walker-delta-176",
  description: "test",
  satellite_count: 176,
  constellation: "nodalarc:constellations/earth/leo/earth-leo-walker-delta-176.yaml",
  ground_stations: "nodalarc:site-sets/earth/leo/earth-leo-starlink-gateway-sites.yaml",
  mode: "constellation",
};

const groundStationSet = {
  name: "earth-leo-starlink-gateway-sites",
  description: "test",
  stations: ["earth-us-co-denver"],
  file: "nodalarc:site-sets/earth/leo/earth-leo-starlink-gateway-sites.yaml",
};

describe("canPreview", () => {
  it("is satisfied by constellation plus ground stations alone", () => {
    expect(canPreview(baseState({ constellation, groundStationSet }))).toBe(true);
  });

  it("requires a constellation", () => {
    expect(canPreview(baseState({ groundStationSet }))).toBe(false);
  });

  it("requires a ground station set", () => {
    expect(canPreview(baseState({ constellation }))).toBe(false);
  });
});
