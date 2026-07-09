// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
/** The floating-window raise stack: ONE bounded stacking mechanism.
 *
 *  The store pins the ordering (register-on-top, raise-to-top, remove-collapses,
 *  bounded ranks); the FloatingWindow pin proves the wiring — a pointerdown
 *  raises the window and its --window-z-bump (the z offset within the zWindow
 *  band) reflects the new rank.
 */
import { describe, it, expect, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, cleanup } from "@testing-library/react";
import {
  addWindow,
  raiseWindow,
  removeWindow,
  _resetWindowStack,
  _windowStackOrder,
} from "../windowStack";
import { FloatingWindow } from "../FloatingWindow";

beforeEach(() => _resetWindowStack());
afterEach(() => {
  cleanup();
  _resetWindowStack();
});

describe("windowStack — the raise stack ordering", () => {
  it("registers each window on top, bottom → top", () => {
    addWindow("a");
    addWindow("b");
    addWindow("c");
    expect(_windowStackOrder()).toEqual(["a", "b", "c"]); // c opened last = on top
  });

  it("registering the same id twice does not duplicate or reorder", () => {
    addWindow("a");
    addWindow("b");
    addWindow("a"); // idempotent
    expect(_windowStackOrder()).toEqual(["a", "b"]);
  });

  it("raise brings a buried window to the top, keeping ranks bounded to [0, count)", () => {
    addWindow("a");
    addWindow("b");
    addWindow("c");
    raiseWindow("a"); // click the bottom window
    expect(_windowStackOrder()).toEqual(["b", "c", "a"]); // a on top, others shifted down
    // The top rank equals count-1 — a raised window never climbs out of the band.
    expect(_windowStackOrder().indexOf("a")).toBe(2);
  });

  it("raise is a no-op for the top window or an unknown id", () => {
    addWindow("a");
    addWindow("b");
    raiseWindow("b"); // already top
    raiseWindow("ghost"); // unknown
    expect(_windowStackOrder()).toEqual(["a", "b"]);
  });

  it("remove collapses the ranks above it", () => {
    addWindow("a");
    addWindow("b");
    addWindow("c");
    removeWindow("a");
    expect(_windowStackOrder()).toEqual(["b", "c"]); // b:1→0, c:2→1
  });
});

describe("FloatingWindow — raise-on-pointerdown wiring", () => {
  function win(raiseId: string) {
    return (
      <FloatingWindow
        raiseId={raiseId}
        title={raiseId}
        onClose={() => {}}
        initial={{ x: 0, y: 0, w: 300, h: 200 }}
      >
        <div>{raiseId}</div>
      </FloatingWindow>
    );
  }

  it("a pointerdown raises the window and its --window-z-bump follows its rank", () => {
    render(
      <>
        {win("w1")}
        {win("w2")}
      </>,
    );
    const dialogs = screen.getAllByRole("dialog");
    const [w1, w2] = dialogs as HTMLElement[];
    // w2 mounted after w1, so it starts on top.
    expect(w1!.style.getPropertyValue("--window-z-bump")).toBe("0");
    expect(w2!.style.getPropertyValue("--window-z-bump")).toBe("1");
    // Click the buried window — it raises above its sibling.
    fireEvent.pointerDown(w1!);
    expect(w1!.style.getPropertyValue("--window-z-bump")).toBe("1"); // now on top
    expect(w2!.style.getPropertyValue("--window-z-bump")).toBe("0");
  });
});
