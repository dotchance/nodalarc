// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
/** Client merge for the incremental snapshot feed. */

import { describe, it, expect } from "vitest";
import { mergeSnapshot } from "../snapshotMerge";
import type { StampedOpsEvent, StateSnapshot } from "../types";

const base = (over: Partial<StateSnapshot>): StateSnapshot =>
  ({ ops_log_token: "tok-a", ...over }) as StateSnapshot;

const ev = (seq: number, code = `E${seq}`): StampedOpsEvent =>
  ({ seq, code, timestamp: "", tenant_id: "", session_id: "", source: "ome", hostname: "", level: "info", message: "" });

describe("mergeSnapshot", () => {
  it("appends incremental events to the scrollback", () => {
    const prev = base({ ops_events: [ev(1), ev(2)] });
    const next = base({ ops_events: [ev(3)] });
    expect(mergeSnapshot(prev, next).ops_events!.map((e) => e.seq)).toEqual([1, 2, 3]);
  });

  it("dedupes by seq on reconnect full-tail resend", () => {
    const prev = base({ ops_events: [ev(1), ev(2), ev(3)] });
    const next = base({ ops_events: [ev(2), ev(3), ev(4)] });
    expect(mergeSnapshot(prev, next).ops_events!.map((e) => e.seq)).toEqual([1, 2, 3, 4]);
  });

  it("replaces the scrollback when the seq space changes (server restart)", () => {
    const prev = base({ ops_events: [ev(1), ev(2)] });
    const next = { ...base({ ops_events: [ev(1, "FRESH")] }), ops_log_token: "tok-b" };
    const merged = mergeSnapshot(prev, next as StateSnapshot);
    expect(merged.ops_events!.map((e) => e.code)).toEqual(["FRESH"]);
  });

  it("caps the scrollback at 500", () => {
    const prev = base({ ops_events: Array.from({ length: 500 }, (_v, i) => ev(i + 1)) });
    const next = base({ ops_events: [ev(501)] });
    const merged = mergeSnapshot(prev, next);
    expect(merged.ops_events!.length).toBe(500);
    expect(merged.ops_events![0]!.seq).toBe(2);
    expect(merged.ops_events![499]!.seq).toBe(501);
  });
});
