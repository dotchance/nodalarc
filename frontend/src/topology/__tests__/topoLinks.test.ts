// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
import { describe, expect, it } from "vitest";
import { TRACE_BRIDGED_COLOR, TRACE_FORWARD_COLOR, TRACE_REVERSE_COLOR, hexToCSS } from "../../config";
import type { LayoutNode } from "../layout";
import { drawLinks } from "../topoLinks";

/** A canvas context that records each stroke with the style it was drawn in. */
class RecordingContext {
  strokeStyle = "";
  lineWidth = 1;
  lineDashOffset = 0;
  globalAlpha = 1;
  strokes: { style: string; dash: number[]; alpha: number; from: [number, number] }[] = [];
  private dash: number[] = [];
  private from: [number, number] = [0, 0];

  setLineDash(dash: number[]) {
    this.dash = dash;
  }
  beginPath() {}
  moveTo(x: number, y: number) {
    this.from = [x, y];
  }
  lineTo() {}
  stroke() {
    this.strokes.push({
      style: this.strokeStyle,
      dash: this.dash,
      alpha: this.globalAlpha,
      from: this.from,
    });
  }
  save() {}
  restore() {
    this.globalAlpha = 1;
  }
}

function node(id: string, x: number): LayoutNode {
  return { id, label: id, x, y: 0, type: "ground_station", band: null, plane: null, slot: null };
}

describe("drawLinks traced path", () => {
  it("draws each leg's measured segments dashed in its color and bridged ones dotted in their own", () => {
    const nodeMap = new Map([
      ["gs-a", node("gs-a", 0)],
      ["sat-1", node("sat-1", 10)],
      ["gs-b", node("gs-b", 20)],
    ]);
    const ctx = new RecordingContext();

    drawLinks(
      ctx as unknown as CanvasRenderingContext2D,
      [],
      nodeMap,
      [
        {
          segments: [
            { from: "gs-a", to: "sat-1", measured: true },
            { from: "sat-1", to: "gs-b", measured: false },
          ],
          color: hexToCSS(TRACE_FORWARD_COLOR),
          opacity: 0.5,
          animate: false,
        },
        {
          segments: [{ from: "gs-b", to: "gs-a", measured: true }],
          color: hexToCSS(TRACE_REVERSE_COLOR),
          opacity: 0.5,
          animate: false,
        },
      ],
    );

    expect(ctx.strokes).toEqual([
      { style: hexToCSS(TRACE_FORWARD_COLOR), dash: [6, 3], alpha: 0.5, from: [0, 0] },
      { style: hexToCSS(TRACE_BRIDGED_COLOR), dash: [1, 5], alpha: 0.5, from: [10, 0] },
      { style: hexToCSS(TRACE_REVERSE_COLOR), dash: [6, 3], alpha: 0.5, from: [20, 0] },
    ]);
  });
});
