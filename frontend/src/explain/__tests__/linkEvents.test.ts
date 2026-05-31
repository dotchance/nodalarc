// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
import { describe, it, expect } from "vitest";
import { LINK_EVENT_REGISTRY, LINK_EVENT_REASONS, linkEventLabel } from "../linkEvents";
import { FAMILIES } from "../families";

const VALID_FAMILIES = new Set<string>(FAMILIES);

describe("link-event registry (single source for link-lifecycle reasons)", () => {
  it("every record is well-formed and family-classified with a canonical family", () => {
    for (const [code, r] of Object.entries(LINK_EVENT_REGISTRY)) {
      expect(r.code).toBe(code);
      expect(r.label.length).toBeGreaterThan(0);
      expect(r.sentence.length).toBeGreaterThan(0);
      expect(VALID_FAMILIES.has(r.family)).toBe(true);
    }
  });

  it("LINK_EVENT_REASONS lists exactly the registry codes", () => {
    expect([...LINK_EVENT_REASONS].sort()).toEqual(Object.keys(LINK_EVENT_REGISTRY).sort());
  });

  it("covers the documented backend LinkUp/LinkDown reason codes", () => {
    // Mirrors lib/nodalarc/models/link_events.py LinkUp/LinkDown `reason` comments; the
    // cross-language contract test enforces this against the backend constant.
    const documented = [
      "vis_gained",
      "gs_above_horizon",
      "scenario_inject_up",
      "scenario_reconciliation",
      "vis_lost",
      "gs_below_horizon",
      "tracking_exceeded",
      "terminal_exhausted",
      "scenario_inject_down",
      "satellite_loss",
    ];
    for (const code of documented) expect(LINK_EVENT_REGISTRY[code], code).toBeDefined();
  });

  it("linkEventLabel resolves via the registry and falls back to the raw code, never invents text", () => {
    expect(linkEventLabel("vis_lost")).toBe("Out of range");
    expect(linkEventLabel("some_future_code")).toBe("some_future_code");
    expect(linkEventLabel(null)).toBe("");
  });
});
