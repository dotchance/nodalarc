/** Keyboard shortcuts for VF. */

import { useEffect } from "react";
import type { ViewMode, ColorMode } from "../types";

interface KeyboardActions {
  onEscape: () => void;
  onToggleView: (mode: ViewMode) => void;
  onToggleColorMode: (mode: ColorMode) => void;
  onToggleGroundTracks: () => void;
  onToggleAllLinks: () => void;
  onToggleHistorical: () => void;
  onPlayPause: () => void;
}

export function useKeyboard(actions: KeyboardActions): void {
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      // Don't capture when typing in inputs
      if (e.target instanceof HTMLInputElement || e.target instanceof HTMLSelectElement) return;

      switch (e.key) {
        case "Escape":
          actions.onEscape();
          break;
        case "1":
          actions.onToggleView("globe");
          break;
        case "2":
          actions.onToggleView("topology");
          break;
        case "3":
          actions.onToggleView("split");
          break;
        case "c":
        case "C":
          actions.onToggleColorMode(e.shiftKey ? "plane" : "area");
          break;
        case "g":
        case "G":
          actions.onToggleGroundTracks();
          break;
        case "l":
        case "L":
          actions.onToggleAllLinks();
          break;
        case "h":
        case "H":
          actions.onToggleHistorical();
          break;
        case " ":
          e.preventDefault();
          actions.onPlayPause();
          break;
      }
    };

    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [actions]);
}
