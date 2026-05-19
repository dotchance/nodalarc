// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, within } from "@testing-library/react";
import { ConstellationPanel } from "../ConstellationPanel";
import type { ConstellationPreset } from "../wizardTypes";

function preset(name: string, mode: string): ConstellationPreset {
  return {
    name,
    description: `${name} description`,
    satellite_count: 12,
    constellation: `configs/constellations/${name}.yaml`,
    ground_stations: "configs/ground-stations/sets/demo.yaml",
    mode,
  };
}

describe("ConstellationPanel", () => {
  afterEach(() => cleanup());

  it("shows J2 and Keplerian support on parametric constellation tiles", () => {
    render(
      <ConstellationPanel
        presets={[preset("parametric-shell", "parametric")]}
        selected={null}
        onSelect={vi.fn()}
      />,
    );

    const card = screen.getByRole("button", { name: /parametric-shell/ });
    expect(within(card).getByText("J2 Mean Elements")).toBeTruthy();
    expect(within(card).getByText("Keplerian Circular")).toBeTruthy();
    expect(within(card).queryByText("SGP4 / TLE")).toBeNull();
  });

  it("shows SGP4/TLE support on TLE-backed constellation tiles", () => {
    render(
      <ConstellationPanel
        presets={[preset("tle-shell", "tle")]}
        selected={null}
        onSelect={vi.fn()}
      />,
    );

    const card = screen.getByRole("button", { name: /tle-shell/ });
    expect(within(card).getByText("SGP4 / TLE")).toBeTruthy();
    expect(within(card).queryByText("J2 Mean Elements")).toBeNull();
    expect(within(card).queryByText("Keplerian Circular")).toBeNull();
  });
});
