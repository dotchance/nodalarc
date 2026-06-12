// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
/**
 * Floating operational window chrome (the design system's "operational
 * window" contract): title-bar drag, 8-direction edge/corner resize, close,
 * optional header controls, optional geometry persistence. Content is the
 * consumer's; the window owns ONLY chrome. Rides on the zWindow layer —
 * above all fixed chrome, below nothing.
 *
 * Replaces the hand-rolled chrome that lived inside LogPanel so every
 * operational window (logs, trace, future router-output panes) shares one
 * implementation and one keyboard/pointer behavior.
 */

import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import { IconButton } from "./Button";

export interface WindowGeometry {
  x: number;
  y: number;
  w: number;
  h: number;
}

interface FloatingWindowProps {
  title: ReactNode;
  /** Extra header content (filters, font-size controls) — consumer-owned. */
  headerExtras?: ReactNode;
  onClose: () => void;
  initial: WindowGeometry;
  minWidth?: number;
  minHeight?: number;
  /** localStorage key suffix; geometry persists as nodalarc.window.<key>. */
  persistKey?: string;
  children: ReactNode;
}

type ResizeEdge = "n" | "s" | "e" | "w" | "ne" | "nw" | "se" | "sw";
const EDGES: readonly ResizeEdge[] = ["n", "s", "e", "w", "ne", "nw", "se", "sw"];

function loadGeometry(persistKey: string | undefined, fallback: WindowGeometry): WindowGeometry {
  if (!persistKey || typeof localStorage === "undefined") return fallback;
  try {
    const raw = localStorage.getItem(`nodalarc.window.${persistKey}`);
    if (!raw) return fallback;
    const g = JSON.parse(raw) as WindowGeometry;
    if (![g.x, g.y, g.w, g.h].every(Number.isFinite)) return fallback;
    // Clamp so a persisted window is never restored off-screen.
    const maxX = Math.max(0, window.innerWidth - 80);
    const maxY = Math.max(0, window.innerHeight - 60);
    return { x: Math.min(Math.max(0, g.x), maxX), y: Math.min(Math.max(0, g.y), maxY), w: g.w, h: g.h };
  } catch {
    return fallback;
  }
}

export function FloatingWindow({
  title,
  headerExtras,
  onClose,
  initial,
  minWidth = 280,
  minHeight = 160,
  persistKey,
  children,
}: FloatingWindowProps) {
  const [geom, setGeom] = useState<WindowGeometry>(() => loadGeometry(persistKey, initial));
  const geomRef = useRef(geom);
  geomRef.current = geom;

  const persist = useCallback(() => {
    if (!persistKey || typeof localStorage === "undefined") return;
    // Persist from the ref: state captured at drag start would be stale here.
    localStorage.setItem(`nodalarc.window.${persistKey}`, JSON.stringify(geomRef.current));
  }, [persistKey]);

  const beginDrag = useCallback(
    (e: React.PointerEvent) => {
      if (e.button !== 0) return;
      if ((e.target as HTMLElement).closest("button, input, select")) return;
      e.preventDefault();
      const start = { x: e.clientX, y: e.clientY, gx: geomRef.current.x, gy: geomRef.current.y };
      const onMove = (ev: PointerEvent) => {
        setGeom((g) => ({
          ...g,
          x: Math.max(0, start.gx + ev.clientX - start.x),
          y: Math.max(0, start.gy + ev.clientY - start.y),
        }));
      };
      const onUp = () => {
        window.removeEventListener("pointermove", onMove);
        window.removeEventListener("pointerup", onUp);
        persist();
      };
      window.addEventListener("pointermove", onMove);
      window.addEventListener("pointerup", onUp);
    },
    [persist],
  );

  const beginResize = useCallback(
    (edge: ResizeEdge) => (e: React.PointerEvent) => {
      if (e.button !== 0) return;
      e.preventDefault();
      e.stopPropagation();
      const start = { x: e.clientX, y: e.clientY, g: { ...geomRef.current } };
      const onMove = (ev: PointerEvent) => {
        const dx = ev.clientX - start.x;
        const dy = ev.clientY - start.y;
        setGeom(() => {
          let { x, y, w, h } = start.g;
          if (edge.includes("e")) w = Math.max(minWidth, start.g.w + dx);
          if (edge.includes("s")) h = Math.max(minHeight, start.g.h + dy);
          if (edge.includes("w")) {
            w = Math.max(minWidth, start.g.w - dx);
            x = start.g.x + (start.g.w - w);
          }
          if (edge.includes("n")) {
            h = Math.max(minHeight, start.g.h - dy);
            y = start.g.y + (start.g.h - h);
          }
          return { x, y, w, h };
        });
      };
      const onUp = () => {
        window.removeEventListener("pointermove", onMove);
        window.removeEventListener("pointerup", onUp);
        persist();
      };
      window.addEventListener("pointermove", onMove);
      window.addEventListener("pointerup", onUp);
    },
    [minWidth, minHeight, persist],
  );

  // Escape closes the focused window.
  const rootRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key !== "Escape") return;
      const root = rootRef.current;
      if (root && root.contains(document.activeElement)) onClose();
    };
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [onClose]);

  return (
    <div
      ref={rootRef}
      className="ui-window"
      role="dialog"
      aria-label={typeof title === "string" ? title : undefined}
      style={{ left: geom.x, top: geom.y, width: geom.w, height: geom.h }}
    >
      <header className="ui-window-title" onPointerDown={beginDrag}>
        <strong className="ui-window-title-text">{title}</strong>
        <div className="ui-window-actions">
          {headerExtras}
          <IconButton icon="x" label="Close" onClick={onClose} />
        </div>
      </header>
      <div className="ui-window-body">{children}</div>
      {EDGES.map((edge) => (
        <span key={edge} className={`ui-window-edge ui-window-edge--${edge}`} onPointerDown={beginResize(edge)} />
      ))}
    </div>
  );
}
