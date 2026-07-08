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

const {
  useBuilderWorld,
  useLibrarySave,
  resetCatalogStores,
  requestOutlineReveal,
  useOutlineReveal,
  claimOutlineReveal,
  requestLibraryReveal,
  useLibraryReveal,
} = await import("../useBuilderWorld");

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

describe("outline reveal is a channel separate from the Library reveal (IG-1)", () => {
  it("consumes an outline reveal once — claim returns it, then null (no replay)", () => {
    const { result } = renderHook(() => useOutlineReveal());
    act(() => requestOutlineReveal("space-777"));
    const reveal = result.current;
    expect(reveal?.segmentId).toBe("space-777");
    expect(claimOutlineReveal("outline", reveal)?.segmentId).toBe("space-777");
    // A second claim (e.g. a remount replaying the same reveal) yields nothing.
    expect(claimOutlineReveal("outline", reveal)).toBeNull();
  });

  it("a Library reveal never touches the outline channel", () => {
    const outline = renderHook(() => useOutlineReveal());
    const before = outline.result.current;
    act(() =>
      requestLibraryReveal({
        ref: "user:sites/x.yaml",
        family: "sites",
        id: "x",
      } as unknown as Parameters<typeof requestLibraryReveal>[0]),
    );
    // The outline store is unchanged by reference — the reveals do not cross.
    expect(outline.result.current).toBe(before);
  });

  it("an outline reveal never touches the Library channel", () => {
    const library = renderHook(() => useLibraryReveal());
    const before = library.result.current;
    act(() => requestOutlineReveal("ground-42"));
    expect(library.result.current).toBe(before);
  });
});

describe("useLibrarySave — the one 409-conflict save machine (M16)", () => {
  // Each successful save fires saveUserObject's IG-17 side effects (a family
  // refresh into the module-global catalog store); reset it per case.
  beforeEach(() => resetCatalogStores());
  type SaveResp = { ok: boolean; status: number; json: () => Promise<unknown> };
  /** Stub the save endpoint; every other fetch (the family refresh saveUserObject
   *  fires internally) returns an empty list. Records each save POST's body. */
  function stubSave(responder: (n: number) => SaveResp) {
    let n = 0;
    const posts: Array<{ overwrite: boolean; document: unknown; family: string }> = [];
    globalThis.fetch = vi.fn((url: string, init?: { body?: string }) => {
      if (String(url).includes("/builder/catalog/save")) {
        const body = init?.body ? JSON.parse(init.body) : {};
        posts.push({ overwrite: body.overwrite, document: body.document, family: body.family });
        return Promise.resolve(responder(n++));
      }
      return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve([]) });
    }) as unknown as typeof fetch;
    return posts;
  }
  const ok = (ref: string): SaveResp => ({
    ok: true,
    status: 200,
    json: () => Promise.resolve({ ref, family: "sites" }),
  });
  const err = (status: number, message: string): SaveResp => ({
    ok: false,
    status,
    json: () => Promise.resolve({ error: message }),
  });

  it("(1)(6) first save posts overwrite:false; success → saved + onSaved(ref, savedObject)", async () => {
    const posts = stubSave(() => ok("user:sites/x.yaml"));
    const { result } = renderHook(() => useLibrarySave("sites"));
    const onSaved = vi.fn();
    await act(async () => {
      await result.current.save({ site: { id: "x" } }, onSaved);
    });
    expect(posts[0]?.overwrite).toBe(false);
    expect(result.current.state).toEqual({ kind: "saved", ref: "user:sites/x.yaml" });
    // onSaved is invoked with the ref and the EXACT wrapper that was saved.
    expect(onSaved).toHaveBeenCalledWith("user:sites/x.yaml", { site: { id: "x" } });
  });

  it("(2) a 409 on a first save → conflict with the shared copy", async () => {
    stubSave(() => err(409, "exists"));
    const { result } = renderHook(() => useLibrarySave("sites"));
    await act(async () => {
      await result.current.save({ site: {} });
    });
    expect(result.current.state.kind).toBe("conflict");
    expect(result.current.label("Save to library")).toBe("Overwrite in library?");
  });

  it("(3) confirming a conflict re-saves with overwrite:true", async () => {
    const posts = stubSave((n) => (n === 0 ? err(409, "exists") : ok("user:sites/x.yaml")));
    const { result } = renderHook(() => useLibrarySave("sites"));
    await act(async () => {
      await result.current.save({ site: {} });
    });
    expect(result.current.state.kind).toBe("conflict");
    await act(async () => {
      await result.current.save({ site: {} });
    });
    expect(posts[1]?.overwrite).toBe(true);
    expect(result.current.state.kind).toBe("saved");
  });

  it("(4) a 409 while already in conflict → failed with the SECOND 409's message", async () => {
    // Distinct messages so the assertion proves the failed state carries the
    // second (overwrite) attempt's error, not the first.
    stubSave((n) => err(409, n === 0 ? "exists" : "still exists"));
    const { result } = renderHook(() => useLibrarySave("sites"));
    await act(async () => {
      await result.current.save({ site: {} });
    });
    await act(async () => {
      await result.current.save({ site: {} });
    });
    expect(result.current.state).toEqual({ kind: "failed", message: "still exists" });
  });

  it("(5) a non-409 error → failed with the server message verbatim", async () => {
    stubSave(() => err(500, "server boom"));
    const { result } = renderHook(() => useLibrarySave("sites"));
    await act(async () => {
      await result.current.save({ site: {} });
    });
    expect(result.current.state).toEqual({ kind: "failed", message: "server boom" });
  });

  it("shows the shared 'Saving…' label while a save is in flight", async () => {
    // Hold the save open so the {saving} state is observable — this is the
    // in-flight affordance GroundEditor/SiteEditor gained.
    const gate = deferred<SaveResp>();
    globalThis.fetch = vi.fn((url: string) =>
      String(url).includes("/builder/catalog/save")
        ? gate.promise
        : Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve([]) }),
    ) as unknown as typeof fetch;
    const { result } = renderHook(() => useLibrarySave("sites"));
    let done!: Promise<void>;
    act(() => {
      done = result.current.save({ site: {} });
    });
    expect(result.current.saving).toBe(true);
    expect(result.current.label("Save to library")).toBe("Saving…");
    await act(async () => {
      gate.resolve(ok("user:sites/x.yaml"));
      await done;
    });
    expect(result.current.state.kind).toBe("saved");
  });

  it("reset() returns the machine to idle (customize-node reuse)", async () => {
    stubSave(() => err(409, "exists"));
    const { result } = renderHook(() => useLibrarySave("sites"));
    await act(async () => {
      await result.current.save({ site: {} });
    });
    expect(result.current.state.kind).toBe("conflict");
    act(() => result.current.reset());
    expect(result.current.state).toEqual({ kind: "idle" });
  });
});
