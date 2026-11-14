// Copyright 2024-2026 .chance (dotchance)
// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms under AGPL-3.0 Section 7 are in the NOTICE file.
import { describe, it, expect } from "vitest";
import { linkEventLabel } from "../linkEvents";

describe("link-event registry (single source for link-lifecycle reasons)", () => {
  it("linkEventLabel resolves via the registry and falls back to the raw code, never invents text", () => {
    expect(linkEventLabel("not_in_ome_snapshot")).toBe("No longer scheduled");
    // A link OME stopped scheduling carries OME's reason, labelled by the one reason table.
    expect(linkEventLabel("tracking_exceeded")).toBe("Too fast to track");
    expect(linkEventLabel("range_exceeded")).toBe("Out of range");
    expect(linkEventLabel("outranked")).toBe("Outranked");
    expect(linkEventLabel("some_future_code")).toBe("some_future_code");
    expect(linkEventLabel(null)).toBe("");
  });
});
