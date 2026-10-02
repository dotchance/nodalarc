// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
import { describe, it, expect } from "vitest";
import { linkEventLabel } from "../linkEvents";

describe("link-event registry (single source for link-lifecycle reasons)", () => {
  it("linkEventLabel resolves via the registry and falls back to the raw code, never invents text", () => {
    expect(linkEventLabel("vis_lost")).toBe("Out of range");
    expect(linkEventLabel("some_future_code")).toBe("some_future_code");
    expect(linkEventLabel(null)).toBe("");
  });
});
