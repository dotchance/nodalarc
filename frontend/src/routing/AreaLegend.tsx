// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
/** The area legend of the colored IS-IS or OSPF instance, with its instance picker. */

import { hexToCSS } from "../config";
import { instanceLabel, type AreaColoring } from "./instances";

interface AreaLegendProps {
  coloring: AreaColoring;
  onSelectInstance: (domainId: string) => void;
}

export function AreaLegend({ coloring, onSelectInstance }: AreaLegendProps) {
  return (
    <div className="area-legend" aria-label="Area colors">
      {coloring.instance !== null && (
        <select
          className="area-legend-instance"
          aria-label="Instance colored by area"
          value={coloring.instance.domainId}
          onChange={(event) => onSelectInstance(event.target.value)}
        >
          {coloring.instances.map((instance) => (
            <option key={instance.domainId} value={instance.domainId}>
              {instanceLabel(instance)}
            </option>
          ))}
        </select>
      )}
      {coloring.legend.map((entry) => (
        <div className="area-legend-row" key={entry.label}>
          <span className="area-legend-swatch" style={{ background: hexToCSS(entry.color) }} />
          <span>{entry.label}</span>
        </div>
      ))}
    </div>
  );
}
