// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
/**
 * Body: a celestial body (Earth, Luna, Mars) rendered as a sphere that provides a
 * BodyFrame to its children. Constellations, ground stations, and envelopes are
 * children of a Body and position themselves in its local frame. Adding Mars or a
 * lunar constellation is adding a <Body> subtree, not a new imperative subsystem.
 */

import type { ReactNode } from "react";
import { BodyFrameProvider, type BodyFrameValue } from "./BodyFrame";
import { kmToRender } from "./units";

export function Body({
  id,
  radiusKm,
  color = "#13314f",
  position = [0, 0, 0],
  children,
}: {
  id: string;
  radiusKm: number;
  color?: string;
  /** Body centre in the universe frame (render units). Earth sits at origin today. */
  position?: [number, number, number];
  children?: ReactNode;
}) {
  const radiusRender = kmToRender(radiusKm);
  const frame: BodyFrameValue = { id, radiusKm, radiusRender };
  return (
    <BodyFrameProvider value={frame}>
      <group name={`body-${id}`} position={position}>
        <mesh>
          <sphereGeometry args={[radiusRender, 64, 64]} />
          <meshStandardMaterial color={color} />
        </mesh>
        {children}
      </group>
    </BodyFrameProvider>
  );
}
