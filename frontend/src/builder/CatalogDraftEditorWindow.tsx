/** Backend-owned graphical and YAML catalog component editing. */

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
  applyCatalogDraftYaml,
  compileCatalogDraft,
  getCatalogDependents,
  mutateCatalogDraftControls,
  patchCatalogDraft,
  saveCatalogDraft,
} from "./builderApiClient";
import {
  GraphicalControlTreeEditor,
  type BuilderControlMutation,
} from "./GraphicalControlTreeEditor";
import type { CatalogDraftEditorRecovery } from "./structuredDraftRecovery";
import { useBuilderCatalog } from "./useBuilderWorld";
import type {
  BuilderVisualAuthoringFacts,
  CatalogComponentDraftEnvelope,
  CatalogDependencyImpact,
  CatalogDraftCompileResult,
  CatalogDraftIssue,
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
              if (medium === choice.id) return;
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
                const current = isObject(next[index])
                  ? cloneJson(next[index]) as JsonObject
                  : {};
                current.id = id;
                next[index] = current;
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
  const frame = valueAt(document, `${root}/frame`);
  const bodyFixedAuthoring = !isObject(frame)
    || Object.keys(frame).length === 0
    || isObject(frame.body_fixed);
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
      {bodyFixedAuthoring ? (
        <>
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
        </>
      ) : (
        <div className="builder-site-derived">
          This site uses a non-body-fixed frame. Its frame fields are edited below.
        </div>
      )}
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
  const draftRef = useRef(recovered?.draft ?? initialDraft);
  const [baselineDocument, setBaselineDocument] = useState(
    recovered?.baselineDocument ?? initialDraft.document,
  );
  const [workingDocument, setWorkingDocument] = useState(
    recovered?.workingDocument ?? initialDraft.document,
  );
  const [compileResult, setCompileResult] = useState<CatalogDraftCompileResult | null>(null);
  const [yamlText, setYamlText] = useState(
    recovered?.yamlText ?? initialDraft.projected_yaml,
  );
  const [appliedYamlText, setAppliedYamlText] = useState(
    recovered?.appliedYamlText ?? initialDraft.projected_yaml,
  );
  const [yamlIssues, setYamlIssues] = useState<ReadonlyArray<CatalogDraftIssue>>([]);
  const [canonicalizationRequired, setCanonicalizationRequired] = useState(
    recovered?.canonicalizationRequired ?? false,
  );
  const [canonicalizationAccepted, setCanonicalizationAccepted] = useState(
    recovered?.canonicalizationAccepted ?? false,
  );
  const [graphicalSync, setGraphicalSync] = useState<"idle" | "syncing" | "failed">("idle");
  const [yamlSync, setYamlSync] = useState<"idle" | "syncing" | "failed">("idle");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<"idle" | "validating" | "saving">("idle");
  const [impact, setImpact] = useState<CatalogDependencyImpact | null>(null);
  const [impactState, setImpactState] = useState<"not-required" | "loading" | "loaded" | "failed">(
    initialDraft.expected_target_revision ? "loading" : "not-required",
  );
  const [impactAttempt, setImpactAttempt] = useState(0);
  const graphicalSyncSequence = useRef(0);
  const yamlBufferGeneration = useRef(0);
  const yamlApplyQueue = useRef<Promise<void>>(Promise.resolve());
  const yamlPendingApply = useRef<{
    generation: number;
    promise: Promise<CatalogComponentDraftEnvelope | null>;
  } | null>(null);
  const fieldDirty = catalogDraftFieldCommands(draft.document, workingDocument, wrapper).length > 0;
  const yamlTextDirty = yamlText !== appliedYamlText;
  const semanticDirty = !jsonEqual(baselineDocument, workingDocument);
  const dirty = semanticDirty || yamlTextDirty || canonicalizationRequired
    || canonicalizationAccepted;
  const graphicalEditingBlocked = yamlTextDirty || canonicalizationRequired;
  const issues = yamlIssues.length > 0
    ? yamlIssues
    : compileResult?.issues ?? draft.issues;

  useEffect(() => {
    onRecoveryChange?.(
      dirty
        ? {
            draft,
            baselineDocument,
            workingDocument,
            yamlText,
            appliedYamlText,
            canonicalizationRequired,
            canonicalizationAccepted,
          }
        : null,
    );
  }, [
    appliedYamlText,
    baselineDocument,
    canonicalizationAccepted,
    canonicalizationRequired,
    dirty,
    draft,
    onRecoveryChange,
    workingDocument,
    yamlText,
  ]);

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
    if (graphicalEditingBlocked) return;
    setWorkingDocument((current) => setAt(current, pointer, value));
    setCompileResult(null);
    setYamlIssues([]);
  };

  const adoptGraphicalDraft = (updated: CatalogComponentDraftEnvelope) => {
    yamlBufferGeneration.current += 1;
    draftRef.current = updated;
    setDraft(updated);
    setWorkingDocument(updated.document);
    setYamlText(updated.projected_yaml);
    setAppliedYamlText(updated.projected_yaml);
    setYamlIssues([]);
    setCanonicalizationRequired(false);
    setCanonicalizationAccepted(false);
    setCompileResult(null);
  };

  const applyYamlBuffer = (
    buffer: string,
    generation: number,
  ): Promise<CatalogComponentDraftEnvelope | null> => {
    if (yamlPendingApply.current?.generation === generation) {
      return yamlPendingApply.current.promise;
    }
    graphicalSyncSequence.current += 1;
    const run = async (): Promise<CatalogComponentDraftEnvelope | null> => {
      if (yamlBufferGeneration.current === generation) setYamlSync("syncing");
      try {
        const baseDraft = draftRef.current;
        const result = await applyCatalogDraftYaml({
          draft: baseDraft,
          expected_draft_revision: baseDraft.draft_revision,
          yaml_text: buffer,
        });
        if (yamlBufferGeneration.current !== generation) return null;
        setYamlIssues(result.issues);
        setYamlSync("idle");
        if (!result.applied) return null;
        draftRef.current = result.draft;
        setDraft(result.draft);
        setWorkingDocument(result.draft.document);
        setYamlText(result.yaml_text);
        setAppliedYamlText(result.yaml_text);
        setCanonicalizationRequired(result.canonicalization_required);
        setCanonicalizationAccepted(false);
        setCompileResult(null);
        return result.draft;
      } catch (cause) {
        if (yamlBufferGeneration.current === generation) {
          setYamlSync("failed");
          setError(cause instanceof Error ? cause.message : String(cause));
        }
        return null;
      }
    };
    const operation = yamlApplyQueue.current.then(run, run);
    yamlApplyQueue.current = operation.then(() => undefined, () => undefined);
    yamlPendingApply.current = { generation, promise: operation };
    void operation.finally(() => {
      if (yamlPendingApply.current?.promise === operation) yamlPendingApply.current = null;
    });
    return operation;
  };

  const applyYaml = (): Promise<CatalogComponentDraftEnvelope | null> => {
    if (!yamlTextDirty) return Promise.resolve(draftRef.current);
    return applyYamlBuffer(yamlText, yamlBufferGeneration.current);
  };

  const flush = async (
    document: Readonly<Record<string, JsonValue>> = workingDocument,
  ): Promise<CatalogComponentDraftEnvelope> => {
    graphicalSyncSequence.current += 1;
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
    adoptGraphicalDraft(patched);
    return patched;
  };

  useEffect(() => {
    if (
      !fieldDirty
      || graphicalEditingBlocked
      || busy !== "idle"
    ) return;
    const sequence = graphicalSyncSequence.current + 1;
    graphicalSyncSequence.current = sequence;
    const baseDraft = draft;
    const document = workingDocument;
    const commands = catalogDraftFieldCommands(baseDraft.document, document, wrapper);
    const timer = window.setTimeout(() => {
      setGraphicalSync("syncing");
      void (async () => {
        try {
          let patched = baseDraft;
          for (let offset = 0; offset < commands.length; offset += 64) {
            patched = await patchCatalogDraft({
              draft: patched,
              expected_draft_revision: patched.draft_revision,
              commands: commands.slice(offset, offset + 64),
            });
          }
          if (graphicalSyncSequence.current !== sequence) return;
          adoptGraphicalDraft(patched);
          setGraphicalSync("idle");
        } catch (cause) {
          if (graphicalSyncSequence.current !== sequence) return;
          setGraphicalSync("failed");
          setError(cause instanceof Error ? cause.message : String(cause));
        }
      })();
    }, 250);
    return () => window.clearTimeout(timer);
  }, [busy, draft, fieldDirty, graphicalEditingBlocked, workingDocument, wrapper]);

  useEffect(() => {
    if (!yamlTextDirty || fieldDirty || busy !== "idle") return;
    const generation = yamlBufferGeneration.current;
    const buffer = yamlText;
    const timer = window.setTimeout(() => {
      void applyYamlBuffer(buffer, generation);
    }, 400);
    return () => window.clearTimeout(timer);
  }, [busy, fieldDirty, yamlText, yamlTextDirty]);

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
      adoptGraphicalDraft(updated);
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
      adoptGraphicalDraft(updated);
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
      adoptGraphicalDraft(updated);
      return true;
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
      return false;
    } finally {
      setBusy("idle");
    }
  };

  const mutateControls = async (
    commands: ReadonlyArray<BuilderControlMutation>,
  ): Promise<void> => {
    if (fieldDirty || graphicalEditingBlocked) {
      throw new Error("Wait for current graphical or YAML edits to finish before changing this field.");
    }
    graphicalSyncSequence.current += 1;
    setBusy("validating");
    setError(null);
    try {
      const baseDraft = draftRef.current;
      const updated = await mutateCatalogDraftControls({
        draft: baseDraft,
        expected_draft_revision: baseDraft.draft_revision,
        commands,
      });
      adoptGraphicalDraft(updated);
    } finally {
      setBusy("idle");
    }
  };

  const validate = async () => {
    graphicalSyncSequence.current += 1;
    setBusy("validating");
    setError(null);
    try {
      const patched = yamlTextDirty ? await applyYaml() : await flush(workingDocument);
      if (!patched) return;
      const compiled = await compileCatalogDraft({
        draft: patched,
        expected_draft_revision: patched.draft_revision,
      });
      draftRef.current = compiled.draft;
      setDraft(compiled.draft);
      setWorkingDocument(compiled.draft.document);
      setYamlIssues([]);
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
    if (canonicalizationRequired) {
      setError("Acknowledge canonical YAML formatting before saving this component.");
      return;
    }
    graphicalSyncSequence.current += 1;
    setBusy("saving");
    setError(null);
    try {
      const patched = yamlTextDirty ? await applyYaml() : await flush(workingDocument);
      if (!patched) return;
      if (yamlTextDirty && yamlText !== patched.projected_yaml) {
        setError("Review and acknowledge canonical YAML formatting before saving.");
        return;
      }
      const compiled = await compileCatalogDraft({
        draft: patched,
        expected_draft_revision: patched.draft_revision,
      });
      draftRef.current = compiled.draft;
      setDraft(compiled.draft);
      setWorkingDocument(compiled.draft.document);
      setYamlIssues([]);
      setCompileResult(compiled);
      if (!compiled.save_allowed) return;
      const saved = await saveCatalogDraft({
        draft: compiled.draft,
        expected_draft_revision: compiled.draft.draft_revision,
      });
      yamlBufferGeneration.current += 1;
      draftRef.current = saved.draft;
      setDraft(saved.draft);
      setBaselineDocument(saved.draft.document);
      setWorkingDocument(saved.draft.document);
      setYamlText(saved.draft.projected_yaml);
      setAppliedYamlText(saved.draft.projected_yaml);
      setYamlIssues([]);
      setCanonicalizationRequired(false);
      setCanonicalizationAccepted(false);
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
      <div className="catalog-draft-surfaces">
        <div className="builder-inspector-stack" data-testid="catalog-graphical-editor">
          <div className="builder-zone-title">Graphical editor</div>
          {form && (
            <fieldset
              className="catalog-draft-fieldset"
              disabled={busy !== "idle" || graphicalEditingBlocked}
            >
              {form}
            </fieldset>
          )}
          <GraphicalControlTreeEditor
            tree={draft.control_tree}
            disabled={
              busy !== "idle"
              || graphicalEditingBlocked
              || fieldDirty
              || graphicalSync === "syncing"
            }
            onMutate={mutateControls}
          />
          {yamlTextDirty && (
            <div className="builder-warning" data-testid="catalog-yaml-stale-marker">
              Showing applied revision {draft.draft_revision}; the YAML buffer has unapplied edits.
            </div>
          )}
          {canonicalizationRequired && (
            <div className="builder-warning" data-testid="catalog-canonicalization-warning">
              The applied YAML contains formatting or comments that graphical edits and saves
              cannot preserve. Review the backend projection before continuing.
              <div className="builder-preset-row">
                <Button
                  disabled={busy !== "idle"}
                  onClick={() => {
                    yamlBufferGeneration.current += 1;
                    setYamlText(draft.projected_yaml);
                    setAppliedYamlText(draft.projected_yaml);
                    setYamlIssues(draft.issues);
                    setCanonicalizationRequired(false);
                    setCanonicalizationAccepted(true);
                    setError(null);
                  }}
                >
                  Use canonical YAML
                </Button>
              </div>
            </div>
          )}
        </div>
        <div className="builder-inspector-stack" data-testid="catalog-yaml-editor">
          <div className="builder-zone-title">Component YAML</div>
          <div className="builder-site-derived">
            VS-API parses this exact buffer through the canonical catalog loader. The graphical
            view changes only after a valid buffer is applied.
          </div>
          <PasteArea
            ariaLabel="Component YAML"
            className="catalog-draft-yaml"
            rows={28}
            value={yamlText}
            disabled={busy !== "idle" || fieldDirty || graphicalSync === "syncing"}
            onChange={(value) => {
              graphicalSyncSequence.current += 1;
              yamlBufferGeneration.current += 1;
              setYamlText(value);
              setYamlSync("idle");
              setYamlIssues([]);
              setError(null);
            }}
            placeholder="catalog component YAML"
          />
          <div className="builder-preset-row">
            <Button
              disabled={busy !== "idle" || !yamlTextDirty || fieldDirty}
              onClick={() => {
                setBusy("validating");
                void applyYaml().finally(() => setBusy("idle"));
              }}
            >
              Apply YAML
            </Button>
            <Button
              disabled={busy !== "idle" || !yamlTextDirty}
              onClick={() => {
                graphicalSyncSequence.current += 1;
                yamlBufferGeneration.current += 1;
                setYamlText(appliedYamlText);
                setYamlSync("idle");
                setYamlIssues([]);
                setError(null);
              }}
            >
              Revert buffer
            </Button>
          </div>
          {graphicalSync === "syncing" && (
            <div className="builder-site-derived">updating YAML from graphical edits…</div>
          )}
          {graphicalSync === "failed" && (
            <div className="builder-warning">YAML projection could not be refreshed.</div>
          )}
          {yamlSync === "syncing" && (
            <div className="builder-site-derived">validating YAML and updating the graphical view…</div>
          )}
          {yamlSync === "failed" && (
            <div className="builder-warning">YAML validation could not reach VS-API.</div>
          )}
        </div>
      </div>
      {issues.map((issue) => (
        <div className="builder-warning" key={`${issue.code}:${issue.pointer}`}>
          {issue.message}{" "}
          <span className="builder-site-derived">
            {issue.pointer}
            {issue.source_line ? ` · line ${issue.source_line}:${issue.source_column ?? 1}` : ""}
          </span>
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
      {error && <div className="builder-warning">{error}</div>}
      <div className="builder-preset-row">
        <Button disabled={busy !== "idle"} onClick={() => void validate()}>
          {busy === "validating" ? "Validating…" : dirty ? "Validate edits" : "Validate"}
        </Button>
        <Button
          variant="primary"
          disabled={
            busy !== "idle"
            || canonicalizationRequired
            || impactState === "loading"
            || impactState === "failed"
          }
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
