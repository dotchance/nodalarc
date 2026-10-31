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
const { newWorkspace } = await import("./fixtures/workspaceFixtures");

beforeEach(() => resetCatalogStores());
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("truthful incomplete session summaries", () => {
  it("never renders null time values as an authored rate", () => {
    const workspace = newWorkspace("incomplete-time");
    workspace.step_seconds = null;
    workspace.compression = null;
    render(<SessionEditor workspace={workspace} onUpdate={() => {}} />);

    expect(screen.getByText("time rate incomplete")).toBeTruthy();
    expect(document.body.textContent).not.toContain("×null");
    expect(document.body.textContent).not.toContain("step null");
  });

  it("labels a partially missing candidate budget as incomplete", () => {
    const workspace = newWorkspace("incomplete-budget");
    workspace.max_pairs_per_rule = null;
    render(<SessionEditor workspace={workspace} onUpdate={() => {}} />);

    expect(screen.getByText("candidate budget incomplete")).toBeTruthy();
    expect(document.body.textContent).not.toContain("null / rule");
  });
});
