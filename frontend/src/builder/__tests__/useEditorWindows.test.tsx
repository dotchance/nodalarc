// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
/** useEditorWindows — the floating-editor windows + buffered-edit shell.
 *
 *  These tests cover the hook with useWorkspace
 *  (so applyBuffer's mutator actually commits and the reconciliation sees
 *  the applied object move): patchBuffer stages a dirty buffer and never
 *  touches the workspace; applyBuffer commits through the matching mutator and
 *  re-bases opened + clears dirty; revertBuffer returns the draft to opened; a
 *  user close discards the window and its buffer together; and previewWorkspace
 *  overlays a dirty session buffer (what the dwell readout reads). The pure
 *  buffer maths (overlayBuffers/staleBufferKeys/workspaceForSave) remain
 *  covered in useWorkspace.test.
 */
import { describe, it, expect, beforeEach, afterEach } from "vitest";
import { renderHook, act } from "@testing-library/react";
import {
  pendingEditAddress,
  pendingEditTarget,
  useEditorWindows,
  targetKey,
  type EditorTarget,
} from "../useEditorWindows";
import { useWorkspace } from "../useWorkspace";
import type { DraftConstellation } from "../workspace";
import {
  newDraftConstellation,
  newWorkspace,
} from "./fixtures/workspaceFixtures";

const SPACE_NODE = "nodalarc:nodes/space/x.yaml";

/** The real useWorkspace driving useEditorWindows — the exact composition
 *  BuilderView wires, so applyBuffer commits into a live workspace. */
function useHarness() {
  const ws = useWorkspace();
  const editor = useEditorWindows({
    workspace: ws.workspace,
    updateSession: ws.updateSession,
    updateConstellation: ws.updateConstellation,
    updateGroundDraft: ws.updateGroundDraft,
  });
  return { ws, editor };
}

/** A harness holding one constellation, so a segment window/buffer is not
 *  pruned by the reconciliation pass (its applied object exists). */
function withSegment() {
  const { result } = renderHook(() => useHarness());
  const workspace = newWorkspace("t");
  workspace.space.push(newDraftConstellation(SPACE_NODE));
  act(() => result.current.ws.openWorkspace(workspace));
  const draft = result.current.ws.workspace!.space[0]!;
  return { result, segmentId: draft.segment_id };
}

beforeEach(() => localStorage.clear());
afterEach(() => localStorage.clear());

describe("useEditorWindows — buffered editing", () => {
  it("patchBuffer stages a dirty buffer and never touches the workspace", () => {
    const { result, segmentId } = withSegment();
    const key = targetKey({ kind: "segment", id: segmentId });
    const applied = result.current.ws.workspace!.space[0]!;
    act(() =>
      result.current.editor.patchBuffer(key, applied, (d) => ({ ...d, display_name: "renamed" })),
    );
    expect(result.current.editor.buffers[key]?.dirty).toBe(true);
    expect((result.current.editor.buffers[key]?.draft as DraftConstellation).display_name).toBe(
      "renamed",
    );
    expect(result.current.editor.dirtyWindows).toBe(1);
    // Staging only — the applied workspace is untouched.
    expect(result.current.ws.workspace!.space[0]!.display_name).not.toBe("renamed");
  });

  it("applyBuffer commits through the matching mutator and re-bases opened + clears dirty", () => {
    const { result, segmentId } = withSegment();
    const target: EditorTarget = { kind: "segment", id: segmentId };
    const key = targetKey(target);
    const applied = result.current.ws.workspace!.space[0]!;
    act(() =>
      result.current.editor.patchBuffer(key, applied, (d) => ({ ...d, display_name: "renamed" })),
    );
    act(() => result.current.editor.applyBuffer(target));
    // The applied workspace now carries the edit (committed through updateConstellation).
    expect(result.current.ws.workspace!.space[0]!.display_name).toBe("renamed");
    // opened advanced to the applied draft; dirty cleared; the buffer is kept
    // (applied now matches, so reconciliation does not drop it).
    expect(result.current.editor.buffers[key]?.dirty).toBe(false);
    expect((result.current.editor.buffers[key]?.opened as DraftConstellation).display_name).toBe(
      "renamed",
    );
    expect(result.current.editor.dirtyWindows).toBe(0);
  });

  it("revertBuffer returns the draft to opened (Defaults)", () => {
    const { result, segmentId } = withSegment();
    const key = targetKey({ kind: "segment", id: segmentId });
    const applied = result.current.ws.workspace!.space[0]!;
    const originalName = applied.display_name;
    act(() =>
      result.current.editor.patchBuffer(key, applied, (d) => ({ ...d, display_name: "renamed" })),
    );
    act(() => result.current.editor.revertBuffer(key));
    expect((result.current.editor.buffers[key]?.draft as DraftConstellation).display_name).toBe(
      originalName,
    );
  });

  it("a user close discards the window and its buffer together", () => {
    const { result, segmentId } = withSegment();
    const target: EditorTarget = { kind: "segment", id: segmentId };
    const key = targetKey(target);
    const applied = result.current.ws.workspace!.space[0]!;
    act(() => result.current.editor.openEditor(target));
    act(() =>
      result.current.editor.patchBuffer(key, applied, (d) => ({ ...d, display_name: "renamed" })),
    );
    expect(result.current.editor.isOpen(key)).toBe(true);
    expect(result.current.editor.buffers[key]).toBeDefined();
    act(() => result.current.editor.closeWindow(key));
    expect(result.current.editor.isOpen(key)).toBe(false);
    expect(result.current.editor.buffers[key]).toBeUndefined();
  });

  it("keeps a link, domain or boundary list window whose object the workspace does not hold", () => {
    // These windows show objects the backend lists, which the workspace may
    // not hold. A segment window still edits the workspace, so it closes with
    // its object.
    const { result } = withSegment();
    const link: EditorTarget = { kind: "link", id: "relay-rule" };
    const domain: EditorTarget = { kind: "domain", id: "relay-domain" };
    const boundaries: EditorTarget = { kind: "boundaries" };
    const segment: EditorTarget = { kind: "segment", id: "no-such-segment" };
    for (const target of [link, domain, boundaries, segment]) {
      act(() => result.current.editor.openEditor(target));
    }
    expect(result.current.editor.isOpen(targetKey(link))).toBe(true);
    expect(result.current.editor.isOpen(targetKey(domain))).toBe(true);
    expect(result.current.editor.isOpen(targetKey(boundaries))).toBe(true);
    expect(result.current.editor.isOpen(targetKey(segment))).toBe(false);
  });

  it("a link, domain or boundary list window edits the pending edit that opens it", () => {
    // Cancel discards the pending edit a window's address names, and a rail
    // entry for a pending edit opens the window that edits it: the two agree.
    const editing: EditorTarget[] = [
      { kind: "link", id: "leo-isl" },
      { kind: "domain", id: "leo-core" },
      { kind: "boundaries" },
    ];
    expect(editing.map((target) => pendingEditAddress(target))).toEqual([
      { kind: "member", list_pointer: "/link_rules", id: "leo-isl" },
      { kind: "member", list_pointer: "/routing/domains", id: "leo-core" },
      { kind: "whole_list", pointer: "/routing/boundaries" },
    ]);
    for (const target of editing) {
      expect(targetKey(pendingEditTarget(pendingEditAddress(target)!))).toBe(targetKey(target));
    }
    // A window that edits the twin or shows a view edits no pending edit.
    for (const target of [
      { kind: "session" },
      { kind: "segment", id: "leo" },
      { kind: "ground", id: "gw" },
      { kind: "save-session" },
    ] as EditorTarget[]) {
      expect(pendingEditAddress(target)).toBeNull();
    }
  });

  it("a session gesture closes every window except the component draft", () => {
    const { result, segmentId } = withSegment();
    const segment = targetKey({ kind: "segment", id: segmentId });
    const library = targetKey({ kind: "library" });
    const catalog = targetKey({ kind: "catalog" });
    act(() => result.current.editor.openEditor({ kind: "segment", id: segmentId }));
    act(() => result.current.editor.openEditor({ kind: "catalog" }));
    act(() => result.current.editor.openEditor({ kind: "library" }));
    act(() => result.current.editor.closeSessionWindows());
    expect(result.current.editor.isOpen(segment)).toBe(false);
    expect(result.current.editor.isOpen(catalog)).toBe(false);
    expect(result.current.editor.isOpen(library)).toBe(true);
  });

  it("exposes a synchronous mutation fence for asynchronous backend commands", () => {
    const { result, segmentId } = withSegment();
    const target: EditorTarget = { kind: "segment", id: segmentId };
    const key = targetKey(target);
    const applied = result.current.ws.workspace!.space[0]!;
    act(() => result.current.editor.openEditor(target));
    const before = result.current.editor.currentBufferMutationRevision();
    act(() =>
      result.current.editor.patchBuffer(key, applied, (draft) => ({
        ...draft,
        display_name: "changed while awaiting VS-API",
      })),
    );
    const afterEdit = result.current.editor.currentBufferMutationRevision();
    expect(afterEdit).toBeGreaterThan(before);
    act(() => result.current.editor.closeWindow(key));
    expect(result.current.editor.currentBufferMutationRevision()).toBeGreaterThan(afterEdit);
  });

  it("previewWorkspace overlays a dirty session buffer (what the dwell readout reads)", () => {
    const { result } = renderHook(() => useHarness());
    act(() => result.current.ws.openWorkspace(newWorkspace("t")));
    const future = "2030-01-01T00:00:00+00:00";
    const start = result.current.ws.workspace!.start_time;
    act(() =>
      result.current.editor.patchBuffer("session", { start_time: start }, (d) => ({
        ...d,
        start_time: future,
      })),
    );
    // The preview reflects the dirty session edit; the applied workspace does not.
    expect(result.current.editor.previewWorkspace()?.start_time).toBe(future);
    expect(result.current.ws.workspace!.start_time).not.toBe(future);
  });

  it(" windows are keyed by object identity: distinct objects → distinct windows; re-open refreshes in place", () => {
    const { result } = renderHook(() => useHarness());
    const workspace = newWorkspace("t");
    // Two existing segments keep reconciliation from pruning their windows.
    workspace.space.push(
      newDraftConstellation(SPACE_NODE),
      newDraftConstellation(SPACE_NODE),
    );
    act(() => result.current.ws.openWorkspace(workspace));
    const [s0, s1] = result.current.ws.workspace!.space;
    const a: EditorTarget = { kind: "segment", id: s0!.segment_id };
    const b: EditorTarget = { kind: "segment", id: s1!.segment_id };
    // Two different objects open two windows, keyed by their object id.
    act(() => result.current.editor.openEditor(a));
    act(() => result.current.editor.openEditor(b));
    expect(result.current.editor.windows.map((w) => w.key)).toEqual([targetKey(a), targetKey(b)]);
    // Re-opening the same object refreshes it IN PLACE — never duplicates, and
    // never reorders the array: stacking is the raise stack's job now, not
    // React position. The re-open focus is a raiseWindow call (pinned in
    // windowStack.test.ts), so the array order is untouched.
    act(() => result.current.editor.openEditor({ ...a, id: s0!.segment_id }));
    expect(result.current.editor.windows.map((w) => w.key)).toEqual([targetKey(a), targetKey(b)]);
    expect(result.current.editor.windows).toHaveLength(2);
  });
});
