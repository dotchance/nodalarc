// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
/**
 * Key-value detail rows — the inspector's bread and butter. Labels render in
 * the UI face; values default to the data face (IBM Plex Mono) because they
 * are usually ids, addresses, and measurements. `state` carries semantic
 * emphasis (failed = the red treatment; dead = struck-through dead-knob).
 */

import type { ReactNode } from "react";

interface KeyValueRowProps {
  label: ReactNode;
  children: ReactNode;
  /** Semantic value treatment. */
  state?: "default" | "ok" | "failed" | "dead" | "dim";
  /** Set false for prose values that should use the UI face. */
  mono?: boolean;
  title?: string;
}

export function KeyValueRow({ label, children, state = "default", mono = true, title }: KeyValueRowProps) {
  return (
    <div className="ui-kv" title={title}>
      <span className="ui-kv-label">{label}</span>
      <span className={`ui-kv-value ui-kv-value--${state}${mono ? " ui-kv-value--mono" : ""}`}>
        {children}
      </span>
    </div>
  );
}

/** Titled group of rows with the quiet section header used across panels. */
export function DetailSection({ title, actions, children }: { title: ReactNode; actions?: ReactNode; children: ReactNode }) {
  return (
    <section className="ui-detail-section">
      <header className="ui-detail-section-head">
        <h3>{title}</h3>
        {actions}
      </header>
      {children}
    </section>
  );
}
