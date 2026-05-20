// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render } from "@testing-library/react";

vi.mock("../../config", () => ({
  REST_URL: "http://test:8080",
  getApiKey: () => "",
}));

const mockTerminal = vi.hoisted(() => ({
  loadAddon: vi.fn(),
  open: vi.fn(),
  write: vi.fn(),
  clear: vi.fn(),
  reset: vi.fn(),
  scrollToBottom: vi.fn(),
  onData: vi.fn(() => ({ dispose: vi.fn() })),
  focus: vi.fn(),
  dispose: vi.fn(),
  options: {},
}));

vi.mock("@xterm/xterm", () => ({
  Terminal: vi.fn(function Terminal() {
    return mockTerminal;
  }),
}));

vi.mock("@xterm/addon-fit", () => ({
  FitAddon: vi.fn(function FitAddon() {
    return {
      fit: vi.fn(),
      proposeDimensions: vi.fn(() => ({ cols: 80, rows: 24 })),
    };
  }),
}));

vi.mock("@xterm/addon-web-links", () => ({
  WebLinksAddon: vi.fn(function WebLinksAddon() {
    return {};
  }),
}));

class MockWebSocket {
  static OPEN = 1;

  url: string;
  readyState = MockWebSocket.OPEN;
  sent: string[] = [];
  onopen: ((ev: Event) => void) | null = null;
  onmessage: ((ev: MessageEvent) => void) | null = null;
  onclose: ((ev: CloseEvent) => void) | null = null;
  onerror: ((ev: Event) => void) | null = null;

  constructor(url: string) {
    this.url = url;
    wsInstances.push(this);
  }

  send(data: string) {
    this.sent.push(data);
  }

  close() {
    this.readyState = 3;
    this.onclose?.({ code: 1000 } as CloseEvent);
  }
}

let wsInstances: MockWebSocket[] = [];

class MockResizeObserver {
  observe() {}
  disconnect() {}
}

const { PersistentTerminal } = await import("../PersistentTerminal");

describe("PersistentTerminal", () => {
  beforeEach(() => {
    wsInstances = [];
    vi.stubGlobal("WebSocket", MockWebSocket);
    vi.stubGlobal("ResizeObserver", MockResizeObserver);
  });

  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
  });

  it("reconnects open node terminals when the runtime session changes", () => {
    const onSessionStatusChange = vi.fn();
    const props = {
      sessions: ["sat-P00S00"],
      activeNodeId: "sat-P00S00",
      onSessionStatusChange,
      fontSize: 12,
    };

    const { rerender } = render(
      <PersistentTerminal {...props} runtimeSessionId="run-old" />,
    );

    expect(wsInstances).toHaveLength(1);
    const oldSocket = wsInstances[0]!;

    rerender(<PersistentTerminal {...props} runtimeSessionId="run-new" />);

    expect(oldSocket.readyState).toBe(3);
    expect(wsInstances).toHaveLength(2);
    expect(wsInstances[1]!.url).toBe("ws://test:8080/ws/v1/terminal/sat-P00S00");
  });
});
