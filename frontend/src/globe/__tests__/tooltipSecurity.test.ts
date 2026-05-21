// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.

import { afterEach, describe, expect, it } from "vitest";
import * as THREE from "three";
import { setupGpuPicker } from "../gpuPicker";
import { setupRaycaster } from "../raycaster";

function makeCanvas(): HTMLCanvasElement {
  const canvas = document.createElement("canvas");
  Object.defineProperty(canvas, "getBoundingClientRect", {
    value: () => ({ left: 0, top: 0, width: 100, height: 100, right: 100, bottom: 100 }),
  });
  document.body.appendChild(canvas);
  return canvas;
}

function makeSceneWithNode(nodeId: string): THREE.Scene {
  const scene = new THREE.Scene();
  const mesh = new THREE.Mesh(
    new THREE.SphereGeometry(0.5, 8, 8),
    new THREE.MeshBasicMaterial(),
  );
  mesh.userData["nodeId"] = nodeId;
  mesh.userData["nodeType"] = "unknown";
  scene.add(mesh);
  scene.updateMatrixWorld(true);
  return scene;
}

function makeCamera(): THREE.PerspectiveCamera {
  const camera = new THREE.PerspectiveCamera(75, 1, 0.1, 1000);
  camera.position.set(0, 0, 5);
  camera.lookAt(0, 0, 0);
  camera.updateMatrixWorld(true);
  return camera;
}

function hoverCenter(canvas: HTMLCanvasElement): void {
  canvas.dispatchEvent(new MouseEvent("mousemove", { clientX: 50, clientY: 50 }));
}

function visibleTooltip(): HTMLDivElement {
  const tips = [...document.body.querySelectorAll("div")] as HTMLDivElement[];
  const tip = tips.find((el) => el.style.display === "block");
  if (!tip) throw new Error("tooltip was not displayed");
  return tip;
}

describe("globe tooltip rendering", () => {
  const malicious = `normal\n<img src=x onerror=alert(1)>\n<script>alert(1)</script>\n"'&<>`;

  afterEach(() => {
    document.body.replaceChildren();
  });

  it("raycaster renders metadata as text, not HTML", () => {
    const canvas = makeCanvas();
    setupRaycaster(canvas, makeCamera(), makeSceneWithNode(malicious), () => {}, () => ({
      rotationRad: 0,
      angularVelocityRadS: 0,
    }));

    hoverCenter(canvas);

    const tip = visibleTooltip();
    expect(tip.style.whiteSpace).toBe("pre-line");
    expect(tip.textContent).toContain("\n");
    expect(tip.textContent).toContain("<script>alert(1)</script>");
    expect(tip.querySelector("script")).toBeNull();
    expect(tip.querySelector("img")).toBeNull();
  });

  it("GPU picker renders metadata as text, not HTML", () => {
    const canvas = makeCanvas();
    setupGpuPicker(canvas, makeCamera(), makeSceneWithNode(malicious), () => {}, () => ({
      rotationRad: 0,
      angularVelocityRadS: 0,
    }));

    hoverCenter(canvas);

    const tip = visibleTooltip();
    expect(tip.style.whiteSpace).toBe("pre-line");
    expect(tip.textContent).toContain("\n");
    expect(tip.textContent).toContain("<img src=x onerror=alert(1)>");
    expect(tip.querySelector("script")).toBeNull();
    expect(tip.querySelector("img")).toBeNull();
  });
});
