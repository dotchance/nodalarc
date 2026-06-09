// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { OrbitModelPanel } from "../OrbitModelPanel";
import type { ConstellationPreset } from "../wizardTypes";

function preset(mode: string): ConstellationPreset {
  return {
    name: `${mode}-constellation`,
    description: "test",
    satellite_count: 1,
    constellation: "nodalarc:constellations/earth/leo/earth-leo-ring-36.yaml",
    ground_stations: "nodalarc:site-sets/earth/leo/earth-leo-starlink-pop-sites.yaml",
    mode,
  };
}

describe("OrbitModelPanel", () => {
  afterEach(() => cleanup());

  it("shows J2 as the visible default and disables SGP4 for parametric constellations", () => {
    const onSelect = vi.fn();
    render(
      <OrbitModelPanel
        constellation={preset("parametric")}
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

  it("shows SGP4 as coming soon for TLE-backed constellations", () => {
    const onSelect = vi.fn();
    render(
      <OrbitModelPanel
        constellation={preset("tle")}
        selected="j2_mean_elements"
        onSelect={onSelect}
      />,
    );

    const j2 = screen.getByRole("button", { name: /J2 Mean Elements/ }) as HTMLButtonElement;
    const kepler = screen.getByRole("button", { name: /Keplerian Circular/ }) as HTMLButtonElement;
    const sgp4 = screen.getByRole("button", { name: /SGP4 \/ TLE/ }) as HTMLButtonElement;
    expect(j2.disabled).toBe(true);
    expect(kepler.disabled).toBe(true);
    expect(sgp4.disabled).toBe(true);
    expect(screen.getByText("Coming soon")).toBeTruthy();

    fireEvent.click(j2);
    fireEvent.click(sgp4);
    expect(onSelect).not.toHaveBeenCalled();
  });
});
