// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
import { describe, it, expect, vi, afterEach } from "vitest";
import { render, screen, fireEvent, cleanup } from "@testing-library/react";
import { LogPanel } from "../LogPanel";
import type { OpsEvent, RecentEvent } from "../../types";

afterEach(cleanup);

const OPS: OpsEvent[] = [
  { timestamp: "2026-06-12T06:11:04Z", source: "scheduler", level: "warning", code: "BOUNDARY", message: "export waiting for kernel proof", session_id: "s1", hostname: "node02" },
  { timestamp: "2026-06-12T06:11:06Z", source: "node_agent", level: "error", code: "ACTUATION", message: "kernel state mismatch after LinkUp", session_id: "s1", hostname: "node02" },
];

const NET: RecentEvent[] = [
  { sim_time: "2026-06-12T06:11:05Z", node_id: "ground-gs-x", event_type: "link_up", summary: "gs-x to sat-1 vis_gained" },
];

function renderPanel() {
  const onClose = vi.fn();
  render(
    <LogPanel
      events={OPS}
      debugEvents={[]}
      debugSources={[]}
      recentEvents={NET}
      sendMessage={vi.fn()}
      onClose={onClose}
    />,
  );
  return { onClose };
}

describe("LogPanel", () => {
  it("renders ops rows in the logs mode", () => {
    renderPanel();
    expect(screen.getByText(/export waiting for kernel proof/)).toBeTruthy();
    expect(screen.getByText(/kernel state mismatch/)).toBeTruthy();
  });

  it("level chip toggles filter rows out", () => {
    renderPanel();
    fireEvent.click(screen.getByRole("button", { name: "error" }));
    expect(screen.queryByText(/kernel state mismatch/)).toBeNull();
    expect(screen.getByText(/export waiting for kernel proof/)).toBeTruthy();
  });

  it("events mode shows the network event feed", () => {
    renderPanel();
    fireEvent.click(screen.getByRole("button", { name: "Events" }));
    expect(screen.getByText(/vis_gained/)).toBeTruthy();
    expect(screen.queryByText(/kernel state mismatch/)).toBeNull();
  });

  it("regex search filters and flags invalid patterns", () => {
    renderPanel();
    const input = screen.getByPlaceholderText(/Search/);
    fireEvent.change(input, { target: { value: "ACTUATION" } });
    expect(screen.queryByText(/export waiting/)).toBeNull();
    fireEvent.change(input, { target: { value: "[" } });
    expect(screen.getByText("invalid regex")).toBeTruthy();
  });

  it("close button closes the window", () => {
    const { onClose } = renderPanel();
    fireEvent.click(screen.getByLabelText("Close"));
    expect(onClose).toHaveBeenCalled();
  });
});
