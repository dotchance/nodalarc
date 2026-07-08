// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { renderHook, act, cleanup } from "@testing-library/react";
import { useKeyboard, KEYBOARD_SHORTCUTS } from "../useKeyboard";
import { focusSearchTarget } from "../../ui/searchFocus";
import type { ViewMode, ColorMode } from "../../types";

vi.mock("../../ui/searchFocus", () => ({ focusSearchTarget: vi.fn(() => true) }));

// Each hook registers a window keydown listener; unmount between tests so a
// prior test's live-mode listener never fires on a later test's event.
afterEach(cleanup);

function makeActions() {
  return {
    onEscape: vi.fn(),
    onToggleView: vi.fn<(mode: ViewMode) => void>(),
    onSetColorMode: vi.fn<(mode: ColorMode) => void>(),
    onToggleGroundLinks: vi.fn(),
    onToggleIslLinks: vi.fn(),
    onToggleSatPaths: vi.fn(),
    onToggleTrails: vi.fn(),
    onToggleHistorical: vi.fn(),
    onPlayPause: vi.fn(),
    onFollowNode: vi.fn(),
    onFrameSelection: vi.fn(),
    onFrameScene: vi.fn(),
    onTopView: vi.fn(),
    onToggleGlobeMode: vi.fn(),
    onToggleReferenceFrame: vi.fn(),
    onToggleCli: vi.fn(),
    onTogglePanel: vi.fn(),
    onToggleFilter: vi.fn(),
    onToggleLabels: vi.fn(),
    onToggleGsLabels: vi.fn(),
    onShowHelp: vi.fn(),
  };
}

function fireKey(
  key: string,
  opts?: { target?: EventTarget; preventDefault?: () => void; shiftKey?: boolean },
) {
  const event = new KeyboardEvent("keydown", { key, bubbles: true, shiftKey: opts?.shiftKey });
  if (opts?.preventDefault) {
    Object.defineProperty(event, "preventDefault", { value: opts.preventDefault });
  }
  if (opts?.target) {
    Object.defineProperty(event, "target", { value: opts.target });
  }
  window.dispatchEvent(event);
}

describe("useKeyboard", () => {
  let actions: ReturnType<typeof makeActions>;

  beforeEach(() => {
    actions = makeActions();
  });

  it.each([
    ["t", "onToggleTrails"],
    ["T", "onToggleTrails"],
    ["v", "onTopView"],
    ["V", "onTopView"],
    ["l", "onToggleIslLinks"],
    ["L", "onToggleIslLinks"],
    ["g", "onToggleGroundLinks"],
    ["G", "onToggleGroundLinks"],
    ["p", "onToggleSatPaths"],
    ["P", "onToggleSatPaths"],
    ["h", "onToggleHistorical"],
    ["H", "onToggleHistorical"],
    ["f", "onFrameSelection"],
    ["F", "onFrameSelection"],
    ["Home", "onFrameScene"],
    ["n", "onToggleGlobeMode"],
    ["i", "onToggleReferenceFrame"],
    ["]", "onTogglePanel"],
    ["q", "onToggleFilter"],
    [";", "onToggleLabels"],
    ["'", "onToggleGsLabels"],
  ])("key '%s' calls %s", (key, actionName) => {
    renderHook(() => useKeyboard(actions, "globe"));
    act(() => fireKey(key));
    expect((actions as Record<string, ReturnType<typeof vi.fn>>)[actionName]).toHaveBeenCalled();
  });

  it("Space calls onPlayPause and preventDefault", () => {
    renderHook(() => useKeyboard(actions, "globe"));
    const pd = vi.fn();
    act(() => fireKey(" ", { preventDefault: pd }));
    expect(actions.onPlayPause).toHaveBeenCalled();
    expect(pd).toHaveBeenCalled();
  });

  it("Tab calls onToggleView and preventDefault", () => {
    renderHook(() => useKeyboard(actions, "globe"));
    const pd = vi.fn();
    act(() => fireKey("Tab", { preventDefault: pd }));
    expect(actions.onToggleView).toHaveBeenCalledWith("topology");
    expect(pd).toHaveBeenCalled();
  });

  it("Shift+F follows the selected item instead of framing it", () => {
    renderHook(() => useKeyboard(actions, "globe"));
    act(() => fireKey("F", { shiftKey: true }));
    expect(actions.onFollowNode).toHaveBeenCalled();
    expect(actions.onFrameSelection).not.toHaveBeenCalled();
  });

  it("1 sets area color mode, 2 sets plane", () => {
    renderHook(() => useKeyboard(actions, "globe"));
    act(() => fireKey("1"));
    expect(actions.onSetColorMode).toHaveBeenCalledWith("area");
    act(() => fireKey("2"));
    expect(actions.onSetColorMode).toHaveBeenCalledWith("plane");
  });

  it("suppresses shortcuts when input is focused", () => {
    renderHook(() => useKeyboard(actions, "globe"));
    const input = document.createElement("input");
    act(() => fireKey("t", { target: input }));
    expect(actions.onToggleTrails).not.toHaveBeenCalled();
  });

  it("suppresses shortcuts when textarea is focused", () => {
    renderHook(() => useKeyboard(actions, "globe"));
    const textarea = document.createElement("textarea");
    act(() => fireKey("t", { target: textarea }));
    expect(actions.onToggleTrails).not.toHaveBeenCalled();
  });

  it("suppresses shortcuts when select is focused", () => {
    renderHook(() => useKeyboard(actions, "globe"));
    const select = document.createElement("select");
    act(() => fireKey("t", { target: select }));
    expect(actions.onToggleTrails).not.toHaveBeenCalled();
  });

  it("Escape routes to onCloseCatalog when provided", () => {
    const onCloseCatalog = vi.fn();
    renderHook(() => useKeyboard({ ...actions, onCloseCatalog }, "globe"));
    act(() => fireKey("Escape"));
    expect(onCloseCatalog).toHaveBeenCalled();
    expect(actions.onEscape).not.toHaveBeenCalled();
  });

  it("Escape routes to onEscape when onCloseCatalog is undefined", () => {
    renderHook(() => useKeyboard({ ...actions, onCloseCatalog: undefined }, "globe"));
    act(() => fireKey("Escape"));
    expect(actions.onEscape).toHaveBeenCalled();
  });

  it("optional actions don't crash when undefined", () => {
    const minimal = {
      onEscape: vi.fn(),
      onToggleView: vi.fn<(mode: ViewMode) => void>(),
      onSetColorMode: vi.fn<(mode: ColorMode) => void>(),
      onToggleGroundLinks: vi.fn(),
      onToggleIslLinks: vi.fn(),
      onToggleSatPaths: vi.fn(),
      onToggleTrails: vi.fn(),
      onToggleHistorical: vi.fn(),
      onPlayPause: vi.fn(),
      onFollowNode: vi.fn(),
      onFrameSelection: vi.fn(),
      onFrameScene: vi.fn(),
      onTopView: vi.fn(),
    };
    renderHook(() => useKeyboard(minimal, "globe"));
    act(() => fireKey("n")); // onToggleGlobeMode is optional
    act(() => fireKey("i")); // onToggleReferenceFrame is optional
    act(() => fireKey("]")); // onTogglePanel is optional
  });
});

describe("useKeyboard — builder mode suspends live-session keys (M9)", () => {
  // How each KEYBOARD_SHORTCUTS row is dispatched and which action it drives.
  // "/" (a DOM search-focus, not an action) and "Escape" (two-arm) are covered
  // by dedicated tests below.
  const DISPATCH: Record<
    string,
    { key: string; shiftKey?: boolean; action: keyof ReturnType<typeof makeActions> }
  > = {
    Space: { key: " ", action: "onPlayPause" },
    Tab: { key: "Tab", action: "onToggleView" },
    V: { key: "v", action: "onTopView" },
    F: { key: "f", action: "onFrameSelection" },
    "Shift+F": { key: "F", shiftKey: true, action: "onFollowNode" },
    Home: { key: "Home", action: "onFrameScene" },
    L: { key: "l", action: "onToggleIslLinks" },
    G: { key: "g", action: "onToggleGroundLinks" },
    P: { key: "p", action: "onToggleSatPaths" },
    T: { key: "t", action: "onToggleTrails" },
    N: { key: "n", action: "onToggleGlobeMode" },
    I: { key: "i", action: "onToggleReferenceFrame" },
    ";": { key: ";", action: "onToggleLabels" },
    "'": { key: "'", action: "onToggleGsLabels" },
    "1": { key: "1", action: "onSetColorMode" },
    "2": { key: "2", action: "onSetColorMode" },
    "3": { key: "3", action: "onSetColorMode" },
    "]": { key: "]", action: "onTogglePanel" },
    Q: { key: "q", action: "onToggleFilter" },
    "`": { key: "`", action: "onToggleCli" },
    H: { key: "h", action: "onToggleHistorical" },
    "?": { key: "?", action: "onShowHelp" },
  };

  it("the help table's partition IS the handler's actual gate, both directions", () => {
    // Iterating the table against the live behavior means the table can never
    // drift from the gate: every claim in the help overlay is asserted here.
    for (const entry of KEYBOARD_SHORTCUTS) {
      if (entry.keys === "/" || entry.keys === "Escape") continue;
      const spec = DISPATCH[entry.keys]!;

      const builder = makeActions();
      const pd = vi.fn();
      const b = renderHook(() => useKeyboard(builder, "builder"));
      act(() => fireKey(spec.key, { shiftKey: spec.shiftKey, preventDefault: pd }));
      b.unmount();
      if (entry.suspendedInBuilder) {
        expect(builder[spec.action], `${entry.keys} suspended in builder`).not.toHaveBeenCalled();
        // A suspended key returns BEFORE preventDefault — native behavior intact.
        expect(pd, `${entry.keys} preserves native default`).not.toHaveBeenCalled();
      } else {
        expect(builder[spec.action], `${entry.keys} kept in builder`).toHaveBeenCalled();
      }

      // Live mode never gates — the action always fires, either direction.
      const live = makeActions();
      const l = renderHook(() => useKeyboard(live, "globe"));
      act(() => fireKey(spec.key, { shiftKey: spec.shiftKey, preventDefault: vi.fn() }));
      l.unmount();
      expect(live[spec.action], `${entry.keys} live`).toHaveBeenCalled();
    }
  });

  it("Escape's clear-selection arm is suspended in builder; native default preserved", () => {
    const actions = makeActions();
    const pd = vi.fn();
    renderHook(() => useKeyboard({ ...actions, onCloseCatalog: undefined }, "builder"));
    act(() => fireKey("Escape", { preventDefault: pd }));
    expect(actions.onEscape).not.toHaveBeenCalled();
    expect(pd).not.toHaveBeenCalled();
    // Live mode still deselects.
    const live = makeActions();
    renderHook(() => useKeyboard({ ...live, onCloseCatalog: undefined }, "globe"));
    act(() => fireKey("Escape"));
    expect(live.onEscape).toHaveBeenCalled();
  });

  it("'/' log-search focus is suspended in builder; live it focuses and prevents default", () => {
    const search = vi.mocked(focusSearchTarget);
    search.mockClear();
    const pd = vi.fn();
    renderHook(() => useKeyboard(makeActions(), "builder"));
    act(() => fireKey("/", { preventDefault: pd }));
    expect(search).not.toHaveBeenCalled();
    expect(pd).not.toHaveBeenCalled();

    const pdLive = vi.fn();
    renderHook(() => useKeyboard(makeActions(), "globe"));
    act(() => fireKey("/", { preventDefault: pdLive }));
    expect(search).toHaveBeenCalled();
    expect(pdLive).toHaveBeenCalled();
  });
});
