// Copyright 2024-2026 .chance (dotchance)
// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms under AGPL-3.0 Section 7 are in the NOTICE file.
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
