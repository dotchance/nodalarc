// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
/** One browser-download implementation for every "save this to a file" site.
 *
 *  Each caller previously created a Blob URL, clicked a synthetic anchor, and
 *  revoked the URL — with the revoke placed inconsistently. Revoking
 *  synchronously right after click can cancel the download in some browsers
 *  before it has read the URL, so revocation is deferred to the next macrotask
 *  here, once, for everyone.
 */

/** Trigger a browser download of `content` as a file named `filename`. */
export function downloadBlob(
  content: BlobPart,
  filename: string,
  type = "text/yaml",
): void {
  const blob = new Blob([content], { type });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  // Defer the revoke: a synchronous revoke can cancel the click's download.
  setTimeout(() => URL.revokeObjectURL(url), 0);
}
