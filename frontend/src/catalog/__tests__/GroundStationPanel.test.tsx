// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { GroundStationPanel } from "../GroundStationPanel";

afterEach(cleanup);

describe("GroundStationPanel custom stations", () => {
  it("says VS-API returned no stations instead of waiting for them", () => {
    render(
      <GroundStationPanel
        groundStationSets={[
          { name: "Starlink PoPs", description: "Gateways", stations: ["denver"], file: "x" },
        ] as never}
        availableStations={[]}
        selected={null}
        onSelectSet={vi.fn()}
        onSelectCustom={vi.fn()}
      />,
    );
    fireEvent.click(screen.getByText(/Custom/));

    expect(screen.getByText("VS-API returned no ground stations.")).toBeTruthy();
    expect(screen.queryByText(/Loading/)).toBeNull();
  });
});
