// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
/** Imperative globe actions exposed to app chrome and node popovers. */

export interface GlobeActions {
  frameScene: () => void;
  focusBody: (bodyId: string) => void;
  focusNode: (nodeId: string, options?: { follow?: boolean }) => void;
  focusLink: (nodeA: string, nodeB: string, options?: { follow?: boolean }) => void;
  flyToTopView: () => void;
  setFollowTarget: (nodeId: string | null) => void;
  captureScreenshot: () => void;
  flyToNode: (nodeId: string) => void;
  flyToSegment: (nodeIds: string[]) => void;
  getNodeScreenPosition: (nodeId: string) => { x: number; y: number; visible: boolean } | null;
}
