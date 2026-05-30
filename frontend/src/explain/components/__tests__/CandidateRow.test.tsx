// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
/** Render contract for the shared candidate row. */

import { afterEach, describe, it, expect } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";

afterEach(cleanup);
import { CandidateRow } from "../CandidateRow";

describe("CandidateRow", () => {
  it("renders the node, label, and detail, and fires onClick", () => {
    let clicked = false;
    render(
      <CandidateRow
        node="sat-P00S02"
        family="expected_no_link"
        label="elevation_below_min"
        detail="14 deg el"
        onClick={() => {
          clicked = true;
        }}
      />,
    );
    expect(screen.getByText("sat-P00S02")).toBeTruthy();
    expect(screen.getByText("elevation_below_min")).toBeTruthy();
    expect(screen.getByText("14 deg el")).toBeTruthy();
    fireEvent.click(screen.getByText("sat-P00S02"));
    expect(clicked).toBe(true);
  });

  it("omits the detail span when no detail is given", () => {
    render(<CandidateRow node="gs-denver" family="connected" label="connected" onClick={() => {}} />);
    expect(screen.getByText("gs-denver")).toBeTruthy();
    expect(screen.getByText("connected")).toBeTruthy();
  });
});
