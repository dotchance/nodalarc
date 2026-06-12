// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
/**
 * Photoshop-style tool slot.
 *
 * Gesture, one continuous motion — NOT click-then-click:
 *  - quick click       → advance to the next variant (mode slots are always-on,
 *                        so "activate current tool" is a no-op; cycling keeps
 *                        single-click switching as fast as the old buttons);
 *  - press + HOLD      → flyout opens in drag-select mode: moving the pressed
 *                        pointer highlights rows, RELEASE commits the
 *                        highlighted variant, release outside cancels;
 *  - right-click /     → same flyout in click-select mode (hover highlights,
 *    long-press touch    click commits, Escape or outside-click cancels).
 *
 * The face shows the active variant; a corner triangle marks multi-variant
 * slots; rows show active-dot · icon · label · right-aligned shortcut.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { Icon, type IconName } from "./icons/Icon";

const HOLD_MS = 300;

export interface ToolVariant<V extends string = string> {
  value: V;
  label: string;
  icon: IconName;
  /** Keyboard shortcut shown right-aligned in the flyout row. */
  shortcut?: string;
}

interface ToolSlotProps<V extends string> {
  /** Slot label (tooltip prefix + accessible name). */
  label: string;
  variants: readonly ToolVariant<V>[];
  /** The variant currently shown on the slot face. */
  active: V;
  /** Visual engaged state for the face (e.g. this view is the current view). */
  engaged?: boolean;
  onSelect: (value: V) => void;
}

type FlyoutMode = "closed" | "drag" | "click";

export function ToolSlot<V extends string>({
  label,
  variants,
  active,
  engaged = true,
  onSelect,
}: ToolSlotProps<V>) {
  const [flyout, setFlyout] = useState<FlyoutMode>("closed");
  const [highlight, setHighlight] = useState<V | null>(null);
  const rootRef = useRef<HTMLDivElement>(null);
  const holdTimer = useRef<number | null>(null);
  const pressedRef = useRef(false);

  const activeVariant = variants.find((v) => v.value === active) ?? variants[0]!;
  const hasVariants = variants.length > 1;

  const clearHold = useCallback(() => {
    if (holdTimer.current !== null) {
      window.clearTimeout(holdTimer.current);
      holdTimer.current = null;
    }
  }, []);

  const close = useCallback(() => {
    setFlyout("closed");
    setHighlight(null);
  }, []);

  const cycle = useCallback(() => {
    const idx = variants.findIndex((v) => v.value === active);
    const next = variants[(idx + 1) % variants.length]!;
    onSelect(next.value);
  }, [variants, active, onSelect]);

  // Escape + outside-click close (click-select mode).
  useEffect(() => {
    if (flyout !== "click") return;
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") close();
    };
    const onPointerDown = (e: PointerEvent) => {
      const root = rootRef.current;
      if (root && e.target instanceof Node && !root.contains(e.target)) close();
    };
    document.addEventListener("keydown", onKeyDown);
    document.addEventListener("pointerdown", onPointerDown);
    return () => {
      document.removeEventListener("keydown", onKeyDown);
      document.removeEventListener("pointerdown", onPointerDown);
    };
  }, [flyout, close]);

  useEffect(() => clearHold, [clearHold]);

  const rowValueAt = (clientX: number, clientY: number): V | null => {
    const el = document.elementFromPoint(clientX, clientY);
    const row = el?.closest?.("[data-toolslot-value]");
    const value = row?.getAttribute("data-toolslot-value");
    return value !== null && value !== undefined ? (value as V) : null;
  };

  const onPointerDown = (e: React.PointerEvent<HTMLButtonElement>) => {
    if (e.button !== 0) return;
    pressedRef.current = true;
    if (!hasVariants) return;
    // Capture so move/up keep firing on the button while the user drags
    // through the flyout (rows are hit-tested via elementFromPoint). Without
    // capture (jsdom, some touch stacks) the hold opens click-select mode,
    // which has its own close paths.
    let captured = false;
    try {
      e.currentTarget.setPointerCapture(e.pointerId);
      captured = true;
    } catch {
      captured = false;
    }
    clearHold();
    holdTimer.current = window.setTimeout(() => {
      holdTimer.current = null;
      if (pressedRef.current) {
        setFlyout(captured ? "drag" : "click");
        setHighlight(null);
      }
    }, HOLD_MS);
  };

  const onPointerMove = (e: React.PointerEvent<HTMLButtonElement>) => {
    if (flyout !== "drag" || !pressedRef.current) return;
    setHighlight(rowValueAt(e.clientX, e.clientY));
  };

  const onPointerUp = (e: React.PointerEvent<HTMLButtonElement>) => {
    if (e.button !== 0) return;
    const wasPressed = pressedRef.current;
    pressedRef.current = false;
    if (flyout === "drag") {
      const value = rowValueAt(e.clientX, e.clientY);
      if (value !== null && variants.some((v) => v.value === value)) onSelect(value);
      close();
      return;
    }
    clearHold();
    if (wasPressed) cycle(); // quick click: next variant (single-variant slots re-select)
  };

  const onPointerCancel = () => {
    pressedRef.current = false;
    clearHold();
    if (flyout === "drag") close();
  };

  const onContextMenu = (e: React.MouseEvent) => {
    e.preventDefault();
    if (!hasVariants) return;
    clearHold();
    pressedRef.current = false;
    setFlyout("click");
    setHighlight(null);
  };

  const tooltip = hasVariants
    ? `${label}: ${activeVariant.label}${activeVariant.shortcut ? ` (${activeVariant.shortcut})` : ""} — click: next, hold: pick`
    : `${label}${activeVariant.shortcut ? ` (${activeVariant.shortcut})` : ""}`;

  return (
    <div className="toolslot" ref={rootRef}>
      <button
        className={`toolbar-btn${engaged ? " toolbar-btn--active" : ""}`}
        aria-label={tooltip}
        aria-haspopup={hasVariants ? "menu" : undefined}
        aria-expanded={hasVariants ? flyout !== "closed" : undefined}
        title={tooltip}
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={onPointerUp}
        onPointerCancel={onPointerCancel}
        onContextMenu={onContextMenu}
      >
        <Icon name={activeVariant.icon} size={16} />
        {hasVariants && <span className="toolslot-corner" aria-hidden="true" />}
      </button>
      {flyout !== "closed" && (
        <div className="toolslot-flyout" role="menu" aria-label={`${label} variants`}>
          {variants.map((v) => (
            <button
              key={v.value}
              role="menuitemradio"
              aria-checked={v.value === active}
              data-toolslot-value={v.value}
              className={`toolslot-row${v.value === active ? " toolslot-row--current" : ""}${
                v.value === highlight ? " toolslot-row--highlight" : ""
              }`}
              onClick={() => {
                if (flyout === "click") {
                  onSelect(v.value);
                  close();
                }
              }}
              onMouseEnter={() => {
                if (flyout === "click") setHighlight(v.value);
              }}
            >
              <span className="toolslot-current-dot" aria-hidden="true" />
              <Icon name={v.icon} size={14} />
              <span className="toolslot-row-label">{v.label}</span>
              {v.shortcut && <kbd className="toolslot-row-key">{v.shortcut}</kbd>}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
