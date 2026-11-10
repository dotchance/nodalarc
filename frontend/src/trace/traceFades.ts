// Copyright 2024-2026 .chance (dotchance)
// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms under AGPL-3.0 Section 7 are in the NOTICE file.
/** When a traced path is drawn, shared by the globe and the topology view.
 *
 *  A live trace is drawn at full opacity with its dash flowing. A trace this
 *  view saw stop, or leave the snapshot, stops flowing, holds, and fades out
 *  like a failed link; it stays gone while the snapshot keeps its stopped
 *  result. A trace first seen already stopped is not drawn.
 */

import { FAIL_FADE_MS, FAIL_HOLD_MS } from "../config";
import type { TracedPath } from "../generated/vsApi";

/** The opacity of a stopped trace `elapsedMs` after it stopped, like a failed
 *  link: full while it holds, fading to nothing, then null once it is gone. */
export function stoppedTraceOpacity(elapsedMs: number): number | null {
  if (elapsedMs < FAIL_HOLD_MS) return 1;
  if (elapsedMs < FAIL_HOLD_MS + FAIL_FADE_MS) return 1 - (elapsedMs - FAIL_HOLD_MS) / FAIL_FADE_MS;
  return null;
}

export interface DrawnTrace {
  path: TracedPath;
  opacity: number;
  /** True while the trace is live: its measured dash flows. */
  animate: boolean;
}

interface FadeEntry {
  /** The last version of the path the snapshot carried. */
  path: TracedPath;
  /** When this view saw the trace stop (performance.now()); null while live. */
  stoppedAt: number | null;
  present: boolean;
}

/** A trace is identified by the endpoints it runs between. */
export function traceKey(path: Pick<TracedPath, "src_node" | "dst_node">): string {
  return `${path.src_node}\u2192${path.dst_node}`;
}

export class TraceFades {
  private readonly entries = new Map<string, FadeEntry>();

  /** Record a snapshot's traced paths at `now`. */
  observe(paths: readonly TracedPath[], now: number): void {
    for (const entry of this.entries.values()) entry.present = false;
    for (const path of paths) {
      const entry = this.entries.get(traceKey(path));
      // Live: no stop time. Seen stopping: now. Already stopping: kept.
      // First seen stopped: long gone.
      const stoppedAt = path.tracing
        ? null
        : entry === undefined
          ? -Infinity
          : (entry.stoppedAt ?? now);
      this.entries.set(traceKey(path), { path, stoppedAt, present: true });
    }
    for (const [key, entry] of this.entries) {
      if (entry.present) continue;
      if (entry.stoppedAt === null) {
        entry.stoppedAt = now;
      } else if (stoppedTraceOpacity(now - entry.stoppedAt) === null) {
        this.entries.delete(key);
      }
    }
  }

  /** The traces to draw at `now`, in the order they first appeared. */
  drawn(now: number): DrawnTrace[] {
    const drawn: DrawnTrace[] = [];
    for (const { path, stoppedAt } of this.entries.values()) {
      if (stoppedAt === null) {
        drawn.push({ path, opacity: 1, animate: true });
        continue;
      }
      const opacity = stoppedTraceOpacity(now - stoppedAt);
      if (opacity !== null) drawn.push({ path, opacity, animate: false });
    }
    return drawn;
  }

  /** The one trace a view with room for a single trace draws at `now`: the live
   *  trace when one runs, else the stopped trace still fading. */
  primary(now: number): DrawnTrace | null {
    const drawn = this.drawn(now);
    return drawn.find((trace) => trace.animate) ?? drawn[0] ?? null;
  }
}
