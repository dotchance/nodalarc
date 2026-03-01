/** Raycaster for satellite/GS/link picking. */

import * as THREE from "three";
import type { Selection } from "../types";

let tooltip: HTMLDivElement | null = null;

function getTooltip(): HTMLDivElement {
  if (!tooltip) {
    tooltip = document.createElement("div");
    tooltip.style.cssText = `
      position: fixed;
      background: rgba(26, 26, 46, 0.9);
      border: 1px solid #2a2a4a;
      color: #e0e0e0;
      padding: 4px 8px;
      border-radius: 4px;
      font-size: 11px;
      pointer-events: none;
      display: none;
      z-index: 100;
      font-family: monospace;
    `;
    document.body.appendChild(tooltip);
  }
  return tooltip;
}

export function setupRaycaster(
  canvas: HTMLCanvasElement,
  camera: THREE.PerspectiveCamera,
  scene: THREE.Scene,
  onSelect: (sel: Selection | null) => void,
): void {
  const raycaster = new THREE.Raycaster();
  raycaster.params.Line = { threshold: 5 };
  const mouse = new THREE.Vector2();
  const tip = getTooltip();

  canvas.addEventListener("mousemove", (event: MouseEvent) => {
    const rect = canvas.getBoundingClientRect();
    mouse.x = ((event.clientX - rect.left) / rect.width) * 2 - 1;
    mouse.y = -((event.clientY - rect.top) / rect.height) * 2 + 1;

    raycaster.setFromCamera(mouse, camera);
    const intersects = raycaster.intersectObjects(scene.children, false);

    const hit = intersects.find(
      (i) => i.object.userData["nodeId"] || i.object.userData["linkKey"],
    );

    if (hit) {
      const nodeId = hit.object.userData["nodeId"] as string | undefined;
      const linkKey = hit.object.userData["linkKey"] as string | undefined;
      tip.textContent = nodeId ?? linkKey ?? "";
      tip.style.display = "block";
      tip.style.left = `${event.clientX + 12}px`;
      tip.style.top = `${event.clientY - 8}px`;
      canvas.style = "pointer";
    } else {
      tip.style.display = "none";
      canvas.style = "grab";
    }
  });

  canvas.addEventListener("click", (event: MouseEvent) => {
    const rect = canvas.getBoundingClientRect();
    mouse.x = ((event.clientX - rect.left) / rect.width) * 2 - 1;
    mouse.y = -((event.clientY - rect.top) / rect.height) * 2 + 1;

    raycaster.setFromCamera(mouse, camera);
    const intersects = raycaster.intersectObjects(scene.children, false);

    const hit = intersects.find(
      (i) => i.object.userData["nodeId"] || i.object.userData["linkKey"],
    );

    if (hit) {
      const nodeId = hit.object.userData["nodeId"] as string | undefined;
      const nodeType = hit.object.userData["nodeType"] as string | undefined;
      const linkKey = hit.object.userData["linkKey"] as string | undefined;

      if (nodeId && nodeType) {
        onSelect({
          type: nodeType === "satellite" ? "satellite" : "ground_station",
          id: nodeId,
        });
      } else if (linkKey) {
        onSelect({ type: "link", id: linkKey });
      }
    } else {
      onSelect(null);
    }
  });
}
