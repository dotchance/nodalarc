// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
import { describe, it, expect, vi, afterEach } from "vitest";
import { render, screen, fireEvent, cleanup } from "@testing-library/react";
import { LogPanel } from "../LogPanel";
import type { DebugSourceControl, OpsEvent } from "../../generated/vsApi";

afterEach(cleanup);

const OPS: OpsEvent[] = [
  { timestamp: "2026-06-12T06:11:04Z", source: "scheduler", level: "warning", code: "BOUNDARY", message: "export waiting for kernel proof", session_id: "s1", hostname: "node02", tenant_id: "tenant", details: null },
  { timestamp: "2026-06-12T06:11:06Z", source: "node_agent", level: "error", code: "ACTUATION", message: "kernel state mismatch after LinkUp", session_id: "s1", hostname: "node02", tenant_id: "tenant", details: null },
];


function renderPanel(debugControl: DebugSourceControl[] = [], debugSubscriptionOpen = true) {
  const onClose = vi.fn();
  render(
    <LogPanel
      events={OPS}
      debugEvents={[]}
      debugControl={debugControl}
      debugSubscriptionOpen={debugSubscriptionOpen}
      sendMessage={vi.fn()}
      onClose={onClose}
    />,
  );
  return { onClose };
}

describe("LogPanel", () => {
  it("level chip toggles filter rows out", () => {
    renderPanel();
    fireEvent.click(screen.getByRole("button", { name: "error" }));
    expect(screen.queryByText(/kernel state mismatch/)).toBeNull();
    expect(screen.getByText(/export waiting for kernel proof/)).toBeTruthy();
  });

  it("regex search filters and flags invalid patterns", () => {
    renderPanel();
    const input = screen.getByPlaceholderText(/Search/);
    fireEvent.change(input, { target: { value: "ACTUATION" } });
    expect(screen.queryByText(/export waiting/)).toBeNull();
    fireEvent.change(input, { target: { value: "[" } });
    expect(screen.getByText("invalid regex")).toBeTruthy();
  });

  it("shows each service's debug state from what VS-API last heard back", () => {
    renderPanel([
      { source: "scheduler", request_seq: 4, request: "enable", outcome: "acknowledged", level: "debug" },
      { source: "ome", request_seq: 5, request: "enable", outcome: "no_responders" },
      { source: "vs_api", request_seq: 6, request: "disable", outcome: "timed_out" },
    ]);
    fireEvent.click(screen.getByRole("button", { name: /debug \(1\)/ }));
    expect(screen.getByRole("button", { name: /scheduler.*one instance at debug/ })).toBeTruthy();
    expect(screen.getByRole("button", { name: /ome.*enable failed: not running/ })).toBeTruthy();
    expect(screen.getByRole("button", { name: /vs api.*disable failed: no reply/ })).toBeTruthy();
    expect(screen.queryByText("VS-API is not reading the debug stream")).toBeNull();
  });

  it("says when VS-API is not reading the debug stream it asked for", () => {
    renderPanel(
      [{ source: "scheduler", request_seq: 4, request: "enable", outcome: "acknowledged", level: "debug" }],
      false,
    );
    fireEvent.click(screen.getByRole("button", { name: /debug \(1\)/ }));
    expect(screen.getByText("VS-API is not reading the debug stream")).toBeTruthy();
  });
});
