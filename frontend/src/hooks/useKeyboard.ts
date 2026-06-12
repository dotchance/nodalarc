// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
/** Keyboard shortcuts for VF (per VF spec Section 12). */

import { useEffect } from "react";
import { focusSearchTarget } from "../ui/searchFocus";
import type { ViewMode, ColorMode } from "../types";

/** Shortcut reference consumed by the help overlay — keep in sync with the
 *  switch below (they live side by side so edits are visually adjacent). */
export const KEYBOARD_SHORTCUTS: readonly { keys: string; action: string; group: string }[] = [
  { keys: "Space", action: "Pause / resume simulation", group: "Simulation" },
  { keys: "Tab", action: "Toggle globe / topology view", group: "Views" },
  { keys: "V", action: "Top-down view", group: "Views" },
  { keys: "F", action: "Frame selection", group: "Views" },
  { keys: "Shift+F", action: "Follow selected node", group: "Views" },
  { keys: "Home", action: "Frame whole scene", group: "Views" },
  { keys: "Escape", action: "Deselect / close overlay", group: "Views" },
  { keys: "L", action: "Toggle ISL links", group: "Display" },
  { keys: "G", action: "Toggle ground links", group: "Display" },
  { keys: "P", action: "Toggle orbital paths", group: "Display" },
  { keys: "T", action: "Toggle satellite trails", group: "Display" },
  { keys: "N", action: "Cycle globe surface", group: "Display" },
  { keys: "I", action: "Toggle reference frame", group: "Display" },
  { keys: ";", action: "Toggle satellite labels", group: "Display" },
  { keys: "'", action: "Toggle ground labels", group: "Display" },
  { keys: "1", action: "Color by routing area", group: "Color modes" },
  { keys: "2", action: "Color by orbital plane", group: "Color modes" },
  { keys: "3", action: "Color by orbital regime", group: "Color modes" },
  { keys: "]", action: "Toggle detail panel", group: "Panels" },
  { keys: "Q", action: "Toggle filter drawer", group: "Panels" },
  { keys: "`", action: "Toggle CLI drawer", group: "Panels" },
  { keys: "/", action: "Focus log search (when the log window is open)", group: "Panels" },
  { keys: "H", action: "Toggle historical mode (experimental)", group: "Simulation" },
  { keys: "?", action: "Show this overlay", group: "Panels" },
];

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

export function useKeyboard(actions: KeyboardActions): void {
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      // Don't capture when typing in inputs
      if (
        e.target instanceof HTMLInputElement ||
        e.target instanceof HTMLSelectElement ||
        e.target instanceof HTMLTextAreaElement
      )
        return;

      switch (e.key) {
        case "Escape":
          if (actions.onCloseCatalog) {
            actions.onCloseCatalog();
          } else {
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
  }, [actions]);
}
