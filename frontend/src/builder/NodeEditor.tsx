// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
/** Node draft editor — the faceplate.
 *
 *  A node reads as hardware: terminal mounts are role-colored port CHIPS in
 *  the scene's color language (access teal, isl/crosslink blue); LAN attach
 *  is a "lan" chip that serializes to the grammar's ethernet ports (LAN is
 *  not a terminal role). Click a chip to edit that one mount inline. The
 *  terminal picker stays open for batch adding, and clicking a terminal that
 *  matches an existing mount increments its count — port provisioning is
 *  the costliest authoring step, so it is one click.
 *
 *  Runtime-gated forwarding classes (host, bridge, control_only) are
 *  selectable — they are grammar — and labeled as gated; the resolver's
 *  typed wall appears in the status bar the moment the draft resolves.
 */

import { useState } from "react";
import { Button } from "../ui/Button";
import { EditorName, NumberField, SelectField } from "./editorKit";
import { TerminalEditor } from "./TerminalEditor";
import { importUserObjectYaml, useBuilderCatalog } from "./useBuilderWorld";
import {
  defaultDraftTerminal,
  type DraftNode,
  type DraftTerminal,
  type DraftTerminalMount,
} from "./workspace";

const ROLES: DraftTerminalMount["role"][] = ["access", "isl", "crosslink", "backbone"];

const FORWARDING_OPTIONS: { value: DraftNode["forwarding"]; gated: boolean }[] = [
  { value: "routed", gated: false },
  { value: "host", gated: true },
  { value: "bridge", gated: true },
  { value: "control_only", gated: true },
];

function terminalShortName(ref: string): string {
  return ref.split("/").pop()?.replace(".yaml", "") ?? ref;
}

function nextMountId(draft: DraftNode, role: string): string {
  const taken = new Set(draft.terminals.map((m) => m.mount_id));
  for (let index = 0; ; index++) {
    const candidate = index === 0 ? `${role}_0` : `${role}_${index}`;
    if (!taken.has(candidate)) return candidate;
  }
}

interface NodeEditorProps {
  draft: DraftNode;
  onChange: (draft: DraftNode) => void;
  /** IG-2: focus the name when a create gesture opened this editor. */
  autoFocusName?: boolean;
}

export function NodeEditor({ draft, onChange, autoFocusName = false }: NodeEditorProps) {
  const terminals = useBuilderCatalog("terminals");
  const [openMount, setOpenMount] = useState<string | null>(null);
  const [pickerOpen, setPickerOpen] = useState(false);
  const [pickerRole, setPickerRole] = useState<DraftTerminalMount["role"]>("access");
  const [terminalDraft, setTerminalDraft] = useState<DraftTerminal | null>(null);
  const [importError, setImportError] = useState<string | null>(null);

  const addOrIncrement = (terminalRef: string) => {
    const existing = draft.terminals.find(
      (m) => m.terminal_ref === terminalRef && m.role === pickerRole,
    );
    if (existing) {
      onChange({
        ...draft,
        terminals: draft.terminals.map((m) =>
          m === existing ? { ...m, count: m.count + 1 } : m,
        ),
      });
    } else {
      onChange({
        ...draft,
        terminals: [
          ...draft.terminals,
          {
            mount_id: nextMountId(draft, pickerRole),
            role: pickerRole,
            terminal_ref: terminalRef,
            count: 1,
          },
        ],
      });
    }
  };

  const updateMount = (mountId: string, patch: Partial<DraftTerminalMount>) => {
    onChange({
      ...draft,
      terminals: draft.terminals.map((m) =>
        m.mount_id === mountId ? { ...m, ...patch } : m,
      ),
    });
  };

  const editing = draft.terminals.find((m) => m.mount_id === openMount);

  return (
    <div className="builder-node-editor" data-testid="node-editor">
      <EditorName
        label="node name"
        value={draft.display_name}
        onChange={(value) => onChange({ ...draft, display_name: value, id: value })}
        autoFocus={autoFocusName}
      />
      <SelectField
        label="forwarding"
        ariaLabel="Forwarding class"
        value={draft.forwarding}
        onChange={(value) =>
          onChange({ ...draft, forwarding: value as DraftNode["forwarding"] })
        }
        options={FORWARDING_OPTIONS.map((option) => ({
          value: option.value,
          label: option.value + (option.gated ? " — runtime-gated" : ""),
        }))}
      />
      {FORWARDING_OPTIONS.find((o) => o.value === draft.forwarding)?.gated && (
        <div className="builder-warning">
          {draft.forwarding} is structurally valid grammar; today's runtime rejects it
          with a typed gate at resolve
        </div>
      )}

      <div className="builder-chip-row" data-testid="port-chips">
        {draft.terminals.map((mount) => (
          <button
            key={mount.mount_id}
            className={`builder-chip builder-chip--${mount.role}${
              openMount === mount.mount_id ? " builder-chip--open" : ""
            }`}
            onClick={() =>
              setOpenMount(openMount === mount.mount_id ? null : mount.mount_id)
            }
            title={mount.terminal_ref}
          >
            {mount.role} · {terminalShortName(mount.terminal_ref)} ×{mount.count}
          </button>
        ))}
        {draft.ethernet.map((port) => (
          <button
            key={port}
            className="builder-chip builder-chip--lan"
            title="LAN attach — serializes to the node's ethernet ports"
            onClick={() =>
              onChange({ ...draft, ethernet: draft.ethernet.filter((p) => p !== port) })
            }
          >
            lan · {port} ✕
          </button>
        ))}
        <button
          className="builder-chip builder-chip--ghost"
          onClick={() => setPickerOpen((v) => !v)}
        >
          + port
        </button>
        <button
          className="builder-chip builder-chip--ghost"
          onClick={() => {
            const taken = new Set(draft.ethernet);
            let index = 0;
            while (taken.has(`terr${index}`)) index++;
            onChange({ ...draft, ethernet: [...draft.ethernet, `terr${index}`] });
          }}
        >
          + lan
        </button>
      </div>

      {editing && (
        <div className="builder-mount-editor" data-testid="mount-editor">
          <SelectField
            label="role"
            ariaLabel="Mount role"
            value={editing.role}
            onChange={(value) =>
              updateMount(editing.mount_id, {
                role: value as DraftTerminalMount["role"],
              })
            }
            options={ROLES.map((role) => ({ value: role, label: role }))}
          />
          <SelectField
            stack
            label="terminal"
            ariaLabel="Mount terminal"
            value={editing.terminal_ref}
            onChange={(terminal_ref) => updateMount(editing.mount_id, { terminal_ref })}
            options={terminals.entries
              .filter((entry) => !entry.error)
              .map((entry) => ({
                value: entry.ref,
                label:
                  (entry.display_name ?? entry.id ?? entry.ref) +
                  (entry.ref.startsWith("user:") ? " (yours)" : ""),
              }))}
          />
          {(() => {
            const selected = terminals.entries.find((e) => e.ref === editing.terminal_ref);
            return selected?.summary ? (
              <div className="builder-site-derived">{selected.summary}</div>
            ) : null;
          })()}
          <NumberField
            label="count"
            value={editing.count}
            min={1}
            integer
            onChange={(count) => updateMount(editing.mount_id, { count })}
          />
          <Button
            variant="danger"
            onClick={() => {
              onChange({
                ...draft,
                terminals: draft.terminals.filter((m) => m.mount_id !== editing.mount_id),
              });
              setOpenMount(null);
            }}
          >
            Remove port
          </Button>
        </div>
      )}

      {pickerOpen && (
        <div className="builder-terminal-picker" data-testid="terminal-picker">
          <div className="builder-preset-row" role="radiogroup" aria-label="Port role">
            {ROLES.map((role) => (
              <Button
                key={role}
                active={pickerRole === role}
                onClick={() => setPickerRole(role)}
              >
                {role}
              </Button>
            ))}
          </div>
          <div className="builder-terminal-picker-list">
            {terminals.entries
              .filter((entry) => !entry.error)
              .map((entry) => (
                <button
                  key={entry.ref}
                  className="builder-outline-row builder-terminal-option"
                  title={entry.notes ?? entry.ref}
                  onClick={() => addOrIncrement(entry.ref)}
                >
                  <span className="builder-terminal-option-name">
                    <span>
                      {entry.display_name ?? entry.id}
                      {entry.ref.startsWith("user:") ? " (yours)" : ""}
                    </span>
                    <span className="builder-outline-count">add</span>
                  </span>
                  {entry.summary && (
                    <span className="builder-library-entry-summary">{entry.summary}</span>
                  )}
                </button>
              ))}
            <button
              className="builder-outline-row"
              data-testid="new-terminal"
              onClick={() => setTerminalDraft(defaultDraftTerminal())}
            >
              <span>+ new terminal…</span>
            </button>
            <label className="builder-outline-row" data-testid="import-terminal">
              <span>import file…</span>
              <input
                type="file"
                accept=".yaml,.yml"
                style={{ display: "none" }}
                onChange={(e) => {
                  const file = e.target.files?.[0];
                  if (!file) return;
                  void file.text().then(async (text) => {
                    try {
                      const entry = await importUserObjectYaml(text);
                      await terminals.refresh();
                      addOrIncrement(entry.ref);
                      setImportError(null);
                    } catch (err) {
                      setImportError(err instanceof Error ? err.message : String(err));
                    }
                  });
                  e.target.value = "";
                }}
              />
            </label>
            {importError && <div className="builder-warning">{importError}</div>}
          </div>
          {terminalDraft && (
            <TerminalEditor
              draft={terminalDraft}
              onChange={setTerminalDraft}
              catalog={terminals.entries}
              onSaved={(ref) => {
                setTerminalDraft(null);
                void terminals.refresh();
                // Complete the intent: the freshly authored terminal mounts
                // immediately with the picker's current role.
                addOrIncrement(ref);
              }}
              onCancel={() => setTerminalDraft(null)}
            />
          )}
          {terminals.error && <div className="builder-warning">{terminals.error}</div>}
        </div>
      )}
    </div>
  );
}
