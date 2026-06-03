// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
/**
 * LinkPicker — screen-space missed-click picker. Exact R3F object hits still handle sats, GSs,
 * and body meshes. When the pointer misses physical geometry, this picker gives tiny visible
 * objects a usable screen-space target: nodes first, then active link beams, then bodies.
 *
 * It lives INSIDE the Canvas (it needs the live camera + canvas via useThree) but renders nothing;
 * instead it publishes its hit-test-and-select function into `handlerRef`, which Scene wires to
 * the Canvas-level onPointerMissed. R3F's Canvas onPointerMissed fires only when a click hit no
 * interactive object (empty space, the Earth, or a non-pickable beam) — precisely the legacy
 * gpuPicker "no nodeHit" branch. A hit selects the link; a miss clears the selection (the
 * deselect-on-empty-click the legacy did via onSelect(null)). The selection id is
 * linkKey() == sorted(a,b).join(":"), the same id the InfoPanel's LinkDetail matches on.
 *
 * Refinements over legacy: the hit-test honors the show toggles (an ISL/ground beam that is
 * hidden is not pickable), and MEO/GEO/cislunar nodes remain selectable without visually inflating
 * the scene. Modified clicks (ctrl/cmd) are swallowed, matching the legacy gpuPicker.
 */

import { useEffect, useRef, type MutableRefObject } from "react";
import { useThree } from "@react-three/fiber";
import { linkKey } from "./linkBatch";
import type { LinkState, NodeState, Selection } from "../../types";
import { pickSceneAtScreenPoint, type PickBody } from "./sceneHitTesting";

interface LinkPickerProps {
  nodes: NodeState[];
  links: LinkState[];
  bodies: PickBody[];
  showIslLinks: boolean;
  showGroundLinks: boolean;
  onSelect: (sel: Selection | null) => void;
  onFocusNode: (nodeId: string) => void;
  onFocusLink: (nodeA: string, nodeB: string) => void;
  onFocusBody: (bodyId: string) => void;
  /** Scene wires this to the Canvas-level onPointerMissed. */
  handlerRef: MutableRefObject<(event: MouseEvent) => void>;
}

export function LinkPicker({
  nodes,
  links,
  bodies,
  showIslLinks,
  showGroundLinks,
  onSelect,
  onFocusNode,
  onFocusLink,
  onFocusBody,
  handlerRef,
}: LinkPickerProps) {
  const camera = useThree((s) => s.camera);
  const gl = useThree((s) => s.gl);
  // Read inputs through refs so the published handler closure stays stable and never goes stale.
  const nodesRef = useRef(nodes);
  nodesRef.current = nodes;
  const linksRef = useRef(links);
  linksRef.current = links;
  const bodiesRef = useRef(bodies);
  bodiesRef.current = bodies;
  const showIslRef = useRef(showIslLinks);
  showIslRef.current = showIslLinks;
  const showGndRef = useRef(showGroundLinks);
  showGndRef.current = showGroundLinks;

  useEffect(() => {
    handlerRef.current = (e: MouseEvent) => {
      // Modified click is swallowed (legacy: reserved for sat orbit-pinning; no-op on a miss).
      if (e.ctrlKey || e.metaKey) return;
      const rect = gl.domElement.getBoundingClientRect();
      const hit = pickSceneAtScreenPoint({
        xPx: e.clientX - rect.left,
        yPx: e.clientY - rect.top,
        camera,
        rect,
        nodes: nodesRef.current,
        links: linksRef.current,
        bodies: bodiesRef.current,
        showIslLinks: showIslRef.current,
        showGroundLinks: showGndRef.current,
      });
      if (!hit) {
        onSelect(null);
        return;
      }
      if (hit.kind === "node") {
        const node = hit.node;
        onSelect({
          type: node.node_type === "ground_station" ? "ground_station" : "satellite",
          id: node.node_id,
        });
        if (e.detail >= 2) onFocusNode(node.node_id);
      } else if (hit.kind === "link") {
        onSelect({ type: "link", id: linkKey(hit.link.node_a, hit.link.node_b) });
        if (e.detail >= 2) onFocusLink(hit.link.node_a, hit.link.node_b);
      } else if (e.detail >= 2) {
        onFocusBody(hit.bodyId);
      } else {
        onSelect(null);
      }
    };
  }, [camera, gl, onSelect, onFocusNode, onFocusLink, onFocusBody, handlerRef]);

  return null;
}
