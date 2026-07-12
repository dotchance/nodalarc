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
 *  selectable because they are part of the grammar, and labeled as gated; the
 *  resolver response appears in the status bar when the draft resolves.
 */

import { useState } from "react";
import { Button } from "../ui/Button";
import { EditorCard, EditorName, NumberField, SelectField } from "./editorKit";
import type { BuilderVisualAuthoringFacts } from "./generated/builderApi";
import { useBuilderCatalog } from "./useBuilderWorld";
import {
  type DraftNode,
  type DraftTerminalMount,
  type Forwarding,
} from "./workspace";


function terminalShortName(ref: string | null): string {
  if (ref === null) return "terminal incomplete";
  return ref.split("/").pop()?.replace(".yaml", "") ?? ref;
}

interface NodeEditorProps {
  draft: DraftNode;
  /** Functional-only: the caller reads the LATEST draft, never a stale
   *  render-closure, so a concurrent edit during an in-flight fetch (mounting a
   *  freshly imported/authored terminal) survives. */
  onChange: (update: (prev: DraftNode) => DraftNode) => void;
  onAddTerminal: (
    terminalRef: string,
    role: NonNullable<DraftTerminalMount["role"]>,
  ) => void;
  onSetTerminalRole: (
    mountId: string,
    role: NonNullable<DraftTerminalMount["role"]>,
  ) => void;
  onAddEthernet: () => void;
  /** focus the name when a create gesture opened this editor. */
  autoFocusName?: boolean;
  authoring: BuilderVisualAuthoringFacts;
}

export function NodeEditor({
  draft,
  onChange,
  onAddTerminal,
  onSetTerminalRole,
  onAddEthernet,
  autoFocusName = false,
  authoring,
}: NodeEditorProps) {
  const terminals = useBuilderCatalog("terminals");
  const [openMount, setOpenMount] = useState<string | null>(null);
  const [pickerOpen, setPickerOpen] = useState(false);
  const [pickerRole, setPickerRole] = useState<
    NonNullable<DraftTerminalMount["role"]>
  >(
    authoring.default_mount_role,
  );
  const [pickerSource, setPickerSource] = useState<"all" | "user">("all");

  const addOrIncrement = (terminalRef: string) => {
    onAddTerminal(terminalRef, pickerRole);
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
        onChange={(value) =>
          onChange((prev) => ({
            ...prev,
            display_name: value,
            id: prev.id,
          }))
        }
        autoFocus={autoFocusName}
      />
      <SelectField
        label="forwarding"
        ariaLabel="Forwarding class"
        value={draft.forwarding ?? ""}
        onChange={(value) =>
          onChange((prev) => ({ ...prev, forwarding: value ? value as Forwarding : null }))
        }
        options={[
          { value: "", label: "select forwarding", disabled: true },
          ...authoring.forwarding_classes.map((choice) => ({
            value: choice.id,
            label: choice.label,
          })),
        ]}
      />

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
                    {mount.role ?? "role incomplete"} · {mount.count === null
                      ? "count incomplete"
                      : `×${mount.count}`}
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
                    onSetTerminalRole(
                      mount.mount_id,
                      value as NonNullable<DraftTerminalMount["role"]>,
                    )
                  }
                  options={authoring.mount_roles.map((choice) => ({
                    value: choice.id,
                    label: `${choice.label} \u2014 ${choice.description}`,
                  }))}
                />
                <SelectField
                  stack
                  label="terminal"
                  ariaLabel="Mount terminal"
                  value={mount.terminal_ref}
                  onChange={(terminal_ref) => updateMount(mount.mount_id, { terminal_ref })}
                  options={terminals.entries.map((entry) => ({
                      value: entry.ref,
                      label:
                        (entry.ref.startsWith("user:") ? "\u2605 " : "") +
                        entry.display_name,
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
                {mount.role === "access" && (
                  <SelectField
                    label="spacecraft boresight"
                    value={mount.boresight?.mode ?? ""}
                    onChange={(mode) =>
                      updateMount(mount.mount_id, {
                        boresight:
                          mode === authoring.space_access_boresight.mode
                            ? { ...authoring.space_access_boresight }
                            : null,
                      })
                    }
                    options={[
                      { value: "", label: "select pointing" },
                      {
                        value: authoring.space_access_boresight.mode,
                        label: authoring.space_access_boresight.mode,
                      },
                    ]}
                  />
                )}
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
        <Button onClick={onAddEthernet}>
          + lan
        </Button>
      </div>

      {pickerOpen && (
        <div className="builder-terminal-picker" data-testid="terminal-picker">
          <div className="builder-preset-row" role="radiogroup" aria-label="Port role">
            {authoring.mount_roles.map((choice) => (
              <Button
                key={choice.id}
                active={pickerRole === choice.id}
                title={choice.description}
                onClick={() => setPickerRole(choice.id)}
              >
                {choice.label}
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
              .filter(
                (entry) => pickerSource === "all" || entry.ref.startsWith("user:"),
              )
              .map((entry) => (
                <button
                  key={entry.ref}
                  className="builder-outline-row builder-terminal-option"
                  title={entry.ref}
                  onClick={() => addOrIncrement(entry.ref)}
                >
                  <span className="builder-terminal-option-name">
                    <span>
                      {entry.ref.startsWith("user:") ? "\u2605 " : ""}
                      {entry.display_name}
                    </span>
                    <span className="builder-outline-count">add</span>
                  </span>
                  {entry.summary && (
                    <span className="builder-library-entry-summary">{entry.summary}</span>
                  )}
                </button>
              ))}
            <div className="builder-site-derived">
              Create and customize reusable terminals from the Library.
            </div>
          </div>
          {terminals.error && <div className="builder-warning">{terminals.error}</div>}
        </div>
      )}
    </div>
  );
}
