// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
/** The error-message policy for the builder and session REST calls.
 *
 *  A refused request answers with the ApiRefusal envelope ({ code, message })
 *  or one of the richer per-operation refusals that share its `message` field;
 *  when the resolver refuses a document it puts its own words there. Routes
 *  outside that contract still answer with { error: string }. Surfacing either
 *  verbatim preserves the reason the request was refused. Two entry points
 *  under one policy, because a failed request takes one of two shapes: a
 *  Response the server sent, or a rejection with no Response at all (a network
 *  failure).
 */

/** Shown when a caught fetch rejection carries no message of its own. */
export const NETWORK_ERROR_MESSAGE = "network request failed";

/** A failed Response → a human message: the envelope's `message`, else the
 *  legacy `error` field, when it is a non-empty string; else a status-code
 *  fallback for a non-JSON, field-less, non-string, or empty-string body. An
 *  empty message must fall through to the status message, not be passed on as
 *  an empty string that a caller would mistake for "no error". */
export async function apiErrorMessage(response: Response): Promise<string> {
  try {
    const data = await response.json();
    if (data && typeof data === "object") {
      if (typeof data.message === "string" && data.message) return data.message;
      if (typeof data.error === "string" && data.error) return data.error;
    }
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
