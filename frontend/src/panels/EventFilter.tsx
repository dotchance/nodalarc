// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
/** Event filter toggles. */

interface EventFilterProps {
  filters: Record<string, boolean>;
  onToggle: (key: string) => void;
}

const FILTER_LABELS: Record<string, string> = {
  link_up: "Link Up",
  link_down: "Link Down",
  adjacency: "Adj",
  spf: "SPF",
  convergence: "Conv",
  inject: "Inject",
};

export function EventFilter({ filters, onToggle }: EventFilterProps) {
  return (
    <div className="event-filter-bar">
      {Object.entries(FILTER_LABELS).map(([key, label]) => (
        <button
          key={key}
          className={`filter-toggle ${filters[key] ? "filter-toggle--active" : ""}`}
          onClick={() => onToggle(key)}
        >
          {label}
        </button>
      ))}
    </div>
  );
}
