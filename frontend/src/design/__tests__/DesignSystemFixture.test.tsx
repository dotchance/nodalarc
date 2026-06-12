// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
import { describe, it, expect, beforeAll, afterEach } from "vitest";
import { render, screen, cleanup } from "@testing-library/react";
import { applyTheme } from "../../styles/tokens";
import { DesignSystemFixture } from "../DesignSystemFixture";

beforeAll(() => {
  applyTheme();
});

afterEach(cleanup);

describe("DesignSystemFixture", () => {
  it("renders every section of the review surface", () => {
    render(<DesignSystemFixture />);
    for (const heading of [
      "Surfaces",
      "Text + accents",
      "Status slots",
      "Decision families (the color law)",
      "Taxonomy — regime / medium / relation",
      "Link language — relation × medium × state",
      "Typography",
      "Z ladder",
    ]) {
      expect(screen.getByText(heading)).toBeTruthy();
    }
  });

  it("shows all six decision families with their operator labels", () => {
    render(<DesignSystemFixture />);
    for (const label of [
      "Connected",
      "Expected no-link",
      "Eligible (not selected)",
      "In flight",
      "Faulted",
      "Unknown",
    ]) {
      expect(screen.getAllByText(label).length).toBeGreaterThan(0);
    }
  });

  it("renders the full relation × state matrix including forward vocabulary", () => {
    const { container } = render(<DesignSystemFixture />);
    for (const state of ["active", "candidate", "degraded", "faulted", "unsupported"]) {
      expect(
        container.querySelectorAll(`.fx-link.state-${state}`).length,
        `state '${state}' must appear in the matrix`,
      ).toBe(6);
    }
  });
});
