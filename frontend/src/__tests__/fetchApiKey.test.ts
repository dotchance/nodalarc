// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
/** The API key comes from VS-API's token endpoint; a failure is reported, and the
 *  stored key never stands in for it. */

import { afterEach, describe, expect, it, vi } from "vitest";
import { fetchApiKey, getApiKey, setApiKey } from "../config";

afterEach(() => {
  vi.unstubAllGlobals();
  sessionStorage.clear();
});

describe("fetchApiKey", () => {
  it("stores and returns the token VS-API issues", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(() => Promise.resolve({ ok: true, json: () => Promise.resolve({ token: "fresh" }) })),
    );
    await expect(fetchApiKey()).resolves.toBe("fresh");
    expect(getApiKey()).toBe("fresh");
  });

  it("rejects with the network failure instead of returning the stored key", async () => {
    setApiKey("stale");
    vi.stubGlobal("fetch", vi.fn(() => Promise.reject(new Error("Failed to fetch"))));
    await expect(fetchApiKey()).rejects.toThrow("Failed to fetch");
  });

  it("rejects with VS-API's refusal", async () => {
    setApiKey("stale");
    vi.stubGlobal(
      "fetch",
      vi.fn(() =>
        Promise.resolve({
          ok: false,
          status: 503,
          json: () => Promise.resolve({ code: "x", message: "token service down" }),
        }),
      ),
    );
    await expect(fetchApiKey()).rejects.toThrow("token service down");
  });

  it("rejects an answer without a token", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(() => Promise.resolve({ ok: true, json: () => Promise.resolve({}) })),
    );
    await expect(fetchApiKey()).rejects.toThrow("without a token");
  });
});
