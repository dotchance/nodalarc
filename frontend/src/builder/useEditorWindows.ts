// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
/** The builder's floating-editor windows.
 *
 *  Extracted from BuilderView: the diagram is a set of floating, per-object
 *  editor windows. A window that edits a session object edits its pending
 *  edit, which the page holds; this hook owns only the windows list.
 */

import { useState } from "react";
import { raiseWindow } from "../ui/windowStack";
import type {
  BuilderContentEdit,
  BuilderVisualObjectEditRequest,
} from "./generated/builderApi";

/** A floating editor window's target — one per object, keyed. */
export type EditorTarget =
  | { kind: "session" }
  | { kind: "segment"; id: string }
  | { kind: "new-ground" }
  | { kind: "link"; id: string }
  | { kind: "domain"; id: string }
  | { kind: "boundaries" }
  | { kind: "inspect"; ref: string; document: Record<string, unknown> }
  | { kind: "node-view"; nodeId: string }
  | { kind: "library" }
  | { kind: "catalog" }
  | { kind: "open-session" }
  | { kind: "source-yaml" }
  | { kind: "customize-chain"; segmentId: string; rootRef: string }
  | { kind: "proposal"; ref: string }
  | { kind: "save-session" };

export interface EditorWindow {
  key: string;
  target: EditorTarget;
  x: number;
  y: number;
}

export function targetKey(target: EditorTarget): string {
  switch (target.kind) {
    case "session":
    case "library":
    case "catalog":
    case "open-session":
    case "source-yaml":
    case "save-session":
    case "boundaries":
    case "new-ground":
      return target.kind;
    case "customize-chain":
      return `customize:${target.segmentId}`;
    case "inspect":
      return `inspect:${target.ref}`;
    case "node-view":
      return `node:${target.nodeId}`;
    case "proposal":
      return `proposal:${target.ref}`;
    default:
      return `${target.kind}:${target.id}`;
  }
}

/** The address of the pending edit a segment, link rule, routing domain or
 *  boundary list window edits; null for every other window. */
export function pendingEditAddress(
  target: EditorTarget,
): BuilderVisualObjectEditRequest["address"] | null {
  switch (target.kind) {
    case "segment":
      return { kind: "member", list_pointer: "/segments", id: target.id };
    case "link":
      return { kind: "member", list_pointer: "/link_rules", id: target.id };
    case "domain":
      return { kind: "member", list_pointer: "/routing/domains", id: target.id };
    case "boundaries":
      return { kind: "whole_list", pointer: "/routing/boundaries" };
    default:
      return null;
  }
}

/** The window that edits the pending edit at `address`. */
export function pendingEditTarget(address: BuilderContentEdit["address"]): EditorTarget {
  if (address.kind === "whole_list") return { kind: "boundaries" };
  if (address.kind === "proposal") return { kind: "proposal", ref: address.ref };
  if (address.kind === "section") return { kind: "session" };
  if (address.list_pointer === "/segments") return { kind: "segment", id: address.id };
  return address.list_pointer === "/link_rules"
    ? { kind: "link", id: address.id }
    : { kind: "domain", id: address.id };
}

/** A pending edit's object as notices name it: its id, the boundary list, or
 *  the proposed document's file name. */
export function pendingEditTitle(address: BuilderContentEdit["address"]): string {
  if (address.kind === "whole_list") return "Boundaries";
  if (address.kind === "proposal") {
    return address.ref.slice(address.ref.lastIndexOf("/") + 1).replace(/\.ya?ml$/, "");
  }
  if (address.kind === "section") return address.pointer.slice(1);
  return address.id;
}

export function useEditorWindows() {
  // Editors are floating, anchored windows — many can be open at once, keyed
  // per object (re-open focuses).
  const [windows, setWindows] = useState<EditorWindow[]>([]);
  const openEditor = (target: EditorTarget) => {
    const key = targetKey(target);
    // Re-open FOCUSES via the one stacking mechanism — the raise stack,
    // not an array reorder. The side effect stays outside the updater (StrictMode
    // double-invokes updater bodies); a fresh window rises on mount instead.
    if (windows.some((w) => w.key === key)) raiseWindow(key);
    setWindows((prev) => {
      const existing = prev.find((w) => w.key === key);
      if (existing) {
        // Refresh the target payload IN PLACE — array order no longer decides
        // stacking, so the window keeps its slot and the raise stack owns z.
        return prev.map((w) => (w.key === key ? { ...w, target } : w));
      }
      const n = prev.length;
      // Spawn cascade: monotonic and clamped (the old %6 repeated exactly every
      // sixth window); a far-out window is pulled back so it stays on screen.
      const step = Math.min(n, 8);
      return [...prev, { key, target, x: 440 + step * 40, y: 84 + step * 32 }];
    });
  };
  const closeWindows = (predicate: (w: EditorWindow) => boolean) => {
    const closing = new Set(windows.filter(predicate).map((w) => w.key));
    if (closing.size === 0) return;
    setWindows((prev) => prev.filter((w) => !closing.has(w.key)));
  };
  const closeWindow = (key: string) => closeWindows((w) => w.key === key);
  /** Close every window a session gesture (New, Open) replaces. The
   *  component draft window is independent of sessions and holds its own
   *  unsaved work, so only its own gestures close it. */
  const closeSessionWindows = () => closeWindows((w) => w.target.kind !== "library");
  const isOpen = (key: string) => windows.some((w) => w.key === key);
  return {
    windows,
    openEditor,
    closeWindow,
    closeSessionWindows,
    isOpen,
  };
}
