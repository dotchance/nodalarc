// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { TimeControls } from "../TimeControls";

const START = "2026-01-01T00:00:00.000Z";
const END = "2026-01-01T01:00:00.000Z";

describe("TimeControls", () => {
  beforeEach(() => {
    vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue({
      clearRect: vi.fn(),
      fillRect: vi.fn(),
      fillStyle: "",
    } as unknown as CanvasRenderingContext2D);
  });

  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  it("starts at the end of the historical window so skip-back seeks backward", async () => {
    const onSeek = vi.fn();
    render(<TimeControls onSeek={onSeek} startTime={START} endTime={END} />);

    expect(document.querySelector<HTMLElement>(".time-scrubber-thumb")?.style.left).toBe("100%");

    fireEvent.click(screen.getByTitle("Skip back 5min"));

    expect(onSeek).toHaveBeenCalledWith("2026-01-01T00:55:00.000Z");
    await waitFor(() => {
      expect(document.querySelector<HTMLElement>(".time-scrubber-thumb")?.style.left).toBe("91.66666666666666%");
    });
  });

  it("keeps speed controls visibly stateful", () => {
    render(<TimeControls onSeek={vi.fn()} startTime={START} endTime={END} />);

    expect(screen.getByDisplayValue("1x")).toBeTruthy();
    fireEvent.click(screen.getByTitle("Slower"));
    expect(screen.getByDisplayValue("0.5x")).toBeTruthy();
    fireEvent.click(screen.getByTitle("Faster"));
    expect(screen.getByDisplayValue("1x")).toBeTruthy();
  });

  it("resets to the new end when the historical window changes", async () => {
    const onSeek = vi.fn();
    const { rerender } = render(<TimeControls onSeek={onSeek} startTime={START} endTime={END} />);
    fireEvent.click(screen.getByTitle("Skip back 5min"));
    await waitFor(() => {
      expect(document.querySelector<HTMLElement>(".time-scrubber-thumb")?.style.left).not.toBe("100%");
    });

    rerender(
      <TimeControls
        onSeek={onSeek}
        startTime="2026-01-01T01:00:00.000Z"
        endTime="2026-01-01T02:00:00.000Z"
      />,
    );

    expect(document.querySelector<HTMLElement>(".time-scrubber-thumb")?.style.left).toBe("100%");
  });
});
