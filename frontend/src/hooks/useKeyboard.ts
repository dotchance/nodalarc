// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
/** Keyboard shortcuts for VF (per VF spec Section 12). */

import { useEffect } from "react";
import { focusSearchTarget } from "../ui/searchFocus";
import type { ViewMode, ColorMode } from "../types";

/** Shortcut reference consumed by the help overlay — keep in sync with the
 *  switch below (they live side by side so edits are visually adjacent).
 *  `suspendedInBuilder` is the M9 suspend/keep partition: a live-session key is
 *  a no-op in builder mode (the builder has no live session to drive); a shared
 *  display/color/label/camera/help key stays live so the builder consumes the
 *  same display state. This table is the SINGLE source of truth — the help
 *  overlay reads it and a test asserts it equals the handler's actual gate. */
export const KEYBOARD_SHORTCUTS: readonly {
  keys: string;
  action: string;
  group: string;
  suspendedInBuilder: boolean;
}[] = [
  { keys: "Space", action: "Pause / resume simulation", group: "Simulation", suspendedInBuilder: true },
  { keys: "Tab", action: "Toggle globe / topology view", group: "Views", suspendedInBuilder: true },
  { keys: "V", action: "Top-down view", group: "Views", suspendedInBuilder: false },
  { keys: "F", action: "Frame selection", group: "Views", suspendedInBuilder: true },
  { keys: "Shift+F", action: "Follow selected node", group: "Views", suspendedInBuilder: true },
  { keys: "Home", action: "Frame whole scene", group: "Views", suspendedInBuilder: false },
  { keys: "Escape", action: "Deselect / close overlay", group: "Views", suspendedInBuilder: true },
  { keys: "L", action: "Toggle ISL links", group: "Display", suspendedInBuilder: false },
  { keys: "G", action: "Toggle ground links", group: "Display", suspendedInBuilder: false },
  { keys: "P", action: "Toggle orbital paths", group: "Display", suspendedInBuilder: false },
  { keys: "T", action: "Toggle satellite trails", group: "Display", suspendedInBuilder: false },
  { keys: "N", action: "Cycle globe surface", group: "Display", suspendedInBuilder: false },
  { keys: "I", action: "Toggle reference frame", group: "Display", suspendedInBuilder: false },
  { keys: ";", action: "Toggle satellite labels", group: "Display", suspendedInBuilder: false },
  { keys: "'", action: "Toggle ground labels", group: "Display", suspendedInBuilder: false },
  { keys: "1", action: "Color by routing area", group: "Color modes", suspendedInBuilder: false },
  { keys: "2", action: "Color by orbital plane", group: "Color modes", suspendedInBuilder: false },
  { keys: "3", action: "Color by orbital regime", group: "Color modes", suspendedInBuilder: false },
  { keys: "]", action: "Toggle detail panel", group: "Panels", suspendedInBuilder: true },
  { keys: "Q", action: "Toggle filter drawer", group: "Panels", suspendedInBuilder: true },
  { keys: "`", action: "Toggle CLI drawer", group: "Panels", suspendedInBuilder: true },
  { keys: "/", action: "Focus log search (when the log window is open)", group: "Panels", suspendedInBuilder: true },
  { keys: "H", action: "Toggle historical mode (experimental)", group: "Simulation", suspendedInBuilder: true },
  { keys: "?", action: "Show this overlay", group: "Panels", suspendedInBuilder: false },
];

/** The keys whose action is a full no-op in builder mode. e.key values (both
 *  letter cases); Escape is handled specially in the switch (its onCloseCatalog
 *  arm is kept but unreachable in builder). MUST match the partition above. */
const SUSPENDED_IN_BUILDER: ReadonlySet<string> = new Set([
  " ",
  "Tab",
  "f",
  "F",
  "]",
  "q",
  "Q",
  "`",
  "/",
  "h",
  "H",
]);

interface KeyboardActions {
  onEscape: () => void;
  onCloseCatalog?: () => void;
  onToggleView: (mode: ViewMode) => void;
  onSetColorMode: (mode: ColorMode) => void;
  onToggleGroundLinks: () => void;
  onToggleIslLinks: () => void;
  onToggleSatPaths: () => void;
  onToggleTrails: () => void;
  onToggleHistorical: () => void;
  onPlayPause: () => void;
  onFollowNode: () => void;
  onFrameSelection: () => void;
  onFrameScene: () => void;
  onTopView: () => void;
  onToggleGlobeMode?: () => void;
  onToggleReferenceFrame?: () => void;
  onToggleCli?: () => void;
  onTogglePanel?: () => void;
  onToggleFilter?: () => void;
  onToggleLabels?: () => void;
  onToggleGsLabels?: () => void;
  onShowHelp?: () => void;
}

export function useKeyboard(actions: KeyboardActions, viewMode: ViewMode): void {
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      // Don't capture when typing in inputs
      if (
        e.target instanceof HTMLInputElement ||
        e.target instanceof HTMLSelectElement ||
        e.target instanceof HTMLTextAreaElement
      )
        return;

      // M9: a live-session key is a full no-op in the builder — RETURN before
      // any preventDefault so native behavior (Tab focus-nav, Space activation)
      // is preserved, not merely the action skipped.
      if (viewMode === "builder" && SUSPENDED_IN_BUILDER.has(e.key)) return;

      switch (e.key) {
        case "Escape":
          if (actions.onCloseCatalog) {
            actions.onCloseCatalog();
          } else if (viewMode !== "builder") {
            // The clearSelection arm is suspended in the builder; the catalog
            // arm above is kept but unreachable there (showCatalog is cleared on
            // entry, so App stops passing onCloseCatalog).
            actions.onEscape();
          }
          break;
        case " ":
          e.preventDefault();
          actions.onPlayPause();
          break;
        case "1":
          actions.onSetColorMode("area");
          break;
        case "2":
          actions.onSetColorMode("plane");
          break;
        case "3":
          actions.onSetColorMode("regime");
          break;
        case "Tab":
          e.preventDefault();
          actions.onToggleView("topology"); // Will toggle in App.tsx
          break;
        case "g":
        case "G":
          actions.onToggleGroundLinks();
          break;
        case "l":
        case "L":
          actions.onToggleIslLinks();
          break;
        case "p":
        case "P":
          actions.onToggleSatPaths();
          break;
        case "f":
        case "F":
          if (e.shiftKey) actions.onFollowNode();
          else actions.onFrameSelection();
          break;
        case "Home":
          actions.onFrameScene();
          break;
        case "t":
        case "T":
          actions.onToggleTrails();
          break;
        case "v":
        case "V":
          actions.onTopView();
          break;
        case "h":
        case "H":
          actions.onToggleHistorical();
          break;
        case "n":
        case "N":
          actions.onToggleGlobeMode?.();
          break;
        case "i":
        case "I":
          actions.onToggleReferenceFrame?.();
          break;
        case "]":
          actions.onTogglePanel?.();
          break;
        case "q":
        case "Q":
          actions.onToggleFilter?.();
          break;
        case ";":
          actions.onToggleLabels?.();
          break;
        case "'":
          actions.onToggleGsLabels?.();
          break;
        case "`":
          actions.onToggleCli?.();
          break;
        case "/":
          if (focusSearchTarget()) e.preventDefault();
          break;
        case "?":
          actions.onShowHelp?.();
          break;
      }
    };

    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [actions, viewMode]);
}
