// Copyright 2024-2026 .chance (dotchance)
// SPDX-License-Identifier: AGPL-3.0-only

interface VisualizationFailureProps {
  message: string;
}

export function VisualizationFailure({ message }: VisualizationFailureProps) {
  return (
    <div className="visualization-failure">
      <div className="visualization-failure-box">
        <h2>Visualization Failed</h2>
        <p>{message}</p>
      </div>
    </div>
  );
}
