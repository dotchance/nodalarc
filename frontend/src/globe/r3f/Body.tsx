// Copyright 2024-2026 .chance (dotchance)
// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms under AGPL-3.0 Section 7 are in the NOTICE file.
/**
 * Body: a celestial body's local coordinate frame (Earth, Luna, Mars). It is a group
 * positioned in the universe frame plus a BodyFrame context; its children — the body's
 * APPEARANCE (<Earth>), constellations, ground stations, envelopes — position themselves
 * in its local frame. Appearance is a child, not built in, so the same <Body> frame
 * renders any body by composing the matching appearance component. Adding Mars or a lunar
 * constellation is adding a <Body> subtree, not a new imperative subsystem.
 *
 * Each Body REGISTERS its group as the position registry's frame for its body id (setBodyFrame),
 * via a callback ref so it fires the moment the group attaches — Body sits behind the Earth-texture
 * <Suspense>, so a one-shot effect would race the mount and register null (the bug that mirrored
 * the globe). The callback also forwards the group to the parent ref (FrameDriver / orbit layers
 * drive its rotation). Every body owns its own registration; the registry resolves each node
 * through ITS body's frame.
 */

import { forwardRef, useCallback, type ReactNode } from "react";
import * as THREE from "three";
import { BodyFrameProvider, type BodyFrameValue } from "./BodyFrame";
import { setBodyFrame } from "./positions";
import { kmToRender } from "./units";

interface BodyProps {
  id: string;
  radiusKm: number;
  kmPerRenderUnit: number;
  /** False while the scene has no body samples for the render time. FrameDriver sets the
   *  group's position and rotation each frame from the samples. */
  visible: boolean;
  onFocusBody?: (bodyId: string) => void;
  children?: ReactNode;
}

/** ref exposes the body's group so the render loop can drive its frame rotation. */
export const Body = forwardRef<THREE.Group, BodyProps>(function Body(
  { id, radiusKm, kmPerRenderUnit, visible, onFocusBody, children },
  ref,
) {
  const radiusRender = kmToRender(radiusKm, kmPerRenderUnit);
  const frame: BodyFrameValue = { id, radiusKm, kmPerRenderUnit, radiusRender };

  const attach = useCallback(
    (group: THREE.Group | null) => {
      setBodyFrame(id, group, radiusRender);
      if (typeof ref === "function") ref(group);
      else if (ref) ref.current = group;
    },
    [id, radiusRender, ref],
  );

  return (
    <BodyFrameProvider value={frame}>
      <group
        ref={attach}
        name={`body-${id}`}
        visible={visible}
        onDoubleClick={(e) => {
          if (!onFocusBody) return;
          e.stopPropagation();
          onFocusBody(id);
        }}
      >
        {children}
      </group>
    </BodyFrameProvider>
  );
});
