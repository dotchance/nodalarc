// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
/**
 * Keyboard-shortcut reference + About overlay. Rendered from the shortcut map
 * in useKeyboard.ts so the help can't drift from the bindings. Opened with
 * `?` or the top-bar help button.
 */

import { useEffect } from "react";
import { KEYBOARD_SHORTCUTS } from "../hooks/useKeyboard";
import { Button } from "../ui/Button";

interface ShortcutHelpProps {
  onClose: () => void;
}

export function ShortcutHelp({ onClose }: ShortcutHelpProps) {
  useEffect(() => {
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.stopPropagation();
        onClose();
      }
    };
    // Capture phase so this closes before the app-level Escape handling runs.
    window.addEventListener("keydown", onKeyDown, true);
    return () => window.removeEventListener("keydown", onKeyDown, true);
  }, [onClose]);

  const groups = [...new Set(KEYBOARD_SHORTCUTS.map((s) => s.group))];

  return (
    <div className="help-overlay" onClick={onClose}>
      <div
        className="help-panel"
        role="dialog"
        aria-label="Keyboard shortcuts and about"
        onClick={(e) => e.stopPropagation()}
      >
        <header className="help-head">
          <h2>Keyboard shortcuts</h2>
          <Button onClick={onClose}>Close</Button>
        </header>
        <p className="help-note">Shortcuts are inactive while typing in a terminal or input field.</p>
        <div className="help-groups">
          {groups.map((group) => (
            <section key={group} className="help-group">
              <h3>{group}</h3>
              {KEYBOARD_SHORTCUTS.filter((s) => s.group === group).map((s) => (
                <div key={s.keys + s.action} className="help-row">
                  <kbd>{s.keys}</kbd>
                  <span>{s.action}</span>
                </div>
              ))}
            </section>
          ))}
        </div>
        <footer className="help-about">
          <strong>NodalArc</strong> — high-fidelity orbital network emulation.{" "}
          <span>
            by{" "}
            <a href="https://github.com/dotchance/nodalarc" target="_blank" rel="noreferrer">
              .chance
            </a>{" "}
            · Apache-2.0
          </span>
        </footer>
      </div>
    </div>
  );
}
