// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
/** The error-message policy for the builder and session REST calls.
 *
 *  The API's error bodies are the BuilderResolveRefusal JSON envelope
 *  ({ error: string, ... }); when the resolver refuses a document it puts its
 *  own words in `error`. Surfacing those verbatim preserves the reason the
 *  session was
 *  rejected. Two entry points under one policy, because a failed request takes
 *  one of two shapes: a Response the server sent, or a rejection with no
 *  Response at all (a network failure).
 *
 *  The resolve/download and catalog paths route through here. Some older call
 *  sites still read the envelope inline with their own domain-specific fallback.
 */

/** Shown when a caught fetch rejection carries no message of its own. */
export const NETWORK_ERROR_MESSAGE = "network request failed";

/** A failed Response → a human message: the JSON envelope's `error` field when
 *  it is a non-empty string (e.g. the resolver's refusal), else a status-code
 *  fallback for a non-JSON, field-less, non-string, or empty-string body. An
 *  empty error must fall through to the status message, not be passed on as an
 *  empty string that a caller would mistake for "no error". */
export async function apiErrorMessage(response: Response): Promise<string> {
  try {
    const data = await response.json();
    if (data && typeof data.error === "string" && data.error) return data.error;
  } catch {
    /* non-JSON error body */
  }
  return `request failed (${response.status})`;
}

/** A caught fetch rejection (network failure — no Response exists) → a human
 *  message: the error's own message, or the named constant when it has none. */
export function apiErrorFromException(err: unknown): string {
  if (err instanceof Error && err.message) return err.message;
  return NETWORK_ERROR_MESSAGE;
}
