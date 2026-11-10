// Copyright 2024-2026 .chance (dotchance)
// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms under AGPL-3.0 Section 7 are in the NOTICE file.
/** Browser storage a former Builder wrote to recover unsaved drafts. The Builder
 *  keeps no draft in browser storage now; a page load removes what an older
 *  build left, without reading it, so it stops filling the storage the page's
 *  preferences share. */

const RECOVERY_KEY_PREFIX = "nodalarc-builder-recovery-v2:";
const FIXED_KEYS = new Set([
  "nodalarc-builder-draft",
  "nodalarc-builder-draft-previous",
  "nodalarc-builder-catalog-draft",
  "nodalarc-builder-opaque-yaml-draft",
]);

export function forgetRetiredBuilderDrafts(storages: ReadonlyArray<Storage>): void {
  for (const storage of storages) {
    const retired: string[] = [];
    for (let index = 0; index < storage.length; index += 1) {
      const key = storage.key(index);
      if (key !== null && (key.startsWith(RECOVERY_KEY_PREFIX) || FIXED_KEYS.has(key))) {
        retired.push(key);
      }
    }
    for (const key of retired) storage.removeItem(key);
  }
}
