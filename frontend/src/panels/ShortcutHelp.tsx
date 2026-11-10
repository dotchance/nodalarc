// Copyright 2024-2026 .chance (dotchance)
// SPDX-License-Identifier: AGPL-3.0-only
/**
 * Keyboard-shortcut reference + About overlay. Rendered from the shortcut map
 * in useKeyboard.ts so the help can't drift from the bindings. Opened with
 * `?` or the top-bar help button.
 */

import { useEffect } from "react";
import { KEYBOARD_SHORTCUTS } from "../hooks/useKeyboard";
import { activeThemeName, setTheme, THEMES, type ThemeName } from "../styles/tokens";
import { Button } from "../ui/Button";
import type { ViewMode } from "../types";

const SOURCE_URL = "https://github.com/dotchance/nodalarc";

const THEME_LABELS: Record<ThemeName, string> = {
  "mission-light": "Mission Light",
  "noc-dark": "NOC Dark",
};

interface ShortcutHelpProps {
  onClose: () => void;
  viewMode: ViewMode;
}

export function ShortcutHelp({ onClose, viewMode }: ShortcutHelpProps) {
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
        {viewMode === "builder" && (
          <p className="help-note">
            In the session builder, live-view shortcuts are suspended — marked below.
          </p>
        )}
        <div className="help-groups">
          {groups.map((group) => (
            <section key={group} className="help-group">
              <h3>{group}</h3>
              {KEYBOARD_SHORTCUTS.filter((s) => s.group === group).map((s) => {
                const suspended = viewMode === "builder" && s.suspendedInBuilder;
                return (
                  <div
                    key={s.keys + s.action}
                    className={`help-row${suspended ? " help-row--suspended" : ""}`}
                    data-testid={suspended ? "shortcut-suspended" : "shortcut-active"}
                  >
                    <kbd>{s.keys}</kbd>
                    <span>{s.action}</span>
                    {suspended && <span className="help-row-tag">live view only</span>}
                  </div>
                );
              })}
            </section>
          ))}
        </div>
        <div className="help-theme">
          <h3>Theme</h3>
          <div className="help-theme-buttons">
            {(Object.keys(THEMES) as ThemeName[]).map((name) => (
              <Button key={name} active={name === activeThemeName()} onClick={() => setTheme(name)}>
                {THEME_LABELS[name]}
              </Button>
            ))}
          </div>
          <p className="help-note">Switching reloads the console so every surface repaints consistently.</p>
        </div>
        {/* The attribution and the source link are protected by the first
            additional term in NOTICE; the copyright, warranty and license
            lines are the Appropriate Legal Notices of AGPL-3.0 Section 5(d). */}
        <footer className="help-about" data-testid="about-notices">
          <p>
            <strong>NodalArc</strong> by .chance (dotchance). High-fidelity orbital network
            emulation.
          </p>
          <p>
            Copyright 2024-2026 .chance (dotchance). NodalArc is free software: you may
            redistribute and modify it under the GNU Affero General Public License, version 3
            only (AGPL-3.0-only), with the additional terms in its NOTICE file. It comes with no
            warranty.
          </p>
          <p>
            <a href={`${SOURCE_URL}/blob/main/LICENSE`} target="_blank" rel="noreferrer">
              License
            </a>
            {" · "}
            <a href={`${SOURCE_URL}/blob/main/NOTICE`} target="_blank" rel="noreferrer">
              Notice
            </a>
            {" · "}
            <a href={SOURCE_URL} target="_blank" rel="noreferrer">
              Source code
            </a>
          </p>
        </footer>
      </div>
    </div>
  );
}
