// Copyright 2024-2026 .chance (dotchance)
// SPDX-License-Identifier: AGPL-3.0-only
/** useEditorWindows — the floating-editor windows.
 *
 *  A window that edits a session object edits its pending edit, which the
 *  page holds; the hook owns only the windows list. These tests cover the
 *  window keys, the pending edit each window edits, and the session gesture
 *  that closes the session's windows.
 */
import { describe, it, expect } from "vitest";
import { renderHook, act } from "@testing-library/react";
import {
  pendingEditAddress,
  pendingEditTarget,
  useEditorWindows,
  targetKey,
  type EditorTarget,
} from "../useEditorWindows";

describe("useEditorWindows", () => {
  it("a close removes the window", () => {
    const { result } = renderHook(() => useEditorWindows());
    const target: EditorTarget = { kind: "segment", id: "space-1" };
    act(() => result.current.openEditor(target));
    expect(result.current.isOpen(targetKey(target))).toBe(true);
    act(() => result.current.closeWindow(targetKey(target)));
    expect(result.current.isOpen(targetKey(target))).toBe(false);
  });

  it("a segment, link, domain or boundary list window edits the pending edit that opens it", () => {
    // Cancel discards the pending edit a window's address names, and a rail
    // entry for a pending edit opens the window that edits it: the two agree.
    const editing: EditorTarget[] = [
      { kind: "segment", id: "space-1" },
      { kind: "link", id: "leo-isl" },
      { kind: "domain", id: "leo-core" },
      { kind: "boundaries" },
    ];
    expect(editing.map((target) => pendingEditAddress(target))).toEqual([
      { kind: "member", list_pointer: "/segments", id: "space-1" },
      { kind: "member", list_pointer: "/link_rules", id: "leo-isl" },
      { kind: "member", list_pointer: "/routing/domains", id: "leo-core" },
      { kind: "whole_list", pointer: "/routing/boundaries" },
    ]);
    for (const target of editing) {
      expect(targetKey(pendingEditTarget(pendingEditAddress(target)!))).toBe(targetKey(target));
    }
    // The session window edits its sections in its own editor; the other
    // windows edit no pending edit.
    for (const target of [
      { kind: "session" },
      { kind: "new-ground" },
      { kind: "save-session" },
    ] as EditorTarget[]) {
      expect(pendingEditAddress(target)).toBeNull();
    }
  });

  it("a session gesture closes every window except the component draft", () => {
    const { result } = renderHook(() => useEditorWindows());
    const segment: EditorTarget = { kind: "segment", id: "space-1" };
    act(() => result.current.openEditor(segment));
    act(() => result.current.openEditor({ kind: "catalog" }));
    act(() => result.current.openEditor({ kind: "library" }));
    act(() => result.current.closeSessionWindows());
    expect(result.current.isOpen(targetKey(segment))).toBe(false);
    expect(result.current.isOpen(targetKey({ kind: "catalog" }))).toBe(false);
    expect(result.current.isOpen(targetKey({ kind: "library" }))).toBe(true);
  });

  it("windows are keyed by object identity: distinct objects → distinct windows; re-open refreshes in place", () => {
    const { result } = renderHook(() => useEditorWindows());
    const a: EditorTarget = { kind: "segment", id: "space-1" };
    const b: EditorTarget = { kind: "segment", id: "space-2" };
    act(() => result.current.openEditor(a));
    act(() => result.current.openEditor(b));
    expect(result.current.windows.map((w) => w.key)).toEqual([targetKey(a), targetKey(b)]);
    // Re-opening the same object refreshes it IN PLACE — never duplicates, and
    // never reorders the array: stacking is the raise stack's job, not React
    // position. The re-open focus is a raiseWindow call (pinned in
    // windowStack.test.ts), so the array order is untouched.
    act(() => result.current.openEditor({ ...a }));
    expect(result.current.windows.map((w) => w.key)).toEqual([targetKey(a), targetKey(b)]);
    expect(result.current.windows).toHaveLength(2);
  });
});
