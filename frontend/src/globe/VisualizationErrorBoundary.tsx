// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
import { Component, type ReactNode } from "react";
import { VisualizationFailure } from "./VisualizationFailure";

interface VisualizationErrorBoundaryProps {
  children: ReactNode;
  onError: (message: string) => void;
}

interface VisualizationErrorBoundaryState {
  message: string | null;
}

function formatVisualizationError(error: unknown): string {
  const detail = error instanceof Error ? error.message : String(error);
  return `Visualization failed: ${detail}`;
}

export class VisualizationErrorBoundary extends Component<
  VisualizationErrorBoundaryProps,
  VisualizationErrorBoundaryState
> {
  state: VisualizationErrorBoundaryState = { message: null };

  static getDerivedStateFromError(error: unknown): VisualizationErrorBoundaryState {
    return { message: formatVisualizationError(error) };
  }

  componentDidCatch(error: unknown): void {
    this.props.onError(formatVisualizationError(error));
  }

  render() {
    if (this.state.message) {
      return <VisualizationFailure message={this.state.message} />;
    }
    return this.props.children;
  }
}
