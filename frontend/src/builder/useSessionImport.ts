// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
/** The builder's session entry/import state machine (M19).
 *
 *  Entering the builder beside a running cluster loads that session; the
 *  picker opens any listed session. This hook owns the in-flight import
 *  (importPending), the refused-import notice (importIssues), and the
 *  provenance of the adopted workspace (importedFrom). It preserves every
 *  subtle rule of the original: the running-session auto-import is entry-scoped
 *  (not replayed on a hide/show toggle); a displaced draft is stashed BEFORE
 *  adoption (the `displace` dep owns the refuse/overwrite choice); a workspace
 *  the user started while a load was in flight WINS (theirs is not overwritten);
 *  and a load that ends without its document clears importPending so the Open
 *  button never stays stuck on "Opening…".
 */
import { useEffect, useState } from "react";
import { workspaceFromSessionDocument, type Workspace } from "./workspace";
import type { BuilderSessionListEntry } from "./builderTypes";

// The running-session auto-import is entry-scoped, not mount-scoped: keyed on
// this module marker so a hide/show toggle does not replay the import.
let _importTriedFile: string | null = null;

interface UseSessionImportDeps {
  active: boolean;
  workspace: Workspace | null;
  runningSession: BuilderSessionListEntry | null;
  loadedDocument: Record<string, unknown> | null;
  loadedFile: string | null;
  loading: boolean;
  loadSession: (file: string) => void;
  /** Run a displacing gesture, stashing the current draft to the backup slot
   *  first (owns the refuse/overwrite choice). */
  displace: (proceed: () => void, label: string) => void;
  openWorkspace: (imported: Workspace) => void;
}

export function useSessionImport({
  active,
  workspace,
  runningSession,
  loadedDocument,
  loadedFile,
  loading,
  loadSession,
  displace,
  openWorkspace,
}: UseSessionImportDeps) {
  const [importPending, setImportPending] = useState<BuilderSessionListEntry | null>(null);
  // A refused import names the session the user actually opened — the
  // running session is not the only thing the picker can open.
  const [importIssues, setImportIssues] = useState<{ name: string; issues: string[] } | null>(
    null,
  );
  // The file the current workspace was imported from (provenance marker).
  const [importedFrom, setImportedFrom] = useState<string | null>(null);

  const startImport = (entry: BuilderSessionListEntry) => {
    setImportIssues(null);
    setImportPending(entry);
    loadSession(entry.file);
  };
  /** Clear the import provenance + notice — the teardown gestures (New, Open,
   *  Restore) call this on the way out. */
  const reset = () => {
    setImportedFrom(null);
    setImportIssues(null);
  };

  useEffect(() => {
    // The running session always loads on ENTRY — that is what entering the
    // builder beside a running cluster means. A browser draft never silently
    // stands in for it (a stale draft wearing the running session's name
    // showed an empty world while thirty nodes ran); a displaced draft is
    // preserved to the backup slot and restorable below. Gated on `active` so
    // a hidden builder is never a background importer, and keyed on the
    // module-scope marker so a hide/show toggle does not replay the import.
    if (!active || workspace || importPending || !runningSession) return;
    if (_importTriedFile === runningSession.file) return;
    _importTriedFile = runningSession.file;
    startImport(runningSession);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [active, workspace, importPending, runningSession]);

  useEffect(() => {
    if (!importPending || loadedDocument === null || loadedFile !== importPending.file) return;
    if (workspace) {
      // The user started something while the load was in flight — theirs wins.
      setImportPending(null);
      return;
    }
    const result = workspaceFromSessionDocument(loadedDocument);
    if (result.workspace) {
      // Preserve any displaced draft before adoption; if that stash is
      // refused, the choice dialog holds the adoption (the world stays on
      // screen read-only meanwhile — never a silently lossy workspace).
      const imported = result.workspace;
      const entry = importPending;
      displace(() => {
        openWorkspace(imported);
        setImportedFrom(entry.file);
      }, `loading ${entry.name}`);
    } else {
      // The world/YAML stay on screen read-only; the note says why the
      // session cannot be edited — never a silently lossy workspace.
      setImportIssues({ name: importPending.name, issues: result.issues });
    }
    setImportPending(null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [importPending, loadedDocument, loadedFile, workspace]);

  useEffect(() => {
    // The import must end with its resolve: a failed fetch or a competing
    // action (clear/+ New discards the in-flight response) would otherwise
    // leave "Loading…" claimed forever — a false in-progress display — and
    // permanently disable the edit-running path for this mount.
    if (!importPending || loading) return;
    if (loadedDocument !== null && loadedFile === importPending.file) return;
    setImportPending(null);
  }, [importPending, loading, loadedDocument, loadedFile]);

  return { importPending, importIssues, importedFrom, startImport, reset };
}
