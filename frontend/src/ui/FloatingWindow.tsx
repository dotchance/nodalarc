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

import { useCallback, useEffect, useId, useLayoutEffect, useRef, useState, type ReactNode } from "react";
import { IconButton } from "./Button";
import { addWindow, raiseWindow, removeWindow, useWindowRank } from "./windowStack";

// A stable fallback id for windows that don't pass one (e.g. the live LogPanel,
// the only window in its view). Module-scoped so ids never collide across mounts.
let _nextWindowId = 0;

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
  /** Stable identity in the raise stack. The builder passes its window
   *  key so a re-open can raise the same window; a lone window (LogPanel) omits
   *  it and gets a generated id. */
  raiseId?: string;
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
  raiseId,
  children,
}: FloatingWindowProps) {
  const titleId = useId();
  const [geom, setGeom] = useState<WindowGeometry>(() => loadGeometry(persistKey, initial));
  const geomRef = useRef(geom);
  geomRef.current = geom;

  // The window's identity in the raise stack: the passed id, or a stable
  // generated one. Register at the top on mount, drop on unmount; the rank is
  // this window's z offset within the zWindow band.
  const idRef = useRef<string | null>(null);
  if (idRef.current === null) idRef.current = raiseId ?? `floating-${_nextWindowId++}`;
  const windowId = raiseId ?? idRef.current;
  // Register BEFORE paint (useLayoutEffect), so a freshly opened window never
  // paints one frame at the pre-registration rank of -1 (z one below the band).
  useLayoutEffect(() => {
    addWindow(windowId);
    return () => removeWindow(windowId);
  }, [windowId]);
  const rank = useWindowRank(windowId);

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
  const previousFocusRef = useRef<HTMLElement | null>(null);
  useLayoutEffect(() => {
    const root = rootRef.current;
    if (!root) return;
    previousFocusRef.current = document.activeElement instanceof HTMLElement
      ? document.activeElement
      : null;
    // Run after the opening click's native focus default. Focusing during the
    // synchronous React commit is too early: the browser then puts focus back
    // on the opener after the event handler returns.
    const focusTimer = window.setTimeout(() => {
      const firstControl = root.querySelector<HTMLElement>(
        ".ui-window-body button:not([disabled]):not([hidden]), .ui-window-body input:not([disabled]):not([hidden]):not([type='hidden']), .ui-window-body select:not([disabled]):not([hidden]), .ui-window-body textarea:not([disabled]):not([hidden]), .ui-window-body [tabindex]:not([tabindex='-1']):not([hidden])",
      );
      (firstControl ?? root).focus();
    }, 50);
    return () => {
      window.clearTimeout(focusTimer);
      const previous = previousFocusRef.current;
      if (root.contains(document.activeElement) && previous?.isConnected) previous.focus();
    };
  }, []);
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
      aria-labelledby={titleId}
      tabIndex={-1}
      // Capture so a pointerdown anywhere in the window raises it even when a
      // child (a resize edge) stops propagation. --window-z-bump is the rank.
      onPointerDownCapture={() => raiseWindow(windowId)}
      style={
        {
          left: geom.x,
          top: geom.y,
          width: geom.w,
          height: geom.h,
          "--window-z-bump": rank,
        } as React.CSSProperties
      }
    >
      <header className="ui-window-title" onPointerDown={beginDrag}>
        <strong id={titleId} className="ui-window-title-text">{title}</strong>
        <div className="ui-window-actions">
          {headerExtras}
          <IconButton icon="x" label="Close" onClick={onClose} />
        </div>
      </header>
      <div className="ui-window-body">{children}</div>
      {EDGES.map((edge) => (
        <span
          key={edge}
          aria-hidden="true"
          className={`ui-window-edge ui-window-edge--${edge}`}
          onPointerDown={beginResize(edge)}
        />
      ))}
    </div>
  );
}
