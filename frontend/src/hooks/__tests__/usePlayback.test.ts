// Copyright 2024-2026 .chance (dotchance)
// Licensed under the NodalArc Source Available License 1.0. See LICENSE file.
import { describe, it, expect, vi, beforeEach } from "vitest";
import { renderHook, act } from "@testing-library/react";

vi.mock("../../config", () => ({
  REST_URL: "http://test:8080",
  authHeaders: (extra?: Record<string, string>) => ({ ...extra }),
}));

const { usePlayback } = await import("../usePlayback");

describe("usePlayback", () => {
  let fetchMock: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: () => Promise.resolve({ paused: false, speed: 1 }),
    });
    globalThis.fetch = fetchMock;
  });

  it("pause sends correct action", async () => {
    const { result } = renderHook(() => usePlayback());
    await act(async () => { await result.current.pause(); });
    const body = JSON.parse(fetchMock.mock.calls[0][1].body);
    expect(body.action).toBe("pause");
  });

  it("resume sends correct action", async () => {
    const { result } = renderHook(() => usePlayback());
    await act(async () => { await result.current.resume(); });
    const body = JSON.parse(fetchMock.mock.calls[0][1].body);
    expect(body.action).toBe("resume");
  });

  it("setSpeed sends factor", async () => {
    const { result } = renderHook(() => usePlayback());
    await act(async () => { await result.current.setSpeed(10); });
    const body = JSON.parse(fetchMock.mock.calls[0][1].body);
    expect(body.action).toBe("set_speed");
    expect(body.factor).toBe(10);
  });

  it("seek sends target_sim_time", async () => {
    const { result } = renderHook(() => usePlayback());
    await act(async () => { await result.current.seek("2026-01-01T12:00:00Z"); });
    const body = JSON.parse(fetchMock.mock.calls[0][1].body);
    expect(body.action).toBe("seek");
    expect(body.target_sim_time).toBe("2026-01-01T12:00:00Z");
  });

  it("response updates local state", async () => {
    fetchMock.mockResolvedValue({
      ok: true,
      json: () => Promise.resolve({ paused: true, speed: 5 }),
    });
    const { result } = renderHook(() => usePlayback());
    await act(async () => { await result.current.pause(); });
    expect(result.current.paused).toBe(true);
    expect(result.current.speed).toBe(5);
  });

  it("snapshot props override local state", () => {
    const { result, rerender } = renderHook(
      ({ paused, speed }) => usePlayback(paused, speed),
      { initialProps: { paused: false, speed: 1 } },
    );
    expect(result.current.paused).toBe(false);
    rerender({ paused: true, speed: 10 });
    expect(result.current.paused).toBe(true);
    expect(result.current.speed).toBe(10);
  });

  it("fetch failure does not crash", async () => {
    fetchMock.mockRejectedValue(new Error("network"));
    const { result } = renderHook(() => usePlayback());
    await act(async () => { await result.current.pause(); });
    expect(result.current.loading).toBe(false);
  });
});
