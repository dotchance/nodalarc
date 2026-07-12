// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
/** downloadBlob clicks a synthetic anchor and DEFERS revocation — a synchronous
 *  revoke can cancel the click's download in some browsers. */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { downloadBlob } from "../downloadBlob";

describe("downloadBlob", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.stubGlobal("URL", {
      createObjectURL: vi.fn(() => "blob:fake"),
      revokeObjectURL: vi.fn(),
    });
  });
  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("clicks a download anchor and defers revocation past the click", () => {
    const click = vi.fn();
    const anchor = { href: "", download: "", click } as unknown as HTMLAnchorElement;
    const createElement = vi.spyOn(document, "createElement").mockReturnValue(anchor);

    downloadBlob("hello: world", "s.yaml");

    expect(createElement).toHaveBeenCalledWith("a");
    expect(anchor.download).toBe("s.yaml");
    expect(anchor.href).toBe("blob:fake");
    expect(click).toHaveBeenCalledOnce();
    // The revoke must NOT have run synchronously with the click.
    expect(URL.revokeObjectURL).not.toHaveBeenCalled();

    vi.runAllTimers();
    expect(URL.revokeObjectURL).toHaveBeenCalledWith("blob:fake");
  });

  it("passes the given MIME type through to the Blob", () => {
    const anchor = { href: "", download: "", click: vi.fn() } as unknown as HTMLAnchorElement;
    vi.spyOn(document, "createElement").mockReturnValue(anchor);

    downloadBlob("x", "n.conf", "text/plain");

    const createObjectURL = URL.createObjectURL as unknown as ReturnType<typeof vi.fn>;
    const blobArg = createObjectURL.mock.calls[0]![0] as Blob;
    expect(blobArg.type).toBe("text/plain");
  });

  it("defaults to text/yaml", () => {
    const anchor = { href: "", download: "", click: vi.fn() } as unknown as HTMLAnchorElement;
    vi.spyOn(document, "createElement").mockReturnValue(anchor);

    downloadBlob("a: b", "s.yaml");

    const createObjectURL = URL.createObjectURL as unknown as ReturnType<typeof vi.fn>;
    const blobArg = createObjectURL.mock.calls[0]![0] as Blob;
    expect(blobArg.type).toBe("text/yaml");
  });
});
