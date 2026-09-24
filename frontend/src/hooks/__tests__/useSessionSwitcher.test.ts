// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
/** The switch lifecycle contract: `switching` follows the websocket
 * transition signal (session_transitioning → session_ready/failed), never
 * snapshot fields — the snapshot is nulled for the whole transition window,
 * so deriving progress from it hangs the overlay forever. */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { renderHook, act } from "@testing-library/react";
import type { SessionInfo } from "../../types";

vi.mock("../../config", () => ({
  REST_URL: "http://test:8080",
  authHeaders: (extra?: Record<string, string>) => ({ ...extra }),
}));

const { useSessionSwitcher } = await import("../useSessionSwitcher");
const digest = `sha256:${"0".repeat(64)}`;
const session = (name: string): SessionInfo => ({
  source_id: { kind: "catalog", session_ref: `nodalarc:sessions/${name}.yaml` },
  name,
  source: "nodalarc",
  constellation: "leo",
  routing_stack: "isis",
  deploy_allowed: true,
  source_revision: digest,
  document_digest: digest,
  dependency_digest: digest,
});

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
    const selected = session("session-01");
    await act(async () => { await result.current.switchSession(selected, true); });
    const switchCall = fetchMock.mock.calls.find(
      (c: unknown[]) => String(c[0]).includes("/sessions/switch"),
    );
    expect(switchCall).toBeTruthy();
    const body = JSON.parse(switchCall![1]!.body as string);
    expect(body).toEqual({
      source: selected.source_id,
      expected_source_revision: digest,
      expected_document_digest: digest,
      expected_dependency_digest: digest,
      record_history: true,
    });
  });

  it("no double switch while already switching", async () => {
    const { result } = renderHook(() => useSessionSwitcher(false));
    await act(async () => { await result.current.switchSession(session("session-01"), false); });
    await act(async () => { await result.current.switchSession(session("session-02"), false); });
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
    let outcome: unknown;
    await act(async () => {
      outcome = await result.current.switchSession(session("session-03"), false);
    });
    expect(result.current.switching).toBe(false);
    expect(outcome).toEqual({ ok: false, message: "network error" });
  });

  it("returns VS-API's reason when it refuses a switch", async () => {
    fetchMock.mockReset();
    fetchMock
      .mockResolvedValueOnce({ ok: true, json: () => Promise.resolve([]) })
      .mockResolvedValueOnce({
        ok: false,
        status: 409,
        json: () => Promise.resolve({ code: "prepared_session.stale", message: "stale digest" }),
      });
    const { result } = renderHook(() => useSessionSwitcher(false));
    let outcome: unknown;
    await act(async () => {
      outcome = await result.current.switchSession(session("session-04"), false);
    });
    expect(outcome).toEqual({ ok: false, message: "stale digest" });
    expect(result.current.switching).toBe(false);
  });

  it("returns ok when VS-API accepts a switch", async () => {
    const { result } = renderHook(() => useSessionSwitcher(false));
    let outcome: unknown;
    await act(async () => {
      outcome = await result.current.switchSession(session("session-05"), false);
    });
    expect(outcome).toEqual({ ok: true });
  });

  it("says why the session list did not load", async () => {
    fetchMock.mockReset();
    fetchMock.mockResolvedValueOnce({
      ok: false,
      status: 503,
      json: () => Promise.resolve({ code: "catalog_read.failed", message: "catalog unreadable" }),
    });
    const { result } = renderHook(() => useSessionSwitcher(false));
    await vi.waitFor(() => expect(result.current.sessionsError).toBe("catalog unreadable"));
    expect(result.current.sessions).toEqual([]);
  });

  it("clears once the websocket transition completes", async () => {
    const { result, rerender } = renderHook(
      ({ transitioning }) => useSessionSwitcher(transitioning),
      { initialProps: { transitioning: false } },
    );
    await act(async () => { await result.current.switchSession(session("session-04"), false); });
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
    await act(async () => { await result.current.switchSession(session("session-05"), false); });
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
