// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
/** One error-message policy: the envelope's `error` reaches the user verbatim
 *  (customer-trust), with a status fallback for a non-envelope body and a named
 *  constant for a network failure that carries no Response. */
import { describe, it, expect } from "vitest";
import { apiErrorMessage, apiErrorFromException, NETWORK_ERROR_MESSAGE } from "../apiError";

function resp(status: number, json: () => Promise<unknown>): Response {
  return { status, json } as unknown as Response;
}

describe("apiErrorMessage", () => {
  it("surfaces the JSON envelope's error field verbatim (the resolver's refusal)", async () => {
    const r = resp(422, () => Promise.resolve({ error: "segment 'leo' has no satellites" }));
    expect(await apiErrorMessage(r)).toBe("segment 'leo' has no satellites");
  });

  it("surfaces the envelope error on any status, not only 422", async () => {
    // The helper serves every REST call; a 409 conflict or 400 validation that
    // carries the envelope must still reach the user, not a status fallback.
    const r = resp(409, () => Promise.resolve({ error: "name already exists" }));
    expect(await apiErrorMessage(r)).toBe("name already exists");
  });

  it("falls back to the status code for a non-JSON body", async () => {
    const r = resp(500, () => Promise.reject(new Error("not json")));
    expect(await apiErrorMessage(r)).toBe("request failed (500)");
  });

  it("falls back to the status code for JSON with no error field", async () => {
    const r = resp(400, () => Promise.resolve({ detail: "nope" }));
    expect(await apiErrorMessage(r)).toBe("request failed (400)");
  });

  it("falls back to the status code for an EMPTY error string (never a network failure)", async () => {
    // An empty error must not pass through as "" — a downstream network-vs-server
    // classifier would misread the falsy string as a network failure.
    const r = resp(422, () => Promise.resolve({ error: "" }));
    expect(await apiErrorMessage(r)).toBe("request failed (422)");
  });

  it("falls back to the status code for a non-string error field", async () => {
    const r = resp(500, () => Promise.resolve({ error: { code: 7 } }));
    expect(await apiErrorMessage(r)).toBe("request failed (500)");
  });

  it("falls back to the status code for a null JSON body", async () => {
    const r = resp(404, () => Promise.resolve(null));
    expect(await apiErrorMessage(r)).toBe("request failed (404)");
  });
});

describe("apiErrorFromException", () => {
  it("returns the caught error's own message (network failure text)", () => {
    expect(apiErrorFromException(new Error("Failed to fetch"))).toBe("Failed to fetch");
  });

  it("falls back to the named constant when the error has no message", () => {
    expect(apiErrorFromException(new Error(""))).toBe(NETWORK_ERROR_MESSAGE);
  });

  it("falls back to the named constant for a non-Error rejection", () => {
    expect(apiErrorFromException("boom")).toBe(NETWORK_ERROR_MESSAGE);
    expect(apiErrorFromException(undefined)).toBe(NETWORK_ERROR_MESSAGE);
  });
});
