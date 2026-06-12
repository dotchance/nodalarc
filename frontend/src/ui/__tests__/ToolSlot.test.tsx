// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, act, cleanup } from "@testing-library/react";
import { ToolSlot, type ToolVariant } from "../ToolSlot";

const VARIANTS: readonly ToolVariant<"a" | "b" | "c">[] = [
  { value: "a", label: "Alpha", icon: "globe", shortcut: "1" },
  { value: "b", label: "Beta", icon: "network", shortcut: "2" },
  { value: "c", label: "Gamma", icon: "map" },
];

beforeEach(() => {
  vi.useFakeTimers();
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

function renderSlot(onSelect = vi.fn(), active: "a" | "b" | "c" = "a") {
  render(<ToolSlot label="Test slot" variants={VARIANTS} active={active} onSelect={onSelect} />);
  return { onSelect, face: screen.getByTitle(/Test slot/) };
}

describe("ToolSlot", () => {
  it("quick click advances to the next variant (release before the hold fires)", () => {
    const { onSelect, face } = renderSlot();
    fireEvent.pointerDown(face, { button: 0 });
    fireEvent.pointerUp(face, { button: 0 });
    expect(onSelect).toHaveBeenCalledExactlyOnceWith("b");
  });

  it("quick click wraps from the last variant to the first", () => {
    const { onSelect, face } = renderSlot(vi.fn(), "c");
    fireEvent.pointerDown(face, { button: 0 });
    fireEvent.pointerUp(face, { button: 0 });
    expect(onSelect).toHaveBeenCalledExactlyOnceWith("a");
  });

  it("holding past the threshold opens the flyout instead of cycling", () => {
    const { onSelect, face } = renderSlot();
    fireEvent.pointerDown(face, { button: 0 });
    act(() => {
      vi.advanceTimersByTime(350);
    });
    expect(screen.getByRole("menu")).toBeTruthy();
    expect(screen.getByText("Beta")).toBeTruthy();
    expect(onSelect).not.toHaveBeenCalled();
  });

  it("right-click opens the flyout in click-select mode and a row click commits", () => {
    const { onSelect, face } = renderSlot();
    fireEvent.contextMenu(face);
    const row = screen.getByText("Gamma").closest("button")!;
    fireEvent.click(row);
    expect(onSelect).toHaveBeenCalledExactlyOnceWith("c");
    expect(screen.queryByRole("menu")).toBeNull();
  });

  it("marks the active variant with menuitemradio checked state", () => {
    const { face } = renderSlot(vi.fn(), "b");
    fireEvent.contextMenu(face);
    const rows = screen.getAllByRole("menuitemradio");
    const checked = rows.filter((r) => r.getAttribute("aria-checked") === "true");
    expect(checked).toHaveLength(1);
    expect(checked[0]!.textContent).toContain("Beta");
  });

  it("Escape closes a click-mode flyout without selecting", () => {
    const { onSelect, face } = renderSlot();
    fireEvent.contextMenu(face);
    expect(screen.getByRole("menu")).toBeTruthy();
    fireEvent.keyDown(document, { key: "Escape" });
    expect(screen.queryByRole("menu")).toBeNull();
    expect(onSelect).not.toHaveBeenCalled();
  });

  it("shows shortcut keys in flyout rows", () => {
    const { face } = renderSlot();
    fireEvent.contextMenu(face);
    expect(screen.getByText("1")).toBeTruthy();
    expect(screen.getByText("2")).toBeTruthy();
  });
});
