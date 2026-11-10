// Copyright 2024-2026 .chance (dotchance)
// SPDX-License-Identifier: AGPL-3.0-only
import { describe, it, expect } from "vitest";
import { linkEventLabel } from "../linkEvents";

describe("link-event registry (single source for link-lifecycle reasons)", () => {
  it("linkEventLabel resolves via the registry and falls back to the raw code, never invents text", () => {
    expect(linkEventLabel("vis_lost")).toBe("Out of range");
    expect(linkEventLabel("some_future_code")).toBe("some_future_code");
    expect(linkEventLabel(null)).toBe("");
  });
});
