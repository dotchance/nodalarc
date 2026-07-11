// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
/** Typed catalog session picker and exact-closure transfer surface. */

import { useState, type ChangeEvent } from "react";
import { Button, IconButton } from "../ui/Button";
import { Icon } from "../ui/icons/Icon";
import { Field } from "./editorKit";
import type {
  CatalogDocumentSummary,
  CatalogImportResult,
} from "./generated/builderApi";

interface OpenSessionPickerProps {
  sessions: readonly CatalogDocumentSummary[];
  sessionsError: string | null;
  openError: string | null;
  onOpen: (entry: CatalogDocumentSummary, targetRef?: string) => void;
  onExport: (entry: CatalogDocumentSummary) => Promise<void>;
  onImport: (payload: unknown, commit: boolean) => Promise<CatalogImportResult>;
}

export function OpenSessionPicker({
  sessions,
  sessionsError,
  openError,
  onOpen,
  onExport,
  onImport,
}: OpenSessionPickerProps) {
  const [transferPayload, setTransferPayload] = useState<unknown>(null);
  const [transferResult, setTransferResult] = useState<CatalogImportResult | null>(null);
  const [transferError, setTransferError] = useState<string | null>(null);
  const [transferring, setTransferring] = useState(false);
  const [exportingRef, setExportingRef] = useState<string | null>(null);
  const [shippedDraft, setShippedDraft] = useState<{
    entry: CatalogDocumentSummary;
    targetId: string;
  } | null>(null);

  const yours = sessions.filter((entry) => entry.namespace === "user");
  const shipped = sessions.filter((entry) => entry.namespace === "nodalarc");

  const nextCopyId = (entry: CatalogDocumentSummary): string => {
    const sourceId = (entry.ref.split("/").pop() ?? entry.ref).replace(/\.ya?ml$/, "");
    const base = `${sourceId}-copy`;
    const occupied = new Set(
      yours.map((session) => (session.ref.split("/").pop() ?? session.ref).replace(/\.ya?ml$/, "")),
    );
    if (!occupied.has(base)) return base;
    let suffix = 2;
    while (occupied.has(`${base}-${suffix}`)) suffix += 1;
    return `${base}-${suffix}`;
  };

  const proposeImport = async (event: ChangeEvent<HTMLInputElement>) => {
    const file = event.currentTarget.files?.[0];
    event.currentTarget.value = "";
    if (!file) return;
    setTransferring(true);
    setTransferResult(null);
    setTransferError(null);
    try {
      const payload: unknown = JSON.parse(await file.text());
      const result = await onImport(payload, false);
      setTransferPayload(payload);
      setTransferResult(result);
    } catch (cause) {
      setTransferPayload(null);
      setTransferError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setTransferring(false);
    }
  };

  const commitImport = async () => {
    if (transferPayload === null) return;
    setTransferring(true);
    setTransferError(null);
    try {
      setTransferResult(await onImport(transferPayload, true));
    } catch (cause) {
      setTransferError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setTransferring(false);
    }
  };

  const group = (label: string, entries: readonly CatalogDocumentSummary[]) =>
    entries.length === 0 ? null : (
      <div className="builder-picker-group" key={label}>
        <div className="builder-outline-kind">{label}</div>
        {entries.map((entry) => (
          <div className="builder-library-entry" key={entry.ref}>
            <button
              className="builder-library-entry-main"
              onClick={() => {
                if (entry.namespace === "user") onOpen(entry);
                else setShippedDraft({ entry, targetId: nextCopyId(entry) });
              }}
              title={`Open ${entry.display_name}`}
            >
              <span className="builder-outline-name builder-library-entry-name">
                <Icon name="folder-open" size={12} />
                <span className="builder-library-entry-text">{entry.display_name}</span>
              </span>
              {entry.summary && (
                <span className="builder-library-entry-summary">{entry.summary}</span>
              )}
            </button>
            <span className="builder-library-actions">
              <IconButton
                icon="download"
                size={12}
                disabled={exportingRef === entry.ref}
                label="Export this session and its exact YAML closure"
                onClick={() => {
                  setExportingRef(entry.ref);
                  setTransferError(null);
                  void onExport(entry).then(
                    () => setExportingRef(null),
                    (cause) => {
                      setExportingRef(null);
                      setTransferError(cause instanceof Error ? cause.message : String(cause));
                    },
                  );
                }}
              />
            </span>
          </div>
        ))}
      </div>
    );

  return (
    <div className="builder-picker" data-testid="builder-open-picker">
      <div className="builder-preset-row">
        <label className="builder-import-label">
          {transferring ? "checking closure…" : "import exact YAML closure"}
          <input
            hidden
            type="file"
            accept="application/json,.json,.nodalarc-session.json"
            disabled={transferring}
            onChange={(event) => void proposeImport(event)}
          />
        </label>
      </div>
      {transferResult?.outcome === "proposed" && (
        <div className="builder-warning" data-testid="session-import-proposed">
          Backend validated {transferResult.proposed_writes.length} new YAML file
          {transferResult.proposed_writes.length === 1 ? "" : "s"}.
          <Button variant="primary" disabled={transferring} onClick={() => void commitImport()}>
            Import atomically
          </Button>
        </div>
      )}
      {transferResult?.outcome === "blocked" && (
        <div className="builder-warning" data-testid="session-import-blocked">
          Import blocked by the backend: {transferResult.collisions.map((item) => `${item.ref} (${item.reason})`).join(", ")}
        </div>
      )}
      {transferResult?.outcome === "unchanged" && (
        <div className="builder-zone-empty">Every YAML file already matches this catalog.</div>
      )}
      {transferResult?.outcome === "committed" && (
        <div className="builder-zone-empty" data-testid="session-import-committed">
          Imported {transferResult.proposed_writes.length} YAML file
          {transferResult.proposed_writes.length === 1 ? "" : "s"} atomically.
        </div>
      )}
      {transferError && <div className="builder-warning">{transferError}</div>}
      {openError && <div className="builder-warning">{openError}</div>}
      {shippedDraft && (() => {
        const targetId = shippedDraft.targetId.trim();
        const targetRef = `user:sessions/${targetId || "invalid-id"}.yaml`;
        const exists = sessions.some((entry) => entry.ref === targetRef);
        return (
          <div className="builder-inspector-stack" data-testid="shipped-session-target">
            <div className="builder-outline-kind">Open an editable user copy</div>
            <Field
              label="session id"
              value={shippedDraft.targetId}
              onChange={(value) => setShippedDraft((current) => current && {
                ...current,
                targetId: value,
              })}
              suffix="user:sessions/….yaml"
            />
            <div className="builder-site-derived">target: {targetRef}</div>
            <div className="builder-site-derived">
              VS-API creates the editable copy and sets its formal session.name to
              {` ${targetId || "the target id"}`}. Every other session field is preserved.
            </div>
            {exists && <div className="builder-warning">That user session already exists.</div>}
            <div className="builder-preset-row">
              <Button
                variant="primary"
                disabled={!targetId || exists}
                onClick={() => onOpen(shippedDraft.entry, targetRef)}
              >
                Open editable copy
              </Button>
              <Button onClick={() => setShippedDraft(null)}>Cancel</Button>
            </div>
          </div>
        );
      })()}
      {sessions.length === 0 && !sessionsError && (
        <div className="builder-zone-empty">no sessions found</div>
      )}
      {group("★ yours", yours)}
      {group("nodalarc library", shipped)}
      {sessionsError && <div className="builder-warning">{sessionsError}</div>}
    </div>
  );
}
