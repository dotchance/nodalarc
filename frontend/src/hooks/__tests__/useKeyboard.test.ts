// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
import { describe, it, expect, vi, beforeEach } from "vitest";
import { renderHook, act } from "@testing-library/react";
import { useKeyboard } from "../useKeyboard";
import type { ViewMode, ColorMode } from "../../types";

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
    renderHook(() => useKeyboard(actions));
    act(() => fireKey(key));
    expect((actions as Record<string, ReturnType<typeof vi.fn>>)[actionName]).toHaveBeenCalled();
  });

  it("Space calls onPlayPause and preventDefault", () => {
    renderHook(() => useKeyboard(actions));
    const pd = vi.fn();
    act(() => fireKey(" ", { preventDefault: pd }));
    expect(actions.onPlayPause).toHaveBeenCalled();
    expect(pd).toHaveBeenCalled();
  });

  it("Tab calls onToggleView and preventDefault", () => {
    renderHook(() => useKeyboard(actions));
    const pd = vi.fn();
    act(() => fireKey("Tab", { preventDefault: pd }));
    expect(actions.onToggleView).toHaveBeenCalledWith("topology");
    expect(pd).toHaveBeenCalled();
  });

  it("Shift+F follows the selected item instead of framing it", () => {
    renderHook(() => useKeyboard(actions));
    act(() => fireKey("F", { shiftKey: true }));
    expect(actions.onFollowNode).toHaveBeenCalled();
    expect(actions.onFrameSelection).not.toHaveBeenCalled();
  });

  it("1 sets area color mode, 2 sets plane", () => {
    renderHook(() => useKeyboard(actions));
    act(() => fireKey("1"));
    expect(actions.onSetColorMode).toHaveBeenCalledWith("area");
    act(() => fireKey("2"));
    expect(actions.onSetColorMode).toHaveBeenCalledWith("plane");
  });

  it("suppresses shortcuts when input is focused", () => {
    renderHook(() => useKeyboard(actions));
    const input = document.createElement("input");
    act(() => fireKey("t", { target: input }));
    expect(actions.onToggleTrails).not.toHaveBeenCalled();
  });

  it("suppresses shortcuts when textarea is focused", () => {
    renderHook(() => useKeyboard(actions));
    const textarea = document.createElement("textarea");
    act(() => fireKey("t", { target: textarea }));
    expect(actions.onToggleTrails).not.toHaveBeenCalled();
  });

  it("suppresses shortcuts when select is focused", () => {
    renderHook(() => useKeyboard(actions));
    const select = document.createElement("select");
    act(() => fireKey("t", { target: select }));
    expect(actions.onToggleTrails).not.toHaveBeenCalled();
  });

  it("Escape routes to onCloseCatalog when provided", () => {
    const onCloseCatalog = vi.fn();
    renderHook(() => useKeyboard({ ...actions, onCloseCatalog }));
    act(() => fireKey("Escape"));
    expect(onCloseCatalog).toHaveBeenCalled();
    expect(actions.onEscape).not.toHaveBeenCalled();
  });

  it("Escape routes to onEscape when onCloseCatalog is undefined", () => {
    renderHook(() => useKeyboard({ ...actions, onCloseCatalog: undefined }));
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
    renderHook(() => useKeyboard(minimal));
    act(() => fireKey("n")); // onToggleGlobeMode is optional
    act(() => fireKey("i")); // onToggleReferenceFrame is optional
    act(() => fireKey("]")); // onTogglePanel is optional
  });
});
