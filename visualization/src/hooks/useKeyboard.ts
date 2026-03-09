/** Keyboard shortcuts for VF (per VF spec Section 12). */

import { useEffect } from "react";
import type { ViewMode, ColorMode } from "../types";

interface KeyboardActions {
  onEscape: () => void;
  onCloseCatalog?: () => void;
  onToggleView: (mode: ViewMode) => void;
  onSetColorMode: (mode: ColorMode) => void;
  onToggleGroundTracks: () => void;
  onToggleAllLinks: () => void;
  onToggleHistorical: () => void;
  onPlayPause: () => void;
  onFollowNode: () => void;
  onTopView: () => void;
  onToggleCli?: () => void;
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
          actions.onToggleGroundTracks();
          break;
        case "l":
        case "L":
          actions.onToggleAllLinks();
          break;
        case "f":
        case "F":
          actions.onFollowNode();
          break;
        case "t":
        case "T":
          actions.onTopView();
          break;
        case "h":
        case "H":
          actions.onToggleHistorical();
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
