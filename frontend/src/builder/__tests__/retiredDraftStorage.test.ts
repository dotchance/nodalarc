// Copyright 2024-2026 .chance (dotchance)
// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms under AGPL-3.0 Section 7 are in the NOTICE file.
import { beforeEach, describe, expect, it } from "vitest";
import { forgetRetiredBuilderDrafts } from "../retiredDraftStorage";

describe("a page load forgets the drafts a former Builder stored", () => {
  beforeEach(() => {
    localStorage.clear();
    sessionStorage.clear();
  });

  it("removes every recovery record and pointer, and nothing else", () => {
    localStorage.setItem("nodalarc-builder-recovery-v2:ctx:tab", "{}");
    localStorage.setItem("nodalarc-builder-draft", "{}");
    localStorage.setItem("nodalarc-builder-draft-previous", "{}");
    localStorage.setItem("nodalarc-builder-catalog-draft", "{}");
    localStorage.setItem("nodalarc-builder-opaque-yaml-draft", "{}");
    localStorage.setItem("nodal_panel_width", "420");
    sessionStorage.setItem("nodalarc-builder-recovery-v2:ctx:current:autosave", "key");
    sessionStorage.setItem("nodal_api_key", "secret");

    forgetRetiredBuilderDrafts([localStorage, sessionStorage]);

    expect(Object.keys({ ...localStorage })).toEqual(["nodal_panel_width"]);
    expect(Object.keys({ ...sessionStorage })).toEqual(["nodal_api_key"]);
  });
});
