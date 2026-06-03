// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
/** Keyboard shortcuts for VF (per VF spec Section 12). */

import { useEffect } from "react";
import type { ViewMode, ColorMode } from "../types";

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
        case "/":
          e.preventDefault();
          // Focus event filter input if exists
          document.querySelector<HTMLInputElement>(".event-filter-input")?.focus();
          break;
      }
    };

    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [actions]);
}
