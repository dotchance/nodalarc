// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
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
