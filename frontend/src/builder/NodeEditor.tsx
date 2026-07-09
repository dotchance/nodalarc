// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
/** Node draft editor — the faceplate.
 *
 *  A node reads as hardware: terminal mounts are role-colored port chips in
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
import { EditorCard, EditorName, NumberField, SelectField } from "./editorKit";
import { TerminalEditor } from "./TerminalEditor";
import { importUserObjectYaml, useBuilderCatalog } from "./useBuilderWorld";
import {
  defaultDraftTerminal,
  FORWARDING_MODES,
  MOUNT_ROLES,
  ROLE_DESCRIPTIONS,
  type DraftNode,
  type DraftTerminal,
  type DraftTerminalMount,
  type Forwarding,
} from "./workspace";


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
  /** Functional-only: the caller reads the LATEST draft, never a stale
   *  render-closure, so a concurrent edit during an in-flight fetch (mounting a
   *  freshly imported/authored terminal) survives. */
  onChange: (update: (prev: DraftNode) => DraftNode) => void;
  /** focus the name when a create gesture opened this editor. */
  autoFocusName?: boolean;
}

export function NodeEditor({ draft, onChange, autoFocusName = false }: NodeEditorProps) {
  const terminals = useBuilderCatalog("terminals");
  const [openMount, setOpenMount] = useState<string | null>(null);
  const [pickerOpen, setPickerOpen] = useState(false);
  const [pickerRole, setPickerRole] = useState<DraftTerminalMount["role"]>("access");
  const [pickerSource, setPickerSource] = useState<"all" | "user">("all");
  const [terminalDraft, setTerminalDraft] = useState<DraftTerminal | null>(null);
  const [importError, setImportError] = useState<string | null>(null);

  const addOrIncrement = (terminalRef: string) => {
    onChange((prev) => {
      const existing = prev.terminals.find(
        (m) => m.terminal_ref === terminalRef && m.role === pickerRole,
      );
      if (existing) {
        return {
          ...prev,
          terminals: prev.terminals.map((m) =>
            m === existing ? { ...m, count: m.count + 1 } : m,
          ),
        };
      }
      return {
        ...prev,
        terminals: [
          ...prev.terminals,
          {
            mount_id: nextMountId(prev, pickerRole),
            role: pickerRole,
            terminal_ref: terminalRef,
            count: 1,
          },
        ],
      };
    });
  };

  const updateMount = (mountId: string, patch: Partial<DraftTerminalMount>) => {
    onChange((prev) => ({
      ...prev,
      terminals: prev.terminals.map((m) =>
        m.mount_id === mountId ? { ...m, ...patch } : m,
      ),
    }));
  };

  return (
    <div className="builder-node-editor" data-testid="node-editor">
      <EditorName
        label="node name"
        value={draft.display_name}
        onChange={(value) => onChange((prev) => ({ ...prev, display_name: value, id: value }))}
        autoFocus={autoFocusName}
      />
      <SelectField
        label="forwarding"
        ariaLabel="Forwarding class"
        value={draft.forwarding}
        onChange={(value) => onChange((prev) => ({ ...prev, forwarding: value as Forwarding }))}
        options={(Object.keys(FORWARDING_MODES) as Forwarding[]).map((value) => ({
          value,
          label: value + (FORWARDING_MODES[value].gated ? " — runtime-gated" : ""),
        }))}
      />
      {FORWARDING_MODES[draft.forwarding]?.gated && (
        <div className="builder-warning">
          {draft.forwarding} is structurally valid grammar; today's runtime rejects it
          with a typed gate at resolve
        </div>
      )}

      <div className="builder-ports" data-testid="port-list">
        {draft.terminals.map((mount) => {
          const entry = terminals.entries.find((e) => e.ref === mount.terminal_ref);
          const name = entry?.display_name ?? terminalShortName(mount.terminal_ref);
          return (
            <div key={mount.mount_id} className={`builder-port builder-port--${mount.role}`}>
              <EditorCard
                title={name}
                summary={
                  <>
                    {mount.role} · ×{mount.count}
                    {entry?.summary ? ` · ${entry.summary}` : ""}
                  </>
                }
                open={openMount === mount.mount_id}
                onToggle={() =>
                  setOpenMount(openMount === mount.mount_id ? null : mount.mount_id)
                }
              >
                <SelectField
                  label="role"
                  ariaLabel="Mount role"
                  value={mount.role}
                  onChange={(value) =>
                    updateMount(mount.mount_id, { role: value as DraftTerminalMount["role"] })
                  }
                  options={MOUNT_ROLES.map((role) => ({
                    value: role,
                    label: `${role} \u2014 ${ROLE_DESCRIPTIONS[role]}`,
                  }))}
                />
                <SelectField
                  stack
                  label="terminal"
                  ariaLabel="Mount terminal"
                  value={mount.terminal_ref}
                  onChange={(terminal_ref) => updateMount(mount.mount_id, { terminal_ref })}
                  options={terminals.entries
                    .filter((e) => !e.error)
                    .map((e) => ({
                      value: e.ref,
                      label:
                        (e.ref.startsWith("user:") ? "\u2605 " : "") +
                        (e.display_name ?? e.id ?? e.ref),
                    }))}
                />
                {entry?.summary ? (
                  <div className="builder-site-derived">{entry.summary}</div>
                ) : null}
                <NumberField
                  label="count"
                  value={mount.count}
                  min={1}
                  integer
                  onChange={(count) => updateMount(mount.mount_id, { count })}
                />
                <Button
                  variant="danger"
                  onClick={() => {
                    onChange((prev) => ({
                      ...prev,
                      terminals: prev.terminals.filter((m) => m.mount_id !== mount.mount_id),
                    }));
                    setOpenMount(null);
                  }}
                >
                  Remove port
                </Button>
              </EditorCard>
            </div>
          );
        })}
        {draft.ethernet.map((port) => (
          <div key={port} className="builder-port builder-port--lan">
            <EditorCard
              title={`lan · ${port}`}
              summary="LAN attach — serializes to the node's ethernet ports"
              open={openMount === `lan:${port}`}
              onToggle={() => setOpenMount(openMount === `lan:${port}` ? null : `lan:${port}`)}
            >
              <Button
                variant="danger"
                onClick={() => {
                  onChange((prev) => ({ ...prev, ethernet: prev.ethernet.filter((p) => p !== port) }));
                  setOpenMount(null);
                }}
              >
                Remove LAN port
              </Button>
            </EditorCard>
          </div>
        ))}
      </div>
      <div className="builder-preset-row">
        <Button onClick={() => setPickerOpen((v) => !v)}>+ port</Button>
        <Button
          onClick={() =>
            onChange((prev) => {
              const taken = new Set(prev.ethernet);
              let index = 0;
              while (taken.has(`terr${index}`)) index++;
              return { ...prev, ethernet: [...prev.ethernet, `terr${index}`] };
            })
          }
        >
          + lan
        </Button>
      </div>

      {pickerOpen && (
        <div className="builder-terminal-picker" data-testid="terminal-picker">
          <div className="builder-preset-row" role="radiogroup" aria-label="Port role">
            {MOUNT_ROLES.map((role) => (
              <Button
                key={role}
                active={pickerRole === role}
                title={ROLE_DESCRIPTIONS[role]}
                onClick={() => setPickerRole(role)}
              >
                {role}
              </Button>
            ))}
            <span role="radiogroup" aria-label="Terminal source">
              <Button active={pickerSource === "all"} onClick={() => setPickerSource("all")}>
                all
              </Button>
              <Button active={pickerSource === "user"} onClick={() => setPickerSource("user")}>
                ★ yours
              </Button>
            </span>
          </div>
          <div className="builder-terminal-picker-list">
            {terminals.entries
              .filter((entry) => !entry.error)
              .filter(
                (entry) => pickerSource === "all" || entry.ref.startsWith("user:"),
              )
              .map((entry) => (
                <button
                  key={entry.ref}
                  className="builder-outline-row builder-terminal-option"
                  title={entry.notes ?? entry.ref}
                  onClick={() => addOrIncrement(entry.ref)}
                >
                  <span className="builder-terminal-option-name">
                    <span>
                      {entry.ref.startsWith("user:") ? "\u2605 " : ""}
                      {entry.display_name ?? entry.id}
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
              onChange={(update) => setTerminalDraft((prev) => (prev ? update(prev) : prev))}
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
