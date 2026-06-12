// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
/**
 * Tab strip primitive (inspector tabs, CLI node tabs, site-member tabs).
 * Controlled; proper tablist semantics with arrow-key navigation. Closable
 * tabs (CLI sessions) render an inline close affordance.
 */

import type { KeyboardEvent } from "react";
import { Icon } from "./icons/Icon";
import { StatusDot, type StatusTone } from "./Badge";

export interface TabItem<K extends string = string> {
  key: K;
  label: string;
  closable?: boolean;
  /** Optional status light before the label (e.g. terminal connection state). */
  tone?: StatusTone;
}

interface TabsProps<K extends string> {
  tabs: readonly TabItem<K>[];
  active: K;
  onSelect: (key: K) => void;
  onClose?: (key: K) => void;
  label: string;
}

export function Tabs<K extends string>({ tabs, active, onSelect, onClose, label }: TabsProps<K>) {
  const onKeyDown = (e: KeyboardEvent) => {
    if (e.key !== "ArrowLeft" && e.key !== "ArrowRight") return;
    const idx = tabs.findIndex((t) => t.key === active);
    if (idx < 0) return;
    const next = tabs[(idx + (e.key === "ArrowRight" ? 1 : tabs.length - 1)) % tabs.length];
    if (next) onSelect(next.key);
    e.preventDefault();
  };

  return (
    <div className="ui-tabs" role="tablist" aria-label={label} onKeyDown={onKeyDown}>
      {tabs.map((tab) => (
        <span key={tab.key} className={`ui-tab${tab.key === active ? " ui-tab--active" : ""}`}>
          <button
            role="tab"
            aria-selected={tab.key === active}
            tabIndex={tab.key === active ? 0 : -1}
            className="ui-tab-btn"
            onClick={() => onSelect(tab.key)}
          >
            {tab.tone && <StatusDot tone={tab.tone} />}
            {tab.label}
          </button>
          {tab.closable && onClose && (
            <button
              className="ui-tab-close"
              aria-label={`Close ${tab.label}`}
              title={`Close ${tab.label}`}
              onClick={(e) => {
                e.stopPropagation();
                onClose(tab.key);
              }}
            >
              <Icon name="x" size={11} />
            </button>
          )}
        </span>
      ))}
    </div>
  );
}
