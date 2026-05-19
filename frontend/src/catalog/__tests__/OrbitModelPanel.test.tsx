// Copyright 2024-2026 .chance (dotchance)
// Licensed under the NodalArc Source Available License 1.0. See LICENSE file.
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { OrbitModelPanel } from "../OrbitModelPanel";
import type { ConstellationPreset } from "../wizardTypes";

function preset(mode: string): ConstellationPreset {
  return {
    name: `${mode}-constellation`,
    description: "test",
    satellite_count: 1,
    constellation: "configs/constellations/test.yaml",
    ground_stations: "configs/ground-stations/sets/global.yaml",
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
        selected="j2-mean-elements"
        onSelect={onSelect}
      />,
    );

    expect(screen.getByText("Default")).toBeTruthy();
    const sgp4 = screen.getByRole("button", { name: /SGP4 \/ TLE/ }) as HTMLButtonElement;
    expect(sgp4.disabled).toBe(true);

    fireEvent.click(sgp4);
    expect(onSelect).not.toHaveBeenCalled();
  });

  it("enables SGP4 when the selected constellation is TLE-backed", () => {
    const onSelect = vi.fn();
    render(
      <OrbitModelPanel
        constellation={preset("tle")}
        selected="j2-mean-elements"
        onSelect={onSelect}
      />,
    );

    const sgp4 = screen.getByRole("button", { name: /SGP4 \/ TLE/ }) as HTMLButtonElement;
    expect(sgp4.disabled).toBe(false);

    fireEvent.click(sgp4);
    expect(onSelect).toHaveBeenCalledWith("sgp4-tle");
  });
});
