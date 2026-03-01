/** Raycaster for satellite/GS/link picking with hover highlight. */

import * as THREE from "three";
import { getSatellites } from "./satellites";
import { getGroundStations } from "./groundStations";
import type { Selection } from "../types";

let tooltip: HTMLDivElement | null = null;
let hoveredObject: THREE.Object3D | null = null;
const HOVER_SCALE = 1.3;

function getTooltip(): HTMLDivElement {
  if (!tooltip) {
    tooltip = document.createElement("div");
    tooltip.style.cssText = `
      position: fixed;
      background: rgba(26, 26, 46, 0.92);
      border: 1px solid #2a2a4a;
      color: #e0e0e0;
      padding: 4px 10px;
      border-radius: 4px;
      font-size: 11px;
      pointer-events: none;
      display: none;
      z-index: 100;
      font-family: "JetBrains Mono", monospace;
      line-height: 1.4;
    `;
    document.body.appendChild(tooltip);
  }
  return tooltip;
}

/** Build tooltip content with contextual info. */
function buildTooltipContent(nodeId: string, nodeType: string): string {
  if (nodeType === "satellite") {
    const sat = getSatellites().get(nodeId);
    if (sat) {
      const ns = sat.nodeState;
      const area = ns.routing_area ?? "none";
      const isl = ns.isl_count;
      const gnd = ns.gnd_count;
      return `${nodeId}\n${isl} ISLs, ${gnd} GND, Area ${area}`;
    }
  } else if (nodeType === "ground_station") {
    const gs = getGroundStations().get(nodeId);
    if (gs) {
      const ns = gs.nodeState;
      return `${nodeId}\n${ns.lat_deg.toFixed(1)}°, ${ns.lon_deg.toFixed(1)}°`;
    }
  }
  return nodeId;
}

function clearHover(): void {
  if (hoveredObject) {
    hoveredObject.scale.set(1, 1, 1);
    hoveredObject = null;
  }
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

    clearHover();

    if (hit) {
      const nodeId = hit.object.userData["nodeId"] as string | undefined;
      const nodeType = hit.object.userData["nodeType"] as string | undefined;
      const linkKey = hit.object.userData["linkKey"] as string | undefined;

      if (nodeId && nodeType) {
        tip.innerHTML = buildTooltipContent(nodeId, nodeType).replace("\n", "<br>");
        // Hover highlight: scale up
        hit.object.scale.set(HOVER_SCALE, HOVER_SCALE, HOVER_SCALE);
        hoveredObject = hit.object;
      } else {
        tip.textContent = linkKey ?? "";
      }

      tip.style.display = "block";
      tip.style.left = `${event.clientX + 12}px`;
      tip.style.top = `${event.clientY - 8}px`;
      canvas.style.cursor = "pointer";
    } else {
      tip.style.display = "none";
      canvas.style.cursor = "grab";
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
