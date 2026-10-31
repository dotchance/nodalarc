// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
/** Editors show an incomplete draft as incomplete: no null, zero or invented value. */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, cleanup } from "@testing-library/react";

vi.mock("../../config", () => ({
  REST_URL: "http://test:8080",
  authHeaders: (extra?: Record<string, string>) => ({ ...extra }),
}));

const { SessionEditor } = await import("../SessionEditor");
const { resetCatalogStores } = await import("../useBuilderWorld");
import type { JsonValue } from "../generated/builderApi";

beforeEach(() => resetCatalogStores());
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("truthful incomplete session summaries", () => {
  const editor = (time: Record<string, JsonValue>, simulation: Record<string, JsonValue> | null) => (
    <SessionEditor
      name="incomplete"
      meta={{ name: "incomplete" }}
      time={time}
      simulation={simulation}
      pending={false}
      onStage={() => {}}
      onApply={() => {}}
      onDiscard={() => {}}
    />
  );

  it("never renders null time values as an authored rate", () => {
    render(editor({ start_time: "2026-01-01T00:00:00Z" }, null));

    expect(screen.getByText("time rate incomplete")).toBeTruthy();
    expect(document.body.textContent).not.toContain("×null");
    expect(document.body.textContent).not.toContain("step null");
  });

  it("labels a partially missing candidate budget as incomplete", () => {
    render(editor({ step_seconds: 1, compression: 1 }, { candidate_limits: { max_pairs_per_tick: 10 } }));

    expect(screen.getByText("candidate budget incomplete")).toBeTruthy();
    expect(document.body.textContent).not.toContain("null / rule");
  });
});
