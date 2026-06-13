// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { ConstellationPanel } from "../ConstellationPanel";
import type { ConstellationPreset } from "../wizardTypes";

function preset(name: string, mode: string): ConstellationPreset {
  return {
    name,
    description: `${name} description`,
    satellite_count: 12,
    constellation: `nodalarc:constellations/earth/leo/${name}.yaml`,
    ground_stations: "nodalarc:site-sets/earth/leo/earth-leo-starlink-pop-sites.yaml",
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

  it("disables TLE-backed constellation tiles until SGP4 runtime support lands", () => {
    const onSelect = vi.fn();
    render(
      <ConstellationPanel
        presets={[preset("tle-shell", "tle")]}
        selected={null}
        onSelect={onSelect}
      />,
    );

    const card = screen.getByRole("button", { name: /tle-shell/ }) as HTMLButtonElement;
    expect(card.disabled).toBe(true);
    expect(within(card).getByText("Coming Soon")).toBeTruthy();
    expect(within(card).getByText(/require SGP4\/TLE runtime support/)).toBeTruthy();
    expect(within(card).queryByText("J2 Mean Elements")).toBeNull();
    expect(within(card).queryByText("Keplerian Circular")).toBeNull();
    fireEvent.click(card);
    expect(onSelect).not.toHaveBeenCalled();
  });

  it("emits custom constellations in the catalog grammar shape", () => {
    const onSelect = vi.fn();
    render(
      <ConstellationPanel
        presets={[preset("parametric-shell", "parametric")]}
        selected={null}
        onSelect={onSelect}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: /^Custom/ }));
    fireEvent.change(screen.getByRole("spinbutton", { name: "Orbital Planes" }), {
      target: { value: "10" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Use Custom Constellation" }));

    expect(onSelect).toHaveBeenCalledTimes(1);
    const selected = onSelect.mock.calls[0]![0] as ConstellationPreset;
    expect(selected.name).toBe("custom-10x11-550km");
    expect(selected.mode).toBe("constellation");
    const parsed = JSON.parse(selected.constellation) as Record<string, unknown>;
    expect(parsed).toHaveProperty("constellation");
    expect(parsed).not.toHaveProperty("mode");
    expect(parsed).not.toHaveProperty("orbit");
    expect(parsed).not.toHaveProperty("satellite_type");
    const constellation = parsed.constellation as Record<string, unknown>;
    expect(constellation.id).toBe("custom-10x11-550km");
    expect(constellation.node).toBe("nodalarc:nodes/space/starlink-v2-mesh.yaml");
    expect(constellation.slots_per_plane).toBe(11);
    expect(constellation.planes).toEqual({ count: 10, raan_spacing_deg: 36 });
    expect(constellation.phasing).toEqual({
      mode: "walker_delta",
      phase_offset_deg: 3.273,
    });
    expect(constellation.orbit).toHaveProperty("orbit");
  });

  it("can author the 576-node Starlink mesh template", () => {
    const onSelect = vi.fn();
    render(
      <ConstellationPanel
        presets={[preset("parametric-shell", "parametric")]}
        selected={null}
        onSelect={onSelect}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: /^Custom/ }));
    fireEvent.click(screen.getByRole("button", { name: /Starlink \(576-node mesh\)/ }));
    fireEvent.click(screen.getByRole("button", { name: "Use Custom Constellation" }));

    expect(onSelect).toHaveBeenCalledTimes(1);
    const selected = onSelect.mock.calls[0]![0] as ConstellationPreset;
    expect(selected.name).toBe("custom-48x12-550km");
    expect(selected.satellite_count).toBe(576);
    const parsed = JSON.parse(selected.constellation) as Record<string, unknown>;
    const constellation = parsed.constellation as Record<string, unknown>;
    expect(constellation.planes).toEqual({ count: 48, raan_spacing_deg: 7.5 });
    expect(constellation.slots_per_plane).toBe(12);
    expect(constellation.phasing).toEqual({
      mode: "walker_delta",
      phase_offset_deg: 0.625,
    });
  });
});
