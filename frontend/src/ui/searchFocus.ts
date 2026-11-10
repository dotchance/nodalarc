// Copyright 2024-2026 .chance (dotchance)
// SPDX-License-Identifier: AGPL-3.0-only
/**
 * Focus registry for the global '/' shortcut. The log window registers its
 * search input while mounted; the keyboard hook focuses it without knowing
 * where it lives (no class-name querySelector coupling).
 */

let target: HTMLInputElement | null = null;

export function setSearchTarget(el: HTMLInputElement | null): void {
  target = el;
}

export function focusSearchTarget(): boolean {
  if (target && target.isConnected) {
    target.focus();
    target.select();
    return true;
  }
  return false;
}
