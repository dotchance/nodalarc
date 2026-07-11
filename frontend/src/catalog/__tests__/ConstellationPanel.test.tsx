// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { ConstellationPanel } from "../ConstellationPanel";
import type { ConstellationPreset, OrbitModel, WizardConstellationGeometry } from "../wizardTypes";

const CUSTOM_CAPABILITY: ConstellationPreset["capability"] = {
  source_kind: "custom_geometry",
  runtime_supported_propagators: ["j2_mean_elements", "two_body"],
  default_propagator: "j2_mean_elements",
  unavailable_reason: null,
};
const CUSTOM_SEED: WizardConstellationGeometry = {
  display_name: "Custom 4x11 shell",
  description: "backend seed",
  altitude_km: 550,
  inclination_deg: 53,
  pattern: "walker_delta",
  planes: 4,
  slots_per_plane: 11,
  raan_spacing_deg: 90,
  phase_offset_deg: 8.182,
};
const ORBIT_MODELS: OrbitModel[] = [
  { id: "j2_mean_elements", label: "J2 Mean Elements", description: "J2" },
  { id: "two_body", label: "Keplerian Two-Body", description: "two body" },
  { id: "sgp4_tle", label: "SGP4 / TLE", description: "TLE" },
];
const AUTHORING_FACTS = {
  customGeometrySeed: CUSTOM_SEED,
  customGeometryDefaultNode: "nodalarc:nodes/space/starlink-v2-mesh.yaml",
  orbitModels: ORBIT_MODELS,
};

function preset(
  name: string,
  supported: ConstellationPreset["capability"]["runtime_supported_propagators"],
  defaultPropagator: ConstellationPreset["capability"]["default_propagator"],
  unavailableReason: string | null = null,
): ConstellationPreset {
  return {
    name,
    description: `${name} description`,
    satellite_count: 12,
    constellation: `nodalarc:constellations/earth/leo/${name}.yaml`,
    ground_stations: "nodalarc:site-sets/earth/leo/earth-leo-starlink-pop-sites.yaml",
    default_node: "starlink-v2-mesh",
    capability: {
      source_kind: "constellation",
      runtime_supported_propagators: supported,
      default_propagator: defaultPropagator,
      unavailable_reason: unavailableReason,
    },
  };
}

describe("ConstellationPanel", () => {
  afterEach(() => cleanup());

  it("shows J2 and Keplerian support on parametric constellation tiles", () => {
    render(
      <ConstellationPanel
        presets={[preset("parametric-shell", ["j2_mean_elements", "two_body"], "j2_mean_elements")]}
        customGeometryCapability={CUSTOM_CAPABILITY}
        {...AUTHORING_FACTS}
        selected={null}
        onSelect={vi.fn()}
      />,
    );

    const card = screen.getByRole("button", { name: /parametric-shell/ });
    expect(within(card).getByText("J2 Mean Elements")).toBeTruthy();
    expect(within(card).getByText("Keplerian Two-Body")).toBeTruthy();
    expect(within(card).queryByText("SGP4 / TLE")).toBeNull();
  });

  it("allows TLE-backed constellation tiles with SGP4 support", () => {
    const onSelect = vi.fn();
    render(
      <ConstellationPanel
        presets={[preset("tle-shell", ["sgp4_tle"], "sgp4_tle")]}
        customGeometryCapability={CUSTOM_CAPABILITY}
        {...AUTHORING_FACTS}
        selected={null}
        onSelect={onSelect}
      />,
    );

    const card = screen.getByRole("button", { name: /tle-shell/ }) as HTMLButtonElement;
    expect(card.disabled).toBe(false);
    expect(within(card).getByText("SGP4 / TLE")).toBeTruthy();
    expect(within(card).queryByText("J2 Mean Elements")).toBeNull();
    expect(within(card).queryByText("Keplerian Two-Body")).toBeNull();
    fireEvent.click(card);
    expect(onSelect).toHaveBeenCalledWith(expect.objectContaining({ name: "tle-shell" }));
  });

  it("disables a backend-declared unavailable source with its exact reason", () => {
    const onSelect = vi.fn();
    const reason = "orbit propagator 'crtbp' is not supported by the current runtime";
    render(
      <ConstellationPanel
        presets={[preset("nrho", [], null, reason)]}
        customGeometryCapability={CUSTOM_CAPABILITY}
        {...AUTHORING_FACTS}
        selected={null}
        onSelect={onSelect}
      />,
    );

    const card = screen.getByRole("button", { name: /nrho/ }) as HTMLButtonElement;
    expect(card.disabled).toBe(true);
    expect(within(card).getByText(reason)).toBeTruthy();
    expect(within(card).getByText("No supported orbit model")).toBeTruthy();
    fireEvent.click(card);
    expect(onSelect).not.toHaveBeenCalled();
  });

  it("emits typed custom geometry without constructing catalog YAML", () => {
    const onSelect = vi.fn();
    render(
      <ConstellationPanel
        presets={[preset("parametric-shell", ["j2_mean_elements", "two_body"], "j2_mean_elements")]}
        customGeometryCapability={CUSTOM_CAPABILITY}
        {...AUTHORING_FACTS}
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
    expect(selected.constellation).toBeNull();
    expect(selected.capability).toEqual(CUSTOM_CAPABILITY);
    expect(selected.custom_geometry).toEqual({
      display_name: "custom-10x11-550km",
      description: "10 planes × 11 sats, 550 km, 53° walker-delta",
      altitude_km: 550,
      inclination_deg: 53,
      pattern: "walker_delta",
      planes: 10,
      slots_per_plane: 11,
      raan_spacing_deg: 36,
      phase_offset_deg: 3.273,
    });
  });

  it("derives Walker-star planes across a 180-degree RAAN span", () => {
    const onSelect = vi.fn();
    render(
      <ConstellationPanel
        presets={[preset("parametric-shell", ["j2_mean_elements", "two_body"], "j2_mean_elements")]}
        customGeometryCapability={CUSTOM_CAPABILITY}
        {...AUTHORING_FACTS}
        selected={null}
        onSelect={onSelect}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: /^Custom/ }));
    fireEvent.change(screen.getByRole("combobox", { name: "Pattern" }), {
      target: { value: "walker_star" },
    });
    fireEvent.change(screen.getByRole("spinbutton", { name: "Orbital Planes" }), {
      target: { value: "6" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Use Custom Constellation" }));

    const selected = onSelect.mock.calls[0]![0] as ConstellationPreset;
    expect(selected.custom_geometry).toMatchObject({
      pattern: "walker_star",
      planes: 6,
      raan_spacing_deg: 30,
    });
  });
});
