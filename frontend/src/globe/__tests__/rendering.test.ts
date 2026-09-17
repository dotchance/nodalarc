// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
// Tests for rendering invariants that caught real bugs in production.
//
// These tests verify structural properties that, when violated, produce
// visible rendering failures. Each test documents a specific bug that
// was discovered during live deployment.

import { afterEach, describe, it, expect, vi } from "vitest";
import { createElement } from "react";
import { cleanup, render } from "@testing-library/react";
import * as THREE from "three";
import { Labels } from "../r3f/Labels";
import { clearPositions, setBodyFrame, setNodeLocalPosition } from "../r3f/positions";
import type { NodeState } from "../../types";
import { tokens } from "../../styles/tokens";
import { SCENE_EARTH_RADIUS } from "../../sim/orbitalMath";
import { catalogEarthKmPerRenderUnit } from "../../sim/__tests__/bodyModelFixture";

const EARTH_KM_PER_RENDER_UNIT = catalogEarthKmPerRenderUnit();

const frame = vi.hoisted(() => ({
  callback: () => {},
  camera: null as THREE.PerspectiveCamera | null,
}));
vi.mock("@react-three/fiber", () => ({
  useFrame: (callback: () => void) => {
    frame.callback = callback;
  },
  useThree: (select: (state: { camera: THREE.Camera | null }) => unknown) => select(frame),
}));

afterEach(() => {
  cleanup();
  clearPositions();
  setBodyFrame("earth", null);
});

it.each([
  { name: "default camera", cameraX: tokens.cameraDistance, visible: true, opaque: true },
  {
    name: "fade interval",
    cameraX: SCENE_EARTH_RADIUS + 550 / EARTH_KM_PER_RENDER_UNIT + 350,
    visible: true,
    opaque: false,
  },
  {
    name: "beyond fade interval",
    cameraX: SCENE_EARTH_RADIUS + 550 / EARTH_KM_PER_RENDER_UNIT + 550,
    visible: false,
    opaque: false,
  },
])("Labels renders the satellite at $name", ({ cameraX, visible, opaque }) => {
  const container = document.createElement("div");
  Object.defineProperties(container, {
    clientWidth: { value: 800 },
    clientHeight: { value: 600 },
  });
  frame.camera = new THREE.PerspectiveCamera(45, 800 / 600, 0.1, 10000);
  frame.camera.position.set(cameraX, 0, 0);
  frame.camera.lookAt(0, 0, 0);
  frame.camera.updateMatrixWorld(true);
  setBodyFrame("earth", new THREE.Group(), SCENE_EARTH_RADIUS);
  setNodeLocalPosition("sat", "earth", SCENE_EARTH_RADIUS + 550 / EARTH_KM_PER_RENDER_UNIT, 0, 0);
  render(
    createElement(Labels, {
      nodes: [{ node_id: "sat", node_type: "satellite", reference_body: "earth" } as NodeState],
      containerRef: { current: container },
      selectedNodeId: "sat",
    }),
  );
  frame.callback();
  const label = Array.from(container.children).find(
    (element) => element.textContent === "sat",
  ) as HTMLDivElement;
  expect(label).toBeDefined();
  expect(label.style.display).toBe(visible ? "block" : "none");
  if (visible) {
    const opacity = Number(label.style.opacity);
    expect(opacity).toBeGreaterThan(0);
    if (opaque) expect(opacity).toBe(1);
    else expect(opacity).toBeLessThan(1);
  }
});

describe("rendering invariants", () => {
  describe("camera bounds include full constellation view", () => {
    // BUG: Camera max distance was too restrictive (600 = 6x earth)
    // preventing operators from zooming out for full constellation view.
    it("max camera distance allows seeing the full LEO shell", () => {
      const satOrbitRadius = SCENE_EARTH_RADIUS + 550 / EARTH_KM_PER_RENDER_UNIT;
      expect(tokens.cameraMaxDistance).toBeGreaterThan(satOrbitRadius * 3);
    });

    it("max camera distance allows framing a GEO shell", () => {
      const geoOrbitRadius = SCENE_EARTH_RADIUS + 35786 / EARTH_KM_PER_RENDER_UNIT;
      const halfFovRad = (tokens.cameraFov * Math.PI) / 360;
      const requiredDistance = geoOrbitRadius / Math.sin(halfFovRad);
      expect(tokens.cameraMaxDistance).toBeGreaterThan(requiredDistance * 1.25);
    });

    it("min camera distance is above the earth surface", () => {
      expect(tokens.cameraMinDistance).toBeGreaterThan(SCENE_EARTH_RADIUS);
    });
  });
});
