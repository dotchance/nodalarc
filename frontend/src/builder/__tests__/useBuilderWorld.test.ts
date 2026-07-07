// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
/** useBuilderWorld data-layer contract (N39): the resolve loop keeps nothing
 *  stale on screen. A late response for a superseded edit never overwrites a
 *  newer one; a failed resolve clears the world ("the error is the state");
 *  and clear() invalidates any in-flight response so a resolve that lands
 *  after a teardown cannot repaint a world the user has left behind. */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { renderHook, act } from "@testing-library/react";

vi.mock("../../config", () => ({
  REST_URL: "http://test:8080",
  authHeaders: (extra?: Record<string, string>) => ({ ...extra }),
}));

const { useBuilderWorld } = await import("../useBuilderWorld");

interface Deferred<T> {
  promise: Promise<T>;
  resolve: (value: T) => void;
  reject: (reason?: unknown) => void;
}
function deferred<T>(): Deferred<T> {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

/** A resolve-world response body identified by its world marker. */
function check(marker: string) {
  return {
    ok: true,
    json: () =>
      Promise.resolve({
        world: { marker, session: { name: marker }, nodes: [] },
        document_yaml: `# ${marker}`,
        document: { session: { name: marker } },
        artifact_sha256: `sha-${marker}`,
      }),
  };
}

const sessionsOk = { ok: true, json: () => Promise.resolve([]) };

describe("useBuilderWorld — the resolve loop keeps nothing stale (N39)", () => {
  let fetchMock: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    fetchMock = vi.fn();
    globalThis.fetch = fetchMock as unknown as typeof fetch;
  });

  it("a late response for a superseded resolve never overwrites the newer one", async () => {
    const slow = deferred<unknown>();
    const fast = deferred<unknown>();
    let resolveCall = 0;
    fetchMock.mockImplementation((url: string) => {
      if (url.includes("/api/v1/sessions")) return Promise.resolve(sessionsOk);
      resolveCall += 1;
      return resolveCall === 1 ? slow.promise : fast.promise;
    });

    const { result } = renderHook(() => useBuilderWorld());
    await act(async () => {}); // flush the mount sessions fetch

    // Fire A (seq N), then B (seq N+1) before A lands.
    act(() => {
      void result.current.resolveDocument({ n: "A" });
    });
    act(() => {
      void result.current.resolveDocument({ n: "B" });
    });

    // B lands first and is current.
    await act(async () => {
      fast.resolve(check("B"));
      await fast.promise;
    });
    expect((result.current.world as { marker?: string })?.marker).toBe("B");

    // A lands late — it is stale (seq N < N+1) and must be discarded.
    await act(async () => {
      slow.resolve(check("A"));
      await slow.promise;
    });
    expect((result.current.world as { marker?: string })?.marker).toBe("B");
    expect(result.current.settledArtifactSha256).toBe("sha-B");
  });

  it("a failed resolve clears the world — the error is the state", async () => {
    let resolveCall = 0;
    fetchMock.mockImplementation((url: string) => {
      if (url.includes("/api/v1/sessions")) return Promise.resolve(sessionsOk);
      resolveCall += 1;
      return resolveCall === 1
        ? Promise.resolve(check("A"))
        : Promise.resolve({
            ok: false,
            status: 400,
            json: () => Promise.resolve({ error: "the session does not resolve" }),
          });
    });

    const { result } = renderHook(() => useBuilderWorld());
    await act(async () => {});

    await act(async () => {
      await result.current.resolveDocument({ n: "A" });
    });
    expect((result.current.world as { marker?: string })?.marker).toBe("A");

    await act(async () => {
      await result.current.resolveDocument({ n: "B" });
    });
    expect(result.current.world).toBeNull();
    expect(result.current.error).toBe("the session does not resolve");
    expect(result.current.settledArtifactSha256).toBeNull();
  });

  it("clear() invalidates an in-flight response so it cannot repaint", async () => {
    const inFlight = deferred<unknown>();
    fetchMock.mockImplementation((url: string) => {
      if (url.includes("/api/v1/sessions")) return Promise.resolve(sessionsOk);
      return inFlight.promise;
    });

    const { result } = renderHook(() => useBuilderWorld());
    await act(async () => {});

    act(() => {
      void result.current.resolveDocument({ n: "A" });
    });
    // Tear down before the response lands.
    act(() => {
      result.current.clear();
    });
    await act(async () => {
      inFlight.resolve(check("A"));
      await inFlight.promise;
    });
    expect(result.current.world).toBeNull();
    expect(result.current.settledArtifactSha256).toBeNull();
  });
});
