// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
/** Terminal draft editor — authoring the physics OME enforces.
 *
 *  Terminals are pure physical truth (no role, no placement — the grammar's
 *  composition rules), so they author LIBRARY-FIRST: edit the physics, save
 *  to your catalog, then mount by reference. "Start from" seeds every value
 *  from an existing terminal in either tier — fork-by-seeding; the values
 *  become yours. Warnings never block; the family grammar validates at save
 *  and its message returns verbatim.
 */

import { useState } from "react";
import { Button } from "../ui/Button";
import { EditorName, Field as TextField, NumberField, SelectField } from "./editorKit";
import { readCatalogObject, saveUserObject } from "./useBuilderWorld";
import type { BuilderCatalogEntry } from "./builderTypes";
import {
  draftTerminalFromDocument,
  identifier,
  terminalObjectFromDraft,
  terminalWarnings,
  type DraftTerminal,
} from "./workspace";

interface TerminalEditorProps {
  draft: DraftTerminal;
  onChange: (draft: DraftTerminal) => void;
  /** Existing terminals for the "start from" seeding row. */
  catalog: BuilderCatalogEntry[];
  /** Called with the new library ref after a successful save. */
  onSaved: (ref: string) => void;
  onCancel: () => void;
}

export function TerminalEditor({
  draft,
  onChange,
  catalog,
  onSaved,
  onCancel,
}: TerminalEditorProps) {
  const [saveState, setSaveState] = useState<
    | { kind: "idle" }
    | { kind: "saving" }
    | { kind: "conflict" }
    | { kind: "failed"; message: string }
  >({ kind: "idle" });
  const [seedError, setSeedError] = useState<string | null>(null);
  const warnings = terminalWarnings(draft);

  const seedFrom = async (ref: string) => {
    setSeedError(null);
    try {
      const { document } = await readCatalogObject(ref);
      const seeded = draftTerminalFromDocument(document);
      onChange({
        ...seeded,
        id: identifier(`${seeded.id}-custom`),
        display_name: `${seeded.display_name} (custom)`,
        reference: "session-builder-draft",
      });
    } catch (e) {
      setSeedError(e instanceof Error ? e.message : String(e));
    }
  };

  const save = async () => {
    setSaveState({ kind: "saving" });
    try {
      const entry = await saveUserObject(
        "terminals",
        { terminal: terminalObjectFromDraft(draft) },
        { overwrite: saveState.kind === "conflict" },
      );
      setSaveState({ kind: "idle" });
      onSaved(entry.ref);
    } catch (e) {
      const status = (e as Error & { status?: number }).status;
      if (status === 409 && saveState.kind !== "conflict") {
        setSaveState({ kind: "conflict" });
      } else {
        setSaveState({ kind: "failed", message: e instanceof Error ? e.message : String(e) });
      }
    }
  };

  return (
    <div className="builder-mount-editor" data-testid="terminal-editor">
      <SelectField
        stack
        label="start from"
        ariaLabel="Seed terminal"
        value=""
        onChange={(ref) => ref && void seedFrom(ref)}
        options={[
          { value: "", label: "blank template" },
          ...catalog
            .filter((entry) => !entry.error)
            .map((entry) => ({
              value: entry.ref,
              label:
                (entry.ref.startsWith("user:") ? "\u2605 " : "") +
                (entry.display_name ?? entry.id ?? entry.ref),
            })),
        ]}
      />
      {seedError && <div className="builder-warning">{seedError}</div>}
      <EditorName
        value={draft.display_name}
        onChange={(value) => onChange({ ...draft, display_name: value, id: value })}
      />
      <div className="builder-preset-row" role="radiogroup" aria-label="Terminal medium">
        <Button active={draft.medium === "rf"} onClick={() => onChange({ ...draft, medium: "rf" })}>
          rf
        </Button>
        <Button
          active={draft.medium === "optical"}
          onClick={() => onChange({ ...draft, medium: "optical" })}
        >
          optical
        </Button>
      </div>
      {draft.medium === "rf" ? (
        <>
          <TextField
            label="band"
            value={draft.band}
            onChange={(band) => onChange({ ...draft, band })}
          />
          <NumberField
            label="frequency"
            value={draft.frequency_ghz}
            suffix="GHz"
            step={0.1}
            onChange={(frequency_ghz) => onChange({ ...draft, frequency_ghz })}
          />
        </>
      ) : (
        <NumberField
          label="wavelength"
          value={draft.wavelength_nm}
          suffix="nm"
          onChange={(wavelength_nm) => onChange({ ...draft, wavelength_nm })}
        />
      )}
      {/* Pointing seeds (IG-7): the elevation window decides what this head
          can physically aim at — a ground dish never looks below its
          horizon; a space head must. Seeds raw values the user then owns. */}
      <div className="builder-preset-row" role="radiogroup" aria-label="Pointing">
        <Button
          title="Elevation 20 to 90 — a dish on the ground, looking up"
          onClick={() => onChange({ ...draft, elevation_min_deg: 20, elevation_max_deg: 90 })}
        >
          ground dish
        </Button>
        <Button
          title="Elevation -90 to 90 — full sky; a space head looks below its own horizontal (GEO aims almost straight down)"
          onClick={() => onChange({ ...draft, elevation_min_deg: -90, elevation_max_deg: 90 })}
        >
          space head
        </Button>
      </div>
      <NumberField
        label="tx bandwidth"
        value={draft.transmit_mbps}
        suffix="Mbps"
        step={50}
        onChange={(transmit_mbps) =>
          onChange(
            // Symmetric duplex is the norm: rx follows tx until rx is set
            // apart, then it is the user's.
            draft.receive_mbps === draft.transmit_mbps
              ? { ...draft, transmit_mbps, receive_mbps: transmit_mbps }
              : { ...draft, transmit_mbps },
          )
        }
      />
      <NumberField
        label="rx bandwidth"
        value={draft.receive_mbps}
        suffix="Mbps"
        step={50}
        onChange={(receive_mbps) => onChange({ ...draft, receive_mbps })}
      />
      <NumberField
        label="tracking capacity"
        value={draft.tracking_capacity}
        onChange={(tracking_capacity) =>
          onChange({ ...draft, tracking_capacity: Math.max(1, Math.round(tracking_capacity)) })
        }
      />
      <NumberField
        label="max range"
        value={draft.max_range_km}
        suffix="km"
        step={100}
        onChange={(max_range_km) => onChange({ ...draft, max_range_km })}
      />
      <NumberField
        label="min elevation"
        value={draft.elevation_min_deg}
        suffix="deg"
        onChange={(elevation_min_deg) => onChange({ ...draft, elevation_min_deg })}
      />
      <NumberField
        label="max elevation"
        value={draft.elevation_max_deg}
        suffix="deg"
        onChange={(elevation_max_deg) => onChange({ ...draft, elevation_max_deg })}
      />
      <NumberField
        label="max tracking rate"
        value={draft.max_tracking_rate_deg_s}
        suffix="deg/s"
        step={0.1}
        onChange={(max_tracking_rate_deg_s) => onChange({ ...draft, max_tracking_rate_deg_s })}
      />
      {warnings.map((warning) => (
        <div className="builder-warning" key={warning}>
          {warning}
        </div>
      ))}
      <div className="builder-preset-row">
        <Button variant="primary" disabled={saveState.kind === "saving"} onClick={() => void save()}>
          {saveState.kind === "conflict"
            ? "Overwrite in library?"
            : saveState.kind === "saving"
              ? "Saving…"
              : "Save terminal to library"}
        </Button>
        <Button onClick={onCancel}>Cancel</Button>
      </div>
      {saveState.kind === "failed" && (
        <div className="builder-warning">{saveState.message}</div>
      )}
    </div>
  );
}
