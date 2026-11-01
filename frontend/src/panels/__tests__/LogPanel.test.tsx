// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
import { describe, it, expect, vi, afterEach } from "vitest";
import { render, screen, fireEvent, cleanup } from "@testing-library/react";
import { LogPanel } from "../LogPanel";
import type { DebugSourceControl, StampedOpsEvent } from "../../generated/vsApi";

afterEach(cleanup);

const PROOF_WAIT: StampedOpsEvent = { timestamp: "2026-06-12T06:11:04Z", source: "scheduler", level: "warning", code: "BOUNDARY", message: "export waiting for kernel proof", session_id: "s1", hostname: "node02", tenant_id: "tenant", details: null, seq: 1 };
const MISMATCH: StampedOpsEvent = { timestamp: "2026-06-12T06:11:06Z", source: "node_agent", level: "error", code: "ACTUATION", message: "kernel state mismatch after LinkUp", session_id: "s1", hostname: "node02", tenant_id: "tenant", details: null, seq: 2 };
const OPS: StampedOpsEvent[] = [PROOF_WAIT, MISMATCH];


function renderPanel(
  debugControl: DebugSourceControl[] = [],
  debugSubscriptionOpen = true,
  events: StampedOpsEvent[] = OPS,
) {
  const onClose = vi.fn();
  render(
    <LogPanel
      events={events}
      debugEvents={[]}
      opsLogToken="t"
      debugControl={debugControl}
      debugSubscriptionOpen={debugSubscriptionOpen}
      sendMessage={vi.fn()}
      onClose={onClose}
    />,
  );
  return { onClose };
}

/** The message cell of every body row, top to bottom. */
function shownMessages(): string[] {
  return screen
    .getAllByRole("row")
    .slice(1)
    .map((row) => row.querySelectorAll('[role="cell"]')[4]?.textContent ?? "");
}

describe("LogPanel", () => {
  it("shows two events alike in every shown field as two rows, and sorts them both ways", () => {
    // Two scheduler instances log the same change at the same second; only
    // VS-API's seq (and the hostname, which the table does not show) tells them apart.
    const alike = {
      timestamp: "2026-06-12T06:11:05Z",
      source: "scheduler",
      level: "info",
      code: "DISPATCHER",
      message: "Link state changed",
      session_id: "s1",
      tenant_id: "tenant",
      details: null,
    } as const;
    const duplicateKeys = vi.spyOn(console, "error").mockImplementation(() => undefined);
    renderPanel([], true, [
      ...OPS,
      { ...alike, hostname: "node01", seq: 3 },
      { ...alike, hostname: "node03", seq: 4 },
    ]);

    // Newest first.
    expect(shownMessages()).toEqual([
      "kernel state mismatch after LinkUp",
      "Link state changed",
      "Link state changed",
      "export waiting for kernel proof",
    ]);
    fireEvent.click(screen.getByRole("button", { name: /timestamp/ }));
    expect(shownMessages()).toEqual([
      "export waiting for kernel proof",
      "Link state changed",
      "Link state changed",
      "kernel state mismatch after LinkUp",
    ]);
    fireEvent.click(screen.getByRole("button", { name: "info" }));
    expect(shownMessages()).toEqual([
      "export waiting for kernel proof",
      "kernel state mismatch after LinkUp",
    ]);
    expect(
      duplicateKeys.mock.calls.some((call) => String(call[0]).includes("same key")),
    ).toBe(false);
    duplicateKeys.mockRestore();
  });

  it("orders timestamps as times, fractions of a second included", () => {
    const at = (timestamp: string, message: string, seq: number): StampedOpsEvent => ({
      timestamp, source: "ome", level: "info", code: "TICK", message,
      session_id: "s1", hostname: "ome-0", tenant_id: "tenant", details: null, seq,
    });
    // As strings "06:11:05.5Z" sorts before "06:11:05Z"; as times it is later.
    renderPanel([], true, [
      at("2026-06-12T06:11:05Z", "on the second", 1),
      at("2026-06-12T06:11:05.5Z", "half a second later", 2),
    ]);
    expect(shownMessages()).toEqual(["half a second later", "on the second"]);
  });

  it("clears by VS-API's arrival seq, whatever the servers' clocks read", () => {
    const props = {
      debugEvents: [],
      debugControl: [],
      debugSubscriptionOpen: true,
      sendMessage: vi.fn(),
      onClose: vi.fn(),
    };
    // A server clock ahead of the browser's stamps a row after the browser's now.
    const ahead = { ...PROOF_WAIT, timestamp: "2099-01-01T00:00:00Z", message: "stamped ahead of the browser", seq: 3 };
    const { rerender } = render(<LogPanel {...props} opsLogToken="tok-a" events={[...OPS, ahead]} />);
    fireEvent.click(screen.getByRole("button", { name: "clear" }));
    expect(shownMessages()).toEqual([]);

    // A server clock behind the browser's stamps a row that arrived after the clear
    // before the browser's now.
    const behind = { ...MISMATCH, timestamp: "2000-01-01T00:00:00Z", message: "stamped behind the browser", seq: 4 };
    rerender(<LogPanel {...props} opsLogToken="tok-a" events={[...OPS, ahead, behind]} />);
    expect(shownMessages()).toEqual(["stamped behind the browser"]);

    // VS-API restarted: seq began again at 1, and every row in the new scrollback
    // arrived after the clear.
    rerender(<LogPanel {...props} opsLogToken="tok-b" events={[PROOF_WAIT]} />);
    expect(shownMessages()).toEqual(["export waiting for kernel proof"]);
  });

  it("sets the table's text size from the window's text buttons", () => {
    renderPanel();
    const table = screen.getByRole("table", { name: "System logs" });
    expect(table.style.fontSize).toBe("11px");
    fireEvent.click(screen.getByRole("button", { name: "Larger text" }));
    fireEvent.click(screen.getByRole("button", { name: "Larger text" }));
    expect(table.style.fontSize).toBe("13px");
    fireEvent.click(screen.getByRole("button", { name: "Smaller text" }));
    expect(table.style.fontSize).toBe("12px");
  });

  it("drops the invalid mark when an invalid search is erased", () => {
    renderPanel();
    const search = screen.getByPlaceholderText(/Search/);
    fireEvent.change(search, { target: { value: "(unclosed" } });
    expect(screen.getByText("invalid regex")).toBeTruthy();
    fireEvent.change(search, { target: { value: "" } });
    expect(screen.queryByText("invalid regex")).toBeNull();
    expect(shownMessages()).toHaveLength(2);
  });

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
