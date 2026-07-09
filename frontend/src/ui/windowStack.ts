// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
/** The floating-window raise stack — ONE stacking mechanism for every
 *  operational window.
 *
 *  Before this, all windows shared a single z on the zWindow layer and stacked
 *  by DOM order, so a buried window could not be brought forward and the
 *  builder kept a second, drifting mechanism (an array reorder) to fake it.
 *  Here the z-order is a bounded RANK within the zWindow layer: a window's
 *  inline `--window-z-bump` is its index in this order, so its computed z is
 *  `zWindow + rank` and rank stays in [0, open-count). A raised window never
 *  climbs out of the window band (open-count is a handful; the next layer is
 *  far above), and there is no unbounded counter. A window rises to the top on
 *  pointerdown (FloatingWindow) and when its owner re-opens it (the builder's
 *  openEditor), both through raiseWindow — the one path.
 */
import { useSyncExternalStore } from "react";

// Window ids, bottom → top. Replaced (never mutated) so getSnapshot stays cheap.
let order: string[] = [];
const listeners = new Set<() => void>();
const emit = () => listeners.forEach((l) => l());

/** Register a window at the top of the stack (a freshly opened window is on
 *  top). Idempotent — re-registering the same id does not duplicate or reorder. */
export function addWindow(id: string): void {
  if (order.includes(id)) return;
  order = [...order, id];
  emit();
}

/** Drop a window from the stack on unmount, collapsing the ranks above it. */
export function removeWindow(id: string): void {
  if (!order.includes(id)) return;
  order = order.filter((x) => x !== id);
  emit();
}

/** Bring a window to the top. A no-op if it is unknown or already on top, so a
 *  pointerdown on the front window does not churn every sibling's rank. */
export function raiseWindow(id: string): void {
  const i = order.indexOf(id);
  if (i === -1 || i === order.length - 1) return;
  order = [...order.slice(0, i), ...order.slice(i + 1), id];
  emit();
}

/** A window's current rank (0 = bottom). -1 until it registers. Drives its
 *  `--window-z-bump`, so every window re-ranks when the order changes. */
export function useWindowRank(id: string): number {
  return useSyncExternalStore(
    (cb) => {
      listeners.add(cb);
      return () => listeners.delete(cb);
    },
    () => order.indexOf(id),
  );
}

/** Test-only: reset the module stack between cases. */
export function _resetWindowStack(): void {
  order = [];
}

/** Test-only: the current bottom→top order. */
export function _windowStackOrder(): readonly string[] {
  return order;
}
