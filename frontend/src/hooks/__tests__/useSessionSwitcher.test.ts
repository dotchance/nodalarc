// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
/** The switch lifecycle contract: `switching` follows the websocket
 * transition signal (session_transitioning → session_ready/failed), never
 * snapshot fields — the snapshot is nulled for the whole transition window,
 * so deriving progress from it hangs the overlay forever. */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { renderHook, act } from "@testing-library/react";

vi.mock("../../config", () => ({
  REST_URL: "http://test:8080",
  authHeaders: (extra?: Record<string, string>) => ({ ...extra }),
}));

const { useSessionSwitcher } = await import("../useSessionSwitcher");

describe("useSessionSwitcher", () => {
  let fetchMock: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    fetchMock = vi.fn()
      .mockResolvedValueOnce({ ok: true, json: () => Promise.resolve([]) })
      .mockResolvedValue({ ok: true, json: () => Promise.resolve({}) });
    globalThis.fetch = fetchMock as unknown as typeof fetch;
  });

  it("switchSession sends deploy request", async () => {
    const { result } = renderHook(() => useSessionSwitcher(false));
    await act(async () => { await result.current.switchSession("catalog/nodalarc/sessions/earth-leo-simple.yaml"); });
    const switchCall = fetchMock.mock.calls.find(
      (c: unknown[]) => String(c[0]).includes("/sessions/switch"),
    );
    expect(switchCall).toBeTruthy();
    const body = JSON.parse(switchCall![1]!.body as string);
    expect(body.session).toBe("catalog/nodalarc/sessions/earth-leo-simple.yaml");
  });

  it("no double switch while already switching", async () => {
    const { result } = renderHook(() => useSessionSwitcher(false));
    await act(async () => { await result.current.switchSession("a.yaml"); });
    await act(async () => { await result.current.switchSession("b.yaml"); });
    const switchCalls = fetchMock.mock.calls.filter(
      (c: unknown[]) => String(c[0]).includes("/sessions/switch"),
    );
    expect(switchCalls).toHaveLength(1);
  });

  it("switch failure clears switching flag", async () => {
    fetchMock.mockReset();
    fetchMock
      .mockResolvedValueOnce({ ok: true, json: () => Promise.resolve([]) })
      .mockRejectedValueOnce(new Error("network error"));
    const { result } = renderHook(() => useSessionSwitcher(false));
    await act(async () => { await result.current.switchSession("bad.yaml"); });
    expect(result.current.switching).toBe(false);
  });

  it("clears once the websocket transition completes", async () => {
    const { result, rerender } = renderHook(
      ({ transitioning }) => useSessionSwitcher(transitioning),
      { initialProps: { transitioning: false } },
    );
    await act(async () => { await result.current.switchSession("test.yaml"); });
    expect(result.current.switching).toBe(true);

    // The websocket lifecycle takes over, then ends — the regression this
    // pins: the hook once watched snapshot.session_status, which is null
    // for the whole transition, so the overlay hung at "Deploying" forever.
    rerender({ transitioning: true });
    expect(result.current.switching).toBe(true);
    rerender({ transitioning: false });
    expect(result.current.switching).toBe(false);
  });

  it("does not clear before the transition has been observed", async () => {
    const { result, rerender } = renderHook(
      ({ transitioning }) => useSessionSwitcher(transitioning),
      { initialProps: { transitioning: false } },
    );
    await act(async () => { await result.current.switchSession("test.yaml"); });
    // No transition seen yet — a rerender without one must not clear.
    rerender({ transitioning: false });
    expect(result.current.switching).toBe(true);
  });

  it("refreshes the session list when any switch completes", async () => {
    const { rerender } = renderHook(
      ({ transitioning }) => useSessionSwitcher(transitioning),
      { initialProps: { transitioning: false } },
    );
    const listCallsBefore = fetchMock.mock.calls.filter(
      (c: unknown[]) => String(c[0]).endsWith("/api/v1/sessions"),
    ).length;
    // Backend-initiated switch (another operator, a deploy): observe its
    // lifecycle without a local trigger.
    rerender({ transitioning: true });
    rerender({ transitioning: false });
    const listCallsAfter = fetchMock.mock.calls.filter(
      (c: unknown[]) => String(c[0]).endsWith("/api/v1/sessions"),
    ).length;
    expect(listCallsAfter).toBe(listCallsBefore + 1);
  });
});
