/** Backend-owned catalog component editing.
 *
 * The browser retains the complete JSON draft returned by VS-API. Specialized
 * forms mutate only known JSON-pointer fields in that full document; the
 * advanced editor exposes the same JSON application state for every component
 * family. Persisted YAML, canonicalization, grammar validation, reference
 * validation, and CAS storage remain backend authority.
 */

import { useEffect, useRef, useState } from "react";
import { Button, IconButton } from "../ui/Button";
import {
  BodySelect,
  EditorCard,
  EditorName,
  Field,
  NumberField,
  PasteArea,
  SelectField,
} from "./editorKit";
import {
  addCatalogDraftNodeEthernet,
  addCatalogDraftNodeTerminal,
  addCatalogDraftSiteNode,
  compileCatalogDraft,
  getCatalogDependents,
  patchCatalogDraft,
  replaceCatalogDraftObject,
  saveCatalogDraft,
} from "./builderApiClient";
import type { CatalogDraftEditorRecovery } from "./structuredDraftRecovery";
import { useBuilderCatalog } from "./useBuilderWorld";
import type {
  BuilderVisualAuthoringFacts,
  CatalogComponentDraftEnvelope,
  CatalogDependencyImpact,
  CatalogDraftCompileResult,
  CatalogDraftPatchCommand,
  CatalogDraftSaveResult,
  CatalogFamilyMetadata,
  JsonValue,
} from "./generated/builderApi";

type JsonObject = { [key: string]: JsonValue };

const cloneJson = <T,>(value: T): T => structuredClone(value);

function isObject(value: unknown): value is JsonObject {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function jsonEqual(left: JsonValue | undefined, right: JsonValue | undefined): boolean {
  if (Object.is(left, right)) return true;
  if (Array.isArray(left) || Array.isArray(right)) {
    return (
      Array.isArray(left) &&
      Array.isArray(right) &&
      left.length === right.length &&
      left.every((value, index) => jsonEqual(value, right[index]))
    );
  }
  if (isObject(left) || isObject(right)) {
    if (!isObject(left) || !isObject(right)) return false;
    const leftKeys = Object.keys(left);
    const rightKeys = Object.keys(right);
    return (
      leftKeys.length === rightKeys.length &&
      leftKeys.every((key) => jsonEqual(left[key], right[key]))
    );
  }
  return false;
}

function escapePointerToken(value: string): string {
  return value.replace(/~/g, "~0").replace(/\//g, "~1");
}

function decodePointer(pointer: string): string[] {
  return pointer
    .split("/")
    .slice(1)
    .map((token) => token.replace(/~1/g, "/").replace(/~0/g, "~"));
}

function valueAt(document: Readonly<Record<string, JsonValue>>, pointer: string): JsonValue | undefined {
  let current: JsonValue | undefined = document;
  for (const token of decodePointer(pointer)) {
    if (Array.isArray(current)) {
      current = current[Number(token)];
    } else if (isObject(current)) {
      current = current[token];
    } else {
      return undefined;
    }
  }
  return current;
}

function setAt(
  document: Readonly<Record<string, JsonValue>>,
  pointer: string,
  value: JsonValue,
): Readonly<Record<string, JsonValue>> {
  const next = cloneJson(document) as JsonObject;
  const tokens = decodePointer(pointer);
  let current: any = next;
  for (let index = 0; index < tokens.length - 1; index += 1) {
    const token = tokens[index]!;
    const following = tokens[index + 1]!;
    if (Array.isArray(current)) {
      const arrayIndex = Number(token);
      if (current[arrayIndex] === undefined) {
        current[arrayIndex] = /^\d+$/.test(following) ? [] : {};
      }
      current = current[arrayIndex]!;
    } else if (isObject(current)) {
      if (current[token] === undefined || current[token] === null) {
        current[token] = /^\d+$/.test(following) ? [] : {};
      }
      current = current[token]!;
    }
  }
  const final = tokens[tokens.length - 1]!;
  if (Array.isArray(current)) current[Number(final)] = cloneJson(value);
  else if (isObject(current)) current[final] = cloneJson(value);
  return next;
}

function wrapperObject(
  document: Readonly<Record<string, JsonValue>>,
  wrapper: string,
): JsonObject {
  const value = document[wrapper];
  return isObject(value) ? value : {};
}

export function catalogDraftFieldCommands(
  base: Readonly<Record<string, JsonValue>>,
  next: Readonly<Record<string, JsonValue>>,
  wrapper: string,
): CatalogDraftPatchCommand[] {
  const baseObject = wrapperObject(base, wrapper);
  const nextObject = wrapperObject(next, wrapper);
  const commands: CatalogDraftPatchCommand[] = [];
  const diff = (baseValue: JsonValue, nextValue: JsonValue, pointer: string) => {
    if (jsonEqual(baseValue, nextValue)) return;
    if (isObject(baseValue) && isObject(nextValue)) {
      for (const key of new Set([...Object.keys(baseValue), ...Object.keys(nextValue)])) {
        const childPointer = `${pointer}/${escapePointerToken(key)}`;
        if (!(key in nextValue)) commands.push({ operation: "remove", pointer: childPointer });
        else if (!(key in baseValue)) {
          commands.push({ operation: "add", pointer: childPointer, value: nextValue[key] ?? null });
        } else {
          diff(baseValue[key] ?? null, nextValue[key] ?? null, childPointer);
        }
      }
      return;
    }
    commands.push({ operation: "replace", pointer, value: nextValue });
  };
  for (const key of new Set([...Object.keys(baseObject), ...Object.keys(nextObject)])) {
    if (key === "id") continue;
    const pointer = `/${escapePointerToken(wrapper)}/${escapePointerToken(key)}`;
    if (!(key in nextObject)) commands.push({ operation: "remove", pointer });
    else if (!(key in baseObject)) {
      commands.push({ operation: "add", pointer, value: nextObject[key] ?? null });
    } else {
      diff(baseObject[key] ?? null, nextObject[key] ?? null, pointer);
    }
  }
  return commands;
}

function text(value: JsonValue | undefined): string {
  return typeof value === "string" ? value : "";
}

function numberValue(value: JsonValue | undefined): number | null {
  return typeof value === "number" ? value : null;
}

function scaledNumberValue(
  value: JsonValue | undefined,
  divisor: number,
): number | null {
  const parsed = numberValue(value);
  return parsed === null ? null : parsed / divisor;
}

function arrayValue(value: JsonValue | undefined): JsonValue[] {
  return Array.isArray(value) ? cloneJson(value) as JsonValue[] : [];
}

function TerminalForm({
  document,
  wrapper,
  setValue,
  authoring,
}: SpecializedFormProps) {
  const root = `/${escapePointerToken(wrapper)}`;
  const displayName = text(valueAt(document, `${root}/display_name`));
  const medium = text(valueAt(document, `${root}/medium`));
  const frequencyGhz = scaledNumberValue(
    valueAt(document, `${root}/signal/frequency_hz`),
    1e9,
  );
  const band = text(valueAt(document, `${root}/signal/band`));
  return (
    <div className="builder-inspector-stack" data-testid="catalog-terminal-form">
      <EditorName value={displayName} onChange={(value) => setValue(`${root}/display_name`, value)} />
      <div className="builder-preset-row" role="radiogroup" aria-label="Terminal medium">
        {authoring.link_media.map((choice) => (
          <Button
            key={choice.id}
            active={medium === choice.id}
            onClick={() => {
              setValue(`${root}/medium`, choice.id);
              setValue(`${root}/signal`, { ...choice.signal_seed });
            }}
          >
            {choice.label}
          </Button>
        ))}
      </div>
      {medium === "optical" ? (
        <NumberField
          label="wavelength"
          suffix="nm"
          value={numberValue(valueAt(document, `${root}/signal/wavelength_nm`))}
          onChange={(value) => setValue(`${root}/signal/wavelength_nm`, value)}
        />
      ) : medium === "rf" ? (
        <>
          <Field
            label="band"
            value={band}
            placeholder="for example, ka"
            onChange={(value) => setValue(`${root}/signal/band`, value)}
          />
          <NumberField
            label="frequency"
            suffix="GHz"
            step={0.1}
            value={frequencyGhz}
            onChange={(value) => setValue(`${root}/signal/frequency_hz`, value * 1e9)}
          />
        </>
      ) : (
        <div className="builder-warning">Select RF or optical before entering signal facts.</div>
      )}
      <NumberField
        label="tx bandwidth"
        suffix="Mbps"
        value={numberValue(valueAt(document, `${root}/bandwidth_mbps/transmit`))}
        onChange={(value) => setValue(`${root}/bandwidth_mbps/transmit`, value)}
      />
      <NumberField
        label="rx bandwidth"
        suffix="Mbps"
        value={numberValue(valueAt(document, `${root}/bandwidth_mbps/receive`))}
        onChange={(value) => setValue(`${root}/bandwidth_mbps/receive`, value)}
      />
      <NumberField
        label="tracking capacity"
        integer
        min={1}
        value={numberValue(valueAt(document, `${root}/tracking_capacity`))}
        onChange={(value) => setValue(`${root}/tracking_capacity`, value)}
      />
      <NumberField
        label="max range"
        suffix="km"
        value={numberValue(valueAt(document, `${root}/max_range_km`))}
        onChange={(value) => setValue(`${root}/max_range_km`, value)}
      />
      <NumberField
        label="min elevation"
        suffix="deg"
        value={numberValue(valueAt(document, `${root}/limits/elevation_deg/min`))}
        onChange={(value) => setValue(`${root}/limits/elevation_deg/min`, value)}
      />
      <NumberField
        label="max elevation"
        suffix="deg"
        value={numberValue(valueAt(document, `${root}/limits/elevation_deg/max`))}
        onChange={(value) => setValue(`${root}/limits/elevation_deg/max`, value)}
      />
      <NumberField
        label="max tracking rate"
        suffix="deg/s"
        value={numberValue(valueAt(document, `${root}/limits/max_tracking_rate_deg_s`))}
        onChange={(value) => setValue(`${root}/limits/max_tracking_rate_deg_s`, value)}
      />
      <Field
        label="reference"
        value={text(valueAt(document, `${root}/reference`))}
        onChange={(value) => setValue(`${root}/reference`, value)}
      />
    </div>
  );
}

interface SpecializedFormProps {
  document: Readonly<Record<string, JsonValue>>;
  wrapper: string;
  setValue: (pointer: string, value: JsonValue) => void;
  authoring: BuilderVisualAuthoringFacts;
}

interface NodeFormProps extends SpecializedFormProps {
  addTerminalMount: (
    role: BuilderVisualAuthoringFacts["default_mount_role"],
    terminalRef: string,
  ) => Promise<boolean>;
  addEthernetPort: () => Promise<boolean>;
}

function NodeForm({
  document,
  wrapper,
  setValue,
  authoring,
  addTerminalMount,
  addEthernetPort,
}: NodeFormProps) {
  const root = `/${escapePointerToken(wrapper)}`;
  const terminals = useBuilderCatalog("terminals");
  const mounts = arrayValue(valueAt(document, `${root}/terminals`));
  const ethernet = arrayValue(valueAt(document, `${root}/ethernet`));
  const forwarding = text(valueAt(document, `${root}/forwarding`));
  const [newMountRole, setNewMountRole] = useState(authoring.default_mount_role);
  const [newMountTerminal, setNewMountTerminal] = useState("");
  const [adding, setAdding] = useState(false);
  const replaceMount = (index: number, field: string, value: JsonValue) => {
    const next = cloneJson(mounts);
    const mount = isObject(next[index]) ? next[index] as JsonObject : {};
    mount[field] = value;
    next[index] = mount;
    setValue(`${root}/terminals`, next);
  };
  return (
    <div className="builder-inspector-stack" data-testid="catalog-node-form">
      <EditorName
        label="node name"
        value={text(valueAt(document, `${root}/display_name`))}
        onChange={(value) => setValue(`${root}/display_name`, value)}
      />
      <SelectField
        label="forwarding"
        ariaLabel="Forwarding class"
        value={forwarding}
        onChange={(value) => setValue(`${root}/forwarding`, value)}
        options={[
          { value: "", label: "select forwarding", disabled: true },
          ...authoring.forwarding_classes.map((choice) => ({
            value: choice.id,
            label: choice.label,
          })),
        ]}
      />
      {mounts.map((value, index) => {
        const mount = isObject(value) ? value : {};
        const mountId = text(mount.id);
        const mountLabel = mountId || "mount id incomplete";
        return (
          <EditorCard
            key={mountId ? `mount:${mountId}` : `index:${index}`}
            title={mountLabel}
            open
            actions={
              <IconButton
                icon="x"
                size={12}
                label={`Remove ${mountLabel}`}
                onClick={() => setValue(`${root}/terminals`, mounts.filter((_, item) => item !== index))}
              />
            }
          >
            <SelectField
              label="role"
              ariaLabel={`${mountLabel} role`}
              value={text(mount.role)}
              onChange={(value) => replaceMount(index, "role", value)}
              options={[
                { value: "", label: "select role", disabled: true },
                ...authoring.mount_roles.map((choice) => ({
                  value: choice.id,
                  label: choice.label,
                })),
              ]}
            />
            <SelectField
              stack
              label="terminal"
              ariaLabel={`${mountLabel} terminal`}
              value={text(mount.terminal)}
              onChange={(value) => replaceMount(index, "terminal", value)}
              options={terminals.entries.map((entry) => ({
                value: entry.ref,
                label: `${entry.namespace === "user" ? "★ " : ""}${entry.display_name}`,
              }))}
            />
            <NumberField
              label="count"
              min={1}
              integer
              value={numberValue(mount.count)}
              onChange={(value) => replaceMount(index, "count", value)}
            />
          </EditorCard>
        );
      })}
      <div className="builder-preset-row">
        <SelectField
          label="new mount role"
          value={newMountRole}
          onChange={(value) =>
            setNewMountRole(value as BuilderVisualAuthoringFacts["default_mount_role"])
          }
          options={authoring.mount_roles.map((choice) => ({
            value: choice.id,
            label: choice.label,
          }))}
        />
        <SelectField
          stack
          label="new mount terminal"
          value={newMountTerminal}
          onChange={setNewMountTerminal}
          options={[
            { value: "", label: "select terminal", disabled: true },
            ...terminals.entries.map((entry) => ({
              value: entry.ref,
              label: `${entry.namespace === "user" ? "★ " : ""}${entry.display_name}`,
            })),
          ]}
        />
      </div>
      <div className="builder-preset-row">
        <Button
          disabled={adding || !newMountTerminal}
          onClick={() => {
            setAdding(true);
            void addTerminalMount(newMountRole, newMountTerminal).finally(() =>
              setAdding(false),
            );
          }}
        >
          + terminal mount
        </Button>
        <Button
          disabled={adding}
          onClick={() => {
            setAdding(true);
            void addEthernetPort().finally(() => setAdding(false));
          }}
        >
          + LAN port
        </Button>
      </div>
      {ethernet.map((value, index) => {
        const port = isObject(value) ? text(value.id) : "";
        return (
          <div className="builder-library-entry" key={index}>
            <Field
              label="LAN port"
              value={port}
              placeholder="port id"
              onChange={(id) => {
                const next = cloneJson(ethernet);
                next[index] = { id };
                setValue(`${root}/ethernet`, next);
              }}
            />
            <IconButton
              icon="x"
              size={12}
              label={`Remove ${port || `port ${index + 1}`}`}
              onClick={() => setValue(`${root}/ethernet`, ethernet.filter((_, item) => item !== index))}
            />
          </div>
        );
      })}
      <Field
        label="reference"
        value={text(valueAt(document, `${root}/reference`))}
        onChange={(value) => setValue(`${root}/reference`, value)}
      />
    </div>
  );
}

interface SiteFormProps extends SpecializedFormProps {
  addNode: (nodeId: string, nodeRef: string) => Promise<boolean>;
}

function SiteForm({ document, wrapper, setValue, addNode }: SiteFormProps) {
  const root = `/${escapePointerToken(wrapper)}`;
  const bodies = useBuilderCatalog("bodies");
  const nodes = useBuilderCatalog("nodes");
  const installedNodes = arrayValue(valueAt(document, `${root}/nodes`));
  const [newNodeId, setNewNodeId] = useState("");
  const [newNodeRef, setNewNodeRef] = useState("");
  const [addingNode, setAddingNode] = useState(false);
  const replaceNode = (index: number, update: (node: JsonObject) => void) => {
    const next = cloneJson(installedNodes);
    const node = isObject(next[index]) ? next[index] as JsonObject : {};
    update(node);
    next[index] = node;
    setValue(`${root}/nodes`, next);
  };
  return (
    <div className="builder-inspector-stack" data-testid="catalog-site-form">
      <EditorName
        value={text(valueAt(document, `${root}/display_name`))}
        onChange={(value) => setValue(`${root}/display_name`, value)}
      />
      <BodySelect
        label="on body"
        ariaLabel="Site body"
        value={text(valueAt(document, `${root}/frame/body_fixed/body`))}
        onChange={(value) => setValue(`${root}/frame/body_fixed/body`, value)}
        bodies={bodies}
      />
      <NumberField
        label="latitude"
        suffix="deg"
        value={numberValue(valueAt(document, `${root}/location/lat_deg`))}
        onChange={(value) => setValue(`${root}/location/lat_deg`, value)}
      />
      <NumberField
        label="longitude"
        suffix="deg"
        value={numberValue(valueAt(document, `${root}/location/lon_deg`))}
        onChange={(value) => setValue(`${root}/location/lon_deg`, value)}
      />
      <NumberField
        label="altitude"
        suffix="m"
        value={numberValue(valueAt(document, `${root}/location/alt_m`))}
        onChange={(value) => setValue(`${root}/location/alt_m`, value)}
      />
      <Field
        label="site LAN"
        value={text(valueAt(document, `${root}/lan/ipv4`))}
        onChange={(value) => setValue(`${root}/lan/ipv4`, value.trim())}
      />
      <Field
        label="tags"
        value={arrayValue(valueAt(document, `${root}/tags`)).map(String).join(", ")}
        onChange={(value) => setValue(
          `${root}/tags`,
          value.split(/[,\s]+/).map((item) => item.trim()).filter(Boolean),
        )}
      />
      {installedNodes.map((value, index) => {
        const node = isObject(value) ? value : {};
        const nodeId = text(node.id);
        const nodeLabel = nodeId || "node id incomplete";
        const terminals = isObject(node.terminals) ? node.terminals : {};
        const interfaces = isObject(node.interfaces) ? node.interfaces : {};
        const lo0 = isObject(interfaces.lo0) ? interfaces.lo0 : {};
        const terr0 = isObject(interfaces.terr0) ? interfaces.terr0 : {};
        return (
          <EditorCard
            key={nodeId ? `node:${nodeId}` : `index:${index}`}
            title={nodeLabel}
            open
            actions={
              <IconButton
                icon="x"
                size={12}
                label={`Remove ${nodeLabel}`}
                onClick={() => setValue(`${root}/nodes`, installedNodes.filter((_, item) => item !== index))}
              />
            }
          >
            <SelectField
              stack
              label="model"
              ariaLabel={`${nodeLabel} model`}
              value={text(node.model)}
              onChange={(value) => replaceNode(index, (current) => { current.model = value; })}
              options={nodes.entries.map((entry) => ({
                value: entry.ref,
                label: entry.display_name ?? entry.ref,
              }))}
            />
            {Object.entries(terminals).map(([mount, installed]) => (
              <NumberField
                key={mount}
                label={mount}
                min={1}
                integer
                value={numberValue(isObject(installed) ? installed.installed_count : undefined)}
                onChange={(count) => replaceNode(index, (current) => {
                  const currentTerminals = isObject(current.terminals)
                    ? cloneJson(current.terminals) as JsonObject
                    : {};
                  const currentInstall = isObject(currentTerminals[mount])
                    ? currentTerminals[mount] as JsonObject
                    : {};
                  currentInstall.installed_count = count;
                  currentTerminals[mount] = currentInstall;
                  current.terminals = currentTerminals;
                })}
              />
            ))}
            <Field
              label="lo0"
              value={text(lo0.ipv4)}
              onChange={(value) => replaceNode(index, (current) => {
                  const currentInterfaces = isObject(current.interfaces)
                    ? cloneJson(current.interfaces) as JsonObject
                    : {};
                const currentLo0 = isObject(currentInterfaces.lo0) ? currentInterfaces.lo0 as JsonObject : {};
                currentLo0.ipv4 = value.trim();
                currentInterfaces.lo0 = currentLo0;
                current.interfaces = currentInterfaces;
              })}
            />
            <Field
              label="terr0"
              value={text(terr0.ipv4)}
              onChange={(value) => replaceNode(index, (current) => {
                  const currentInterfaces = isObject(current.interfaces)
                    ? cloneJson(current.interfaces) as JsonObject
                    : {};
                const currentTerr0 = isObject(currentInterfaces.terr0) ? currentInterfaces.terr0 as JsonObject : {};
                currentTerr0.ipv4 = value.trim();
                currentInterfaces.terr0 = currentTerr0;
                current.interfaces = currentInterfaces;
              })}
            />
          </EditorCard>
        );
      })}
      <EditorCard title="Add installed node" open>
        <Field
          label="node id"
          value={newNodeId}
          placeholder="for example, gw1"
          onChange={setNewNodeId}
        />
        <SelectField
          stack
          label="node model"
          ariaLabel="New site node model"
          value={newNodeRef}
          onChange={setNewNodeRef}
          options={[
            { value: "", label: "select a node model" },
            ...nodes.entries.map((entry) => ({
              value: entry.ref,
              label: entry.display_name ?? entry.ref,
            })),
          ]}
        />
        <Button
          disabled={addingNode || !newNodeId.trim() || !newNodeRef}
          onClick={() => {
            setAddingNode(true);
            void addNode(newNodeId.trim(), newNodeRef).then((added) => {
              if (added) {
                setNewNodeId("");
                setNewNodeRef("");
              }
            }).finally(() => setAddingNode(false));
          }}
        >
          {addingNode ? "Adding…" : "+ add node"}
        </Button>
      </EditorCard>
    </div>
  );
}

function SiteSetForm({ document, wrapper, setValue }: SpecializedFormProps) {
  const root = `/${escapePointerToken(wrapper)}`;
  const sites = useBuilderCatalog("sites");
  const members = arrayValue(valueAt(document, `${root}/sites`));
  const [selectedRef, setSelectedRef] = useState("");
  return (
    <div className="builder-inspector-stack" data-testid="catalog-site-set-form">
      <EditorName
        value={text(valueAt(document, `${root}/display_name`))}
        onChange={(value) => setValue(`${root}/display_name`, value)}
      />
      {members.map((member, index) => {
        const label = typeof member === "string" ? member : `invalid site reference ${index + 1}`;
        return (
          <div className="builder-library-entry" key={`${label}:${index}`}>
            <span className="builder-outline-name">{label}</span>
            <IconButton
              icon="x"
              size={12}
              label={`Remove ${label}`}
              onClick={() => setValue(`${root}/sites`, members.filter((_, item) => item !== index))}
            />
          </div>
        );
      })}
      <SelectField
        stack
        label="add site reference"
        ariaLabel="Site to add"
        value={selectedRef}
        onChange={setSelectedRef}
        options={[
          { value: "", label: "pick a site" },
          ...sites.entries.map((entry) => ({
            value: entry.ref,
            label: entry.display_name ?? entry.ref,
          })),
        ]}
      />
      <Button
        disabled={!selectedRef || members.includes(selectedRef)}
        onClick={() => {
          if (!selectedRef) return;
          setValue(`${root}/sites`, [...members, selectedRef]);
          setSelectedRef("");
        }}
      >
        + add site
      </Button>
      <Field
        label="reference"
        value={text(valueAt(document, `${root}/reference`))}
        onChange={(value) => setValue(`${root}/reference`, value)}
      />
    </div>
  );
}

function impactText(impact: CatalogDependencyImpact): string {
  const direct = impact.direct_dependents.length;
  const transitive = impact.transitive_dependents.length;
  if (direct === 0 && transitive === 0) return "No saved sessions or components depend on this object.";
  return `Overwrite affects ${direct} direct and ${transitive} transitive dependent${transitive === 1 ? "" : "s"}.`;
}

export function CatalogDraftEditorWindow({
  initialDraft,
  metadata,
  onSaved,
  onClose,
  onDiscard,
  initialRecovery,
  onRecoveryChange,
  authoring,
}: {
  initialDraft: CatalogComponentDraftEnvelope;
  metadata: CatalogFamilyMetadata;
  onSaved: (result: CatalogDraftSaveResult) => void | Promise<void>;
  onClose: (dirty: boolean) => void;
  onDiscard?: () => void;
  initialRecovery?: CatalogDraftEditorRecovery | null;
  onRecoveryChange?: (recovery: CatalogDraftEditorRecovery | null) => void;
  authoring: BuilderVisualAuthoringFacts;
}) {
  const wrapper = metadata.wrapper;
  if (!wrapper) throw new Error(`catalog family ${metadata.family} has no component wrapper`);
  const recovered = initialRecovery?.draft.target_ref === initialDraft.target_ref
    ? initialRecovery
    : null;
  const [draft, setDraft] = useState(recovered?.draft ?? initialDraft);
  const [workingDocument, setWorkingDocument] = useState(
    recovered?.workingDocument ?? initialDraft.document,
  );
  const [compileResult, setCompileResult] = useState<CatalogDraftCompileResult | null>(null);
  const [advanced, setAdvanced] = useState(recovered?.advanced ?? false);
  const [advancedText, setAdvancedText] = useState(
    recovered?.advancedText ?? JSON.stringify(wrapperObject(initialDraft.document, wrapper), null, 2),
  );
  const [advancedError, setAdvancedError] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<"idle" | "validating" | "saving">("idle");
  const [impact, setImpact] = useState<CatalogDependencyImpact | null>(null);
  const [impactState, setImpactState] = useState<"not-required" | "loading" | "loaded" | "failed">(
    initialDraft.expected_target_revision ? "loading" : "not-required",
  );
  const [impactAttempt, setImpactAttempt] = useState(0);
  const preserveRecoveredTextOnMount = useRef(recovered !== null);
  const specialized = ["terminals", "nodes", "sites", "site-sets"].includes(draft.family);
  const advancedMode = advanced || !specialized;
  const canonicalAdvancedText = JSON.stringify(wrapperObject(workingDocument, wrapper), null, 2);
  const fieldDirty = catalogDraftFieldCommands(draft.document, workingDocument, wrapper).length > 0;
  const advancedTextDirty = advancedMode && advancedText !== canonicalAdvancedText;
  const dirty = fieldDirty || advancedTextDirty;
  const issues = compileResult?.issues ?? draft.issues;

  useEffect(() => {
    if (preserveRecoveredTextOnMount.current) {
      preserveRecoveredTextOnMount.current = false;
      return;
    }
    setAdvancedText(JSON.stringify(wrapperObject(workingDocument, wrapper), null, 2));
  }, [workingDocument, wrapper]);

  useEffect(() => {
    onRecoveryChange?.(
      dirty
        ? {
            draft,
            workingDocument,
            advanced,
            advancedText,
          }
        : null,
    );
  }, [advanced, advancedText, dirty, draft, onRecoveryChange, workingDocument]);

  useEffect(() => {
    if (!draft.expected_target_revision) {
      setImpact(null);
      setImpactState("not-required");
      return;
    }
    setImpact(null);
    setImpactState("loading");
    void getCatalogDependents({ ref: draft.target_ref }).then(
      (result) => {
        setImpact(result);
        setImpactState("loaded");
      },
      (cause) => {
        setImpactState("failed");
        setError(cause instanceof Error ? cause.message : String(cause));
      },
    );
  }, [draft.expected_target_revision, draft.target_ref, impactAttempt]);

  const setValue = (pointer: string, value: JsonValue) => {
    setWorkingDocument((current) => setAt(current, pointer, value));
    setCompileResult(null);
  };

  const applyAdvanced = async (): Promise<CatalogComponentDraftEnvelope | null> => {
    setAdvancedError(null);
    if (!advancedTextDirty) return draft;
    try {
      const replaced = await replaceCatalogDraftObject({
        draft,
        expected_draft_revision: draft.draft_revision,
        raw_object_json: advancedText,
      });
      setDraft(replaced);
      setWorkingDocument(replaced.document);
      setAdvancedText(JSON.stringify(wrapperObject(replaced.document, wrapper), null, 2));
      setCompileResult(null);
      return replaced;
    } catch (cause) {
      setAdvancedError(cause instanceof Error ? cause.message : String(cause));
      return null;
    }
  };

  const flush = async (
    document: Readonly<Record<string, JsonValue>> = workingDocument,
  ): Promise<CatalogComponentDraftEnvelope> => {
    const commands = catalogDraftFieldCommands(draft.document, document, wrapper);
    if (commands.length === 0) return draft;
    let patched = draft;
    for (let offset = 0; offset < commands.length; offset += 64) {
      patched = await patchCatalogDraft({
        draft: patched,
        expected_draft_revision: patched.draft_revision,
        commands: commands.slice(offset, offset + 64),
      });
    }
    setDraft(patched);
    setWorkingDocument(patched.document);
    return patched;
  };

  const addSiteNode = async (nodeId: string, nodeRef: string): Promise<boolean> => {
    setBusy("validating");
    setError(null);
    try {
      const patched = await flush(workingDocument);
      const updated = await addCatalogDraftSiteNode({
        draft: patched,
        expected_draft_revision: patched.draft_revision,
        node_id: nodeId,
        node_ref: nodeRef,
      });
      setDraft(updated);
      setWorkingDocument(updated.document);
      setCompileResult(null);
      return true;
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
      return false;
    } finally {
      setBusy("idle");
    }
  };

  const addNodeTerminalMount = async (
    role: BuilderVisualAuthoringFacts["default_mount_role"],
    terminalRef: string,
  ): Promise<boolean> => {
    setBusy("validating");
    setError(null);
    try {
      const patched = await flush(workingDocument);
      const updated = await addCatalogDraftNodeTerminal({
        draft: patched,
        expected_draft_revision: patched.draft_revision,
        terminal_ref: terminalRef,
        role,
      });
      setDraft(updated);
      setWorkingDocument(updated.document);
      setCompileResult(null);
      return true;
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
      return false;
    } finally {
      setBusy("idle");
    }
  };

  const addNodeEthernetPort = async (): Promise<boolean> => {
    setBusy("validating");
    setError(null);
    try {
      const patched = await flush(workingDocument);
      const updated = await addCatalogDraftNodeEthernet({
        draft: patched,
        expected_draft_revision: patched.draft_revision,
      });
      setDraft(updated);
      setWorkingDocument(updated.document);
      setCompileResult(null);
      return true;
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
      return false;
    } finally {
      setBusy("idle");
    }
  };

  const validate = async () => {
    setBusy("validating");
    setError(null);
    try {
      const patched = advancedMode ? await applyAdvanced() : await flush(workingDocument);
      if (!patched) return;
      const compiled = await compileCatalogDraft({
        draft: patched,
        expected_draft_revision: patched.draft_revision,
      });
      setDraft(compiled.draft);
      setWorkingDocument(compiled.draft.document);
      setCompileResult(compiled);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setBusy("idle");
    }
  };

  const save = async () => {
    if (draft.expected_target_revision && impactState !== "loaded") {
      setError("Dependency impact must load before this user component can be overwritten.");
      return;
    }
    setBusy("saving");
    setError(null);
    try {
      const patched = advancedMode ? await applyAdvanced() : await flush(workingDocument);
      if (!patched) return;
      const compiled = await compileCatalogDraft({
        draft: patched,
        expected_draft_revision: patched.draft_revision,
      });
      setDraft(compiled.draft);
      setWorkingDocument(compiled.draft.document);
      setCompileResult(compiled);
      if (!compiled.save_allowed) return;
      const saved = await saveCatalogDraft({
        draft: compiled.draft,
        expected_draft_revision: compiled.draft.draft_revision,
      });
      setDraft(saved.draft);
      setWorkingDocument(saved.draft.document);
      setCompileResult(saved.compile_result);
      await onSaved(saved);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setBusy("idle");
    }
  };

  const formProps = { document: workingDocument, wrapper, setValue, authoring };
  const form = draft.family === "terminals"
    ? <TerminalForm {...formProps} />
    : draft.family === "nodes"
      ? (
        <NodeForm
          {...formProps}
          addTerminalMount={addNodeTerminalMount}
          addEthernetPort={addNodeEthernetPort}
        />
      )
      : draft.family === "sites"
        ? <SiteForm {...formProps} addNode={addSiteNode} />
        : draft.family === "site-sets"
          ? <SiteSetForm {...formProps} />
          : null;

  return (
    <div className="builder-inspector-stack" data-testid="catalog-draft-editor">
      <div className="builder-site-derived">
        {draft.source_ref && draft.source_ref !== draft.target_ref
          ? `customizing ${draft.source_ref} as ${draft.target_ref}`
          : `editing ${draft.target_ref}`}
      </div>
      {specialized && (
        <div className="builder-preset-row" role="tablist" aria-label="Catalog editor mode">
          <Button active={!advanced} onClick={() => setAdvanced(false)}>Form</Button>
          <Button active={advanced} onClick={() => setAdvanced(true)}>Advanced JSON</Button>
        </div>
      )}
      {!advanced && form ? form : (
        <div className="builder-inspector-stack" data-testid="catalog-advanced-json">
          <div className="builder-site-derived">
            Full backend document object. The id is fixed; VS-API validates every
            JSON-pointer patch and produces canonical YAML.
          </div>
          <PasteArea
            rows={28}
            value={advancedText}
            onChange={(value) => {
              setAdvancedText(value);
              setAdvancedError(null);
            }}
            placeholder="catalog component JSON"
          />
          <Button
            disabled={busy !== "idle" || !advancedTextDirty}
            onClick={() => {
              setBusy("validating");
              void applyAdvanced().finally(() => setBusy("idle"));
            }}
          >
            Apply JSON edits
          </Button>
          {advancedError && <div className="builder-warning">{advancedError}</div>}
        </div>
      )}
      {issues.map((issue) => (
        <div className="builder-warning" key={`${issue.code}:${issue.pointer}`}>
          {issue.message} <span className="builder-site-derived">{issue.pointer}</span>
        </div>
      ))}
      {impact && (
        <div className="builder-library-note" data-testid="catalog-dependency-impact">
          {impactText(impact)}
          {[...new Map(
            [...impact.direct_dependents, ...impact.transitive_dependents]
              .map((dependent) => [dependent.ref, dependent]),
          ).values()].map((dependent) => (
            <div className="builder-site-derived" key={dependent.ref}>
              {dependent.family} · {dependent.ref}
            </div>
          ))}
        </div>
      )}
      {impactState === "loading" && (
        <div className="builder-site-derived">checking saved dependents before overwrite…</div>
      )}
      {impactState === "failed" && draft.expected_target_revision && (
        <Button
          onClick={() => {
            setError(null);
            setImpactAttempt((attempt) => attempt + 1);
          }}
        >
          retry dependency check
        </Button>
      )}
      {compileResult?.save_allowed && !compileResult.runtime_supported && (
        <div className="builder-warning">
          Structurally valid and savable; current runtime support blocks deployment.
        </div>
      )}
      {compileResult?.canonical_yaml && (
        <details>
          <summary>Backend canonical YAML</summary>
          <pre className="builder-yaml-body">{compileResult.canonical_yaml}</pre>
        </details>
      )}
      {error && <div className="builder-warning">{error}</div>}
      <div className="builder-preset-row">
        <Button disabled={busy !== "idle"} onClick={() => void validate()}>
          {busy === "validating" ? "Validating…" : dirty ? "Validate edits" : "Validate"}
        </Button>
        <Button
          variant="primary"
          disabled={busy !== "idle" || impactState === "loading" || impactState === "failed"}
          onClick={() => void save()}
        >
          {busy === "saving" ? "Saving…" : "Save to library"}
        </Button>
        {dirty && onDiscard && (
          <Button disabled={busy !== "idle"} onClick={onDiscard}>Discard edits</Button>
        )}
        <Button disabled={busy !== "idle"} onClick={() => onClose(dirty)}>Close</Button>
      </div>
    </div>
  );
}
