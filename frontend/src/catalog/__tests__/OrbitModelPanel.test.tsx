// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { OrbitModelPanel } from "../OrbitModelPanel";
import type { ConstellationPreset, OrbitModel } from "../wizardTypes";

const ORBIT_MODELS: OrbitModel[] = [
  { id: "j2_mean_elements", label: "J2 Mean Elements", description: "J2" },
  { id: "two_body", label: "Keplerian Two-Body", description: "two body" },
  { id: "sgp4_tle", label: "SGP4 / TLE", description: "TLE" },
];

function preset(
  supported: ConstellationPreset["capability"]["runtime_supported_propagators"],
  defaultPropagator: ConstellationPreset["capability"]["default_propagator"],
): ConstellationPreset {
  return {
    name: "test-constellation",
    description: "test",
    satellite_count: 1,
    constellation: "nodalarc:constellations/earth/leo/earth-leo-ring-36.yaml",
    ground_stations: "nodalarc:site-sets/earth/leo/earth-leo-starlink-pop-sites.yaml",
    default_node: "starlink-v2-mesh",
    capability: {
      source_kind: "constellation",
      runtime_supported_propagators: supported,
      default_propagator: defaultPropagator,
      unavailable_reason: null,
    },
  };
}

describe("OrbitModelPanel", () => {
  afterEach(() => cleanup());

  it("shows J2 as the visible default and disables SGP4 for parametric constellations", () => {
    const onSelect = vi.fn();
    render(
      <OrbitModelPanel
        constellation={preset(["j2_mean_elements", "two_body"], "j2_mean_elements")}
        orbitModels={ORBIT_MODELS}
        selected="j2_mean_elements"
        onSelect={onSelect}
      />,
    );

    expect(screen.getByText("Default")).toBeTruthy();
    const sgp4 = screen.getByRole("button", { name: /SGP4 \/ TLE/ }) as HTMLButtonElement;
    expect(sgp4.disabled).toBe(true);

    fireEvent.click(sgp4);
    expect(onSelect).not.toHaveBeenCalled();
  });

  it("selects SGP4 for TLE-backed constellations", () => {
    const onSelect = vi.fn();
    render(
      <OrbitModelPanel
        constellation={preset(["sgp4_tle"], "sgp4_tle")}
        orbitModels={ORBIT_MODELS}
        selected="j2_mean_elements"
        onSelect={onSelect}
      />,
    );

    const j2 = screen.getByRole("button", { name: /J2 Mean Elements/ }) as HTMLButtonElement;
    const kepler = screen.getByRole("button", { name: /Keplerian Two-Body/ }) as HTMLButtonElement;
    const sgp4 = screen.getByRole("button", { name: /SGP4 \/ TLE/ }) as HTMLButtonElement;
    expect(j2.disabled).toBe(true);
    expect(kepler.disabled).toBe(true);
    expect(sgp4.disabled).toBe(false);
    expect(screen.getByText("Default")).toBeTruthy();

    fireEvent.click(j2);
    fireEvent.click(sgp4);
    expect(onSelect).toHaveBeenCalledOnce();
    expect(onSelect).toHaveBeenCalledWith("sgp4_tle");
  });
});
