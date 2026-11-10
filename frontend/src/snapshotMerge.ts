// Copyright 2024-2026 .chance (dotchance)
// SPDX-License-Identifier: AGPL-3.0-only
/** Client-side merge for the incremental snapshot feed.
 *
 *  VS-API ships ops_events incrementally (each connection has a server-
 *  side cursor; a frame carries only events that connection has not
 *  seen). The client owns the scrollback: merge new events by seq, and
 *  REPLACE the scrollback when ops_log_token changes —
 *  a VS-API restart restarts the seq space, and merging across two seq
 *  spaces would dedupe fresh events against stale ones.
 */

import type { StateSnapshot } from "./generated/vsApi";

const OPS_SCROLLBACK_CAP = 500;

export function mergeSnapshot(
  prev: StateSnapshot | null,
  next: StateSnapshot,
): StateSnapshot {
  if (!prev || prev.ops_log_token !== next.ops_log_token) {
    return next;
  }
  const seen = new Set(next.ops_events.map((e) => e.seq));
  const carried = prev.ops_events.filter((e) => !seen.has(e.seq));
  return {
    ...next,
    ops_events: [...carried, ...next.ops_events].slice(-OPS_SCROLLBACK_CAP),
  };
}
