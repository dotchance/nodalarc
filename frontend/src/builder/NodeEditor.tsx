// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
/** Node draft editor — the faceplate.
 *
 *  A node reads as hardware: terminal mounts are role-colored port CHIPS in
 *  the scene's color language (access teal, isl/crosslink blue); LAN attach
 *  is a "lan" chip that serializes to the grammar's ethernet ports (LAN is
 *  not a terminal role). Click a chip to edit that one mount inline. The
 *  terminal picker stays open for batch adding, and clicking a terminal that
 *  matches an existing mount increments its count — the one-click transform
 *  the discovery arc measured as the biggest authoring cost.
 *
 *  Runtime-gated forwarding classes (host, bridge, control_only) are
 *  selectable — they are grammar — and labeled as gated; the resolver's
 *  typed wall appears in the status bar the moment the draft resolves.
 */

import { useState } from "react";
import { Button } from "../ui/Button";
import { useBuilderCatalog } from "./useBuilderWorld";
import type { DraftNode, DraftTerminalMount } from "./workspace";

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
}

export function NodeEditor({ draft, onChange }: NodeEditorProps) {
  const terminals = useBuilderCatalog("terminals");
  const [openMount, setOpenMount] = useState<string | null>(null);
  const [pickerOpen, setPickerOpen] = useState(false);
  const [pickerRole, setPickerRole] = useState<DraftTerminalMount["role"]>("access");

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
      <label className="builder-field">
        <span className="builder-field-label">node name</span>
        <span className="builder-field-input">
          <input
            type="text"
            value={draft.display_name}
            onChange={(e) =>
              onChange({ ...draft, display_name: e.target.value, id: e.target.value })
            }
          />
        </span>
      </label>
      <label className="builder-field">
        <span className="builder-field-label">forwarding</span>
        <span className="builder-field-input">
          <select
            aria-label="Forwarding class"
            value={draft.forwarding}
            onChange={(e) =>
              onChange({ ...draft, forwarding: e.target.value as DraftNode["forwarding"] })
            }
          >
            {FORWARDING_OPTIONS.map((option) => (
              <option key={option.value} value={option.value}>
                {option.value}
                {option.gated ? " — runtime-gated" : ""}
              </option>
            ))}
          </select>
        </span>
      </label>
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
          <label className="builder-field">
            <span className="builder-field-label">role</span>
            <span className="builder-field-input">
              <select
                aria-label="Mount role"
                value={editing.role}
                onChange={(e) =>
                  updateMount(editing.mount_id, {
                    role: e.target.value as DraftTerminalMount["role"],
                  })
                }
              >
                {ROLES.map((role) => (
                  <option key={role} value={role}>
                    {role}
                  </option>
                ))}
              </select>
            </span>
          </label>
          <label className="builder-field builder-field--stack">
            <span className="builder-field-label">terminal</span>
            <select
              aria-label="Mount terminal"
              value={editing.terminal_ref}
              onChange={(e) => updateMount(editing.mount_id, { terminal_ref: e.target.value })}
            >
              {terminals.entries
                .filter((entry) => !entry.error)
                .map((entry) => (
                  <option key={entry.ref} value={entry.ref}>
                    {entry.display_name ?? entry.id ?? entry.ref}
                    {entry.ref.startsWith("user:") ? " (yours)" : ""}
                  </option>
                ))}
            </select>
          </label>
          <label className="builder-field">
            <span className="builder-field-label">count</span>
            <span className="builder-field-input">
              <input
                type="number"
                value={editing.count}
                min={1}
                onChange={(e) => {
                  const parsed = Math.max(1, Math.round(Number(e.target.value)));
                  if (Number.isFinite(parsed)) updateMount(editing.mount_id, { count: parsed });
                }}
              />
            </span>
          </label>
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
                  className="builder-outline-row"
                  title={entry.notes ?? entry.ref}
                  onClick={() => addOrIncrement(entry.ref)}
                >
                  <span>
                    {entry.display_name ?? entry.id}
                    {entry.ref.startsWith("user:") ? " (yours)" : ""}
                  </span>
                  <span className="builder-outline-count">add</span>
                </button>
              ))}
          </div>
          {terminals.error && <div className="builder-warning">{terminals.error}</div>}
        </div>
      )}
    </div>
  );
}
