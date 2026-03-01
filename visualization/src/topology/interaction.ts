/** Canvas interaction — pan, zoom, click detection. */

export interface ViewTransform {
  offsetX: number;
  offsetY: number;
  scale: number;
}

export function setupInteraction(
  canvas: HTMLCanvasElement,
  getTransform: () => ViewTransform,
  setTransform: (t: ViewTransform) => void,
  onCanvasClick: (worldX: number, worldY: number) => void,
): () => void {
  let isPanning = false;
  let lastX = 0;
  let lastY = 0;

  const onMouseDown = (e: MouseEvent) => {
    isPanning = true;
    lastX = e.clientX;
    lastY = e.clientY;
    canvas.style.cursor = "grabbing";
  };

  const onMouseMove = (e: MouseEvent) => {
    if (!isPanning) return;
    const dx = e.clientX - lastX;
    const dy = e.clientY - lastY;
    lastX = e.clientX;
    lastY = e.clientY;

    const t = getTransform();
    setTransform({
      ...t,
      offsetX: t.offsetX + dx,
      offsetY: t.offsetY + dy,
    });
  };

  const onMouseUp = () => {
    isPanning = false;
    canvas.style.cursor = "grab";
  };

  const onWheel = (e: WheelEvent) => {
    e.preventDefault();
    const t = getTransform();
    const factor = e.deltaY < 0 ? 1.1 : 0.9;
    const newScale = Math.max(0.2, Math.min(5, t.scale * factor));

    // Zoom toward mouse position
    const rect = canvas.getBoundingClientRect();
    const mx = e.clientX - rect.left;
    const my = e.clientY - rect.top;

    setTransform({
      scale: newScale,
      offsetX: mx - (mx - t.offsetX) * (newScale / t.scale),
      offsetY: my - (my - t.offsetY) * (newScale / t.scale),
    });
  };

  const onClick = (e: MouseEvent) => {
    const rect = canvas.getBoundingClientRect();
    const t = getTransform();
    const worldX = (e.clientX - rect.left - t.offsetX) / t.scale;
    const worldY = (e.clientY - rect.top - t.offsetY) / t.scale;
    onCanvasClick(worldX, worldY);
  };

  canvas.addEventListener("mousedown", onMouseDown);
  canvas.addEventListener("mousemove", onMouseMove);
  canvas.addEventListener("mouseup", onMouseUp);
  canvas.addEventListener("mouseleave", onMouseUp);
  canvas.addEventListener("wheel", onWheel, { passive: false });
  canvas.addEventListener("click", onClick);

  return () => {
    canvas.removeEventListener("mousedown", onMouseDown);
    canvas.removeEventListener("mousemove", onMouseMove);
    canvas.removeEventListener("mouseup", onMouseUp);
    canvas.removeEventListener("mouseleave", onMouseUp);
    canvas.removeEventListener("wheel", onWheel);
    canvas.removeEventListener("click", onClick);
  };
}
