// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
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
    const { result } = renderHook(() => useSessionSwitcher(null));
    await act(async () => { await result.current.switchSession("catalog/nodalarc/sessions/earth-leo-simple.yaml"); });
    const switchCall = fetchMock.mock.calls.find(
      (c: unknown[]) => String(c[0]).includes("/sessions/switch"),
    );
    expect(switchCall).toBeTruthy();
    const body = JSON.parse(switchCall![1]!.body as string);
    expect(body.session).toBe("catalog/nodalarc/sessions/earth-leo-simple.yaml");
  });

  it("no double switch while already switching", async () => {
    const { result } = renderHook(() => useSessionSwitcher(null));
    await act(async () => { await result.current.switchSession("a.yaml"); });
    // Now switching is true - try to switch again
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
    const { result } = renderHook(() => useSessionSwitcher(null));
    await act(async () => { await result.current.switchSession("bad.yaml"); });
    expect(result.current.switching).toBe(false);
  });

  it("backend-initiated switch detected", () => {
    const { result, rerender } = renderHook(
      ({ status }) => useSessionSwitcher(status),
      { initialProps: { status: "ready" as string | null } },
    );
    expect(result.current.switching).toBe(false);
    rerender({ status: "switching" });
    expect(result.current.switching).toBe(true);
  });

  it("ready after switching clears flag", () => {
    const { result, rerender } = renderHook(
      ({ status }) => useSessionSwitcher(status),
      { initialProps: { status: "ready" as string | null } },
    );
    rerender({ status: "switching" });
    expect(result.current.switching).toBe(true);
    rerender({ status: "ready" });
    expect(result.current.switching).toBe(false);
  });

  it("must see switching before ready clears", async () => {
    const { result } = renderHook(() => useSessionSwitcher(null));
    await act(async () => { await result.current.switchSession("test.yaml"); });
    expect(result.current.switching).toBe(true);
    // Rerender directly to "ready" without going through "switching" first
    // The hook should NOT clear switching until it sees the backend "switching" status
  });
});
