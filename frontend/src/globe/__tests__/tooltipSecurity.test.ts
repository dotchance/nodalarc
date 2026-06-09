// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.

import { createElement } from "react";
import { afterEach, describe, expect, it } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { Tooltip, type HoverInfo } from "../r3f/Tooltip";
import type { NodeState } from "../../types";

function node(nodeId: string, nodeType: string): NodeState {
  return {
    node_id: nodeId,
    node_type: nodeType,
    lat_deg: 12.3,
    lon_deg: 45.6,
    alt_km: 0,
    vel_x_km_s: null,
    vel_y_km_s: null,
    vel_z_km_s: null,
    plane: null,
    slot: null,
    routing_area: null,
    neighbor_count: 0,
    isl_count: 0,
    gnd_count: 0,
    prefix: null,
    min_elevation_deg: null,
    beam_falloff_exponent: null,
    reference_body: "earth",
    frame_id: "earth",
  };
}

describe("R3F tooltip rendering", () => {
  const malicious = `normal\n<img src=x onerror=alert(1)>\n<script>alert(1)</script>\n"'&<>`;

  afterEach(() => {
    cleanup();
  });

  it("renders node metadata as text, not HTML", () => {
    const hover: HoverInfo = { node: node(malicious, "ground_station"), x: 50, y: 50 };

    render(createElement(Tooltip, { hover }));

    const tip = screen.getByText((content) => content.includes("<script>alert(1)</script>"));
    expect(tip).toBeInstanceOf(HTMLDivElement);
    expect((tip as HTMLDivElement).style.whiteSpace).toBe("pre-line");
    expect(tip.textContent).toContain("\n");
    expect(tip.textContent).toContain("<img src=x onerror=alert(1)>");
    expect(tip.querySelector("script")).toBeNull();
    expect(tip.querySelector("img")).toBeNull();
  });

  it("renders taxonomy captions as text, not HTML", () => {
    const hover: HoverInfo = {
      node: node("gs-denver", "ground_station"),
      x: 50,
      y: 50,
      caption: malicious,
    };

    render(createElement(Tooltip, { hover }));

    const tip = screen.getByText((content) => content.includes("<script>alert(1)</script>"));
    expect(tip.textContent).toContain("gs-denver: normal");
    expect(tip.querySelector("script")).toBeNull();
    expect(tip.querySelector("img")).toBeNull();
  });
});
