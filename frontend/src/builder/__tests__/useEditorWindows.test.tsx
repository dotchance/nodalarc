// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
/** useEditorWindows — the floating-editor windows + buffered-edit shell (M18).
 *
 *  These pins cover the HOOK behavior end-to-end against the REAL useWorkspace
 *  (so applyBuffer's mutator actually commits and the M5 reconciliation sees
 *  the applied object move): patchBuffer stages a dirty buffer and never
 *  touches the workspace; applyBuffer commits through the matching mutator and
 *  re-bases opened + clears dirty; revertBuffer returns the draft to opened; a
 *  user close discards the window and its buffer together; and previewWorkspace
 *  overlays a dirty session buffer (what the N28 dwell readout reads). The pure
 *  buffer maths (overlayBuffers/staleBufferKeys/workspaceForSave) keep their
 *  landed P0c pins in useWorkspace.test — not re-asserted here.
 */
import { describe, it, expect, beforeEach, afterEach } from "vitest";
import { renderHook, act } from "@testing-library/react";
import { useEditorWindows, targetKey, type EditorTarget } from "../useEditorWindows";
import { useWorkspace } from "../useWorkspace";
import { type DraftConstellation } from "../workspace";

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
    updateLinkRule: ws.updateLinkRule,
    updateRoutingDomain: ws.updateRoutingDomain,
    updateBoundary: ws.updateBoundary,
    convergeGroundToRef: ws.convergeGroundToRef,
  });
  return { ws, editor };
}

/** A harness holding one constellation, so a segment window/buffer is not
 *  pruned by the reconciliation pass (its applied object exists). */
function withSegment() {
  const { result } = renderHook(() => useHarness());
  act(() => result.current.ws.startNew("t"));
  act(() => result.current.ws.addConstellation(SPACE_NODE));
  const draft = result.current.ws.workspace!.space[0]!;
  return { result, segmentId: draft.segment_id };
}

beforeEach(() => localStorage.clear());
afterEach(() => localStorage.clear());

describe("useEditorWindows — buffered editing (M18)", () => {
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

  it("N28: previewWorkspace overlays a dirty session buffer (what the dwell readout reads)", () => {
    const { result } = renderHook(() => useHarness());
    act(() => result.current.ws.startNew("t"));
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
});
