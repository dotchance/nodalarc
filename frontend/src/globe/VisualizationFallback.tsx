// Copyright 2024-2026 .chance (dotchance)
// Licensed under the NodalArc Source Available License 1.0. See LICENSE file.

interface VisualizationFallbackProps {
  message: string;
}

export function VisualizationFallback({ message }: VisualizationFallbackProps) {
  return (
    <div className="visualization-fallback">
      <div className="visualization-fallback-box">
        <h2>Visualization Unavailable</h2>
        <p>{message}</p>
        <p>The session catalog and wizard are still available.</p>
      </div>
    </div>
  );
}
