// Copyright 2024-2026 .chance (dotchance)
// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms under AGPL-3.0 Section 7 are in the NOTICE file.
/** ShortcutHelp marks the live-view shortcuts as suspended in builder mode, so
 *  the help overlay can never claim a key works when the gate suspends it. The
 *  marked set is asserted to equal the table's own suspendedInBuilder partition
 *  (the same table the handler gates on). */

import { afterEach, describe, expect, it } from "vitest";
import { render, screen, cleanup } from "@testing-library/react";
import { ShortcutHelp } from "../ShortcutHelp";
import { KEYBOARD_SHORTCUTS } from "../../hooks/useKeyboard";

afterEach(cleanup);

const suspendedCount = KEYBOARD_SHORTCUTS.filter((s) => s.suspendedInBuilder).length;

describe("ShortcutHelp — suspended-in-builder marking", () => {
  it("marks nothing in a live view", () => {
    render(<ShortcutHelp onClose={() => {}} viewMode="globe" />);
    expect(screen.queryAllByTestId("shortcut-suspended")).toHaveLength(0);
  });

  it("marks exactly the table's suspended entries in builder mode", () => {
    render(<ShortcutHelp onClose={() => {}} viewMode="builder" />);
    expect(screen.getAllByTestId("shortcut-suspended")).toHaveLength(suspendedCount);
    // Each marked row carries the "live view only" tag.
    expect(screen.getAllByText("live view only")).toHaveLength(suspendedCount);
    expect(suspendedCount).toBeGreaterThan(0);
  });
});

describe("ShortcutHelp — About notices", () => {
  it("shows the attribution, the legal notices and the license and source links", () => {
    render(<ShortcutHelp onClose={() => {}} viewMode="globe" />);
    const about = screen.getByTestId("about-notices");
    expect(about.textContent).toContain(
      "NodalArc by .chance (dotchance), https://github.com/dotchance/nodalarc",
    );
    expect(about.textContent).toContain("Copyright 2024-2026 .chance (dotchance)");
    expect(about.textContent).toContain("AGPL-3.0-only");
    expect(about.textContent).toContain("no warranty");
    const links = Object.fromEntries(
      Array.from(about.querySelectorAll("a")).map((a) => [a.textContent, a.getAttribute("href")]),
    );
    expect(links).toEqual({
      "https://github.com/dotchance/nodalarc": "https://github.com/dotchance/nodalarc",
      License: "https://github.com/dotchance/nodalarc/blob/main/LICENSE",
      Notice: "https://github.com/dotchance/nodalarc/blob/main/NOTICE",
      "Source code": "https://github.com/dotchance/nodalarc",
    });
  });
});
