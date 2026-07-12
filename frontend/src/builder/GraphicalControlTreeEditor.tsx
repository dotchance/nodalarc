/** Recursive editor for backend-derived canonical configuration controls. */

import { useEffect, useRef, useState, type ReactNode } from "react";
import { Button, IconButton } from "../ui/Button";
import { CheckboxField, EditorCard, Field, SelectField } from "./editorKit";
import type {
  BuilderChoiceControl,
  BuilderControlTree,
  BuilderMapControl,
  BuilderObjectControl,
  BuilderObjectField,
  BuilderScalarControl,
  BuilderSequenceControl,
  CatalogDraftControlMutationRequest,
  CatalogFamily,
} from "./generated/builderApi";
import { useBuilderCatalog } from "./useBuilderWorld";

type BuilderControl = BuilderObjectField["control"];
export type BuilderControlMutation = CatalogDraftControlMutationRequest["commands"][number];

export interface GraphicalControlTreeEditorProps {
  tree: BuilderControlTree;
  disabled?: boolean;
  hideSpecialized?: boolean;
  onMutate: (commands: ReadonlyArray<BuilderControlMutation>) => Promise<void>;
}

function isScalar(control: BuilderControl): control is BuilderScalarControl {
  return "scalar_kind" in control;
}

function isChoice(control: BuilderControl): control is BuilderChoiceControl {
  return "branches" in control;
}

function isSequence(control: BuilderControl): control is BuilderSequenceControl {
  return "can_add" in control;
}

function isMap(control: BuilderControl): control is BuilderMapControl {
  return "add_key_control" in control;
}

function isObject(control: BuilderControl): control is BuilderObjectControl {
  return "fields" in control || "empty_parameters" in control || "model_name" in control;
}

function scalarText(value: BuilderScalarControl["value"]): string {
  return value === null || value === undefined ? "" : String(value);
}

function parsedScalar(
  control: BuilderScalarControl,
  value: string,
): string | number | boolean | null {
  if (control.scalar_kind === "boolean") {
    if (value === "true") return true;
    if (value === "false") return false;
    return null;
  }
  if (control.scalar_kind !== "number") return value;
  if (value.trim() === "") return null;
  const parsed = Number(value);
  if (!Number.isFinite(parsed)) return null;
  return control.number_kind === "integer" ? Math.round(parsed) : parsed;
}

function constraintSummary(control: BuilderScalarControl): string | null {
  const constraints = control.constraints;
  if (!constraints) return null;
  const parts: string[] = [];
  if (constraints.minimum !== null && constraints.minimum !== undefined) {
    parts.push(`minimum ${constraints.minimum}`);
  }
  if (constraints.exclusive_minimum !== null && constraints.exclusive_minimum !== undefined) {
    parts.push(`greater than ${constraints.exclusive_minimum}`);
  }
  if (constraints.maximum !== null && constraints.maximum !== undefined) {
    parts.push(`maximum ${constraints.maximum}`);
  }
  if (constraints.exclusive_maximum !== null && constraints.exclusive_maximum !== undefined) {
    parts.push(`less than ${constraints.exclusive_maximum}`);
  }
  if (constraints.min_length !== null && constraints.min_length !== undefined) {
    parts.push(`minimum length ${constraints.min_length}`);
  }
  if (constraints.max_length !== null && constraints.max_length !== undefined) {
    parts.push(`maximum length ${constraints.max_length}`);
  }
  return parts.length > 0 ? parts.join(" · ") : null;
}

function ReferenceFamilySelect({
  family,
  label,
  value,
  disabled,
  onSelect,
}: {
  family: CatalogFamily;
  label: string;
  value: string;
  disabled: boolean;
  onSelect: (value: string) => void;
}) {
  const catalog = useBuilderCatalog(family);
  const loaded = catalog.entries.map((entry) => ({
    value: entry.ref,
    label: `${entry.namespace === "user" ? "★ " : ""}${entry.display_name}`,
  }));
  const options = value && !loaded.some((option) => option.value === value)
    ? [{ value, label: value }, ...loaded]
    : loaded;
  return (
    <div className="builder-inspector-stack">
      <fieldset className="catalog-draft-fieldset" disabled={disabled}>
        <SelectField
          stack
          label={label}
          value={value}
          onChange={onSelect}
          options={options}
        />
      </fieldset>
      {catalog.error && (
        <div className="builder-warning">
          {family} catalog unavailable — {catalog.error}{" "}
          <Button disabled={disabled} onClick={() => void catalog.refresh()}>retry</Button>
        </div>
      )}
    </div>
  );
}

function ScalarEditor({
  control,
  disabled,
  mutate,
}: {
  control: BuilderScalarControl;
  disabled: boolean;
  mutate: (commands: ReadonlyArray<BuilderControlMutation>) => Promise<boolean>;
}) {
  const serverValue = scalarText(control.value);
  const [buffer, setBuffer] = useState(serverValue);
  const [invalid, setInvalid] = useState<string | null>(null);
  const debounce = useRef<number | null>(null);
  const submitted = useRef<string | null>(null);
  useEffect(() => {
    setBuffer(serverValue);
    setInvalid(null);
    if (debounce.current !== null) window.clearTimeout(debounce.current);
    debounce.current = null;
    submitted.current = null;
    return () => {
      if (debounce.current !== null) window.clearTimeout(debounce.current);
    };
  }, [control.control_id, serverValue]);

  if (control.scalar_kind === "boolean") {
    return (
      <CheckboxField
        label={control.label}
        checked={control.value === true}
        onChange={(value) => {
          void mutate([{ operation: "set_scalar", control_id: control.control_id, value }]);
        }}
      />
    );
  }

  if (control.scalar_kind === "reference") {
    return (
      <div className="builder-inspector-stack">
        {(control.reference_families ?? []).map((family) => (
          <ReferenceFamilySelect
            key={family}
            family={family}
            label={
              (control.reference_families?.length ?? 0) > 1
                ? `${control.label} · ${family}`
                : control.label
            }
            value={serverValue}
            disabled={disabled}
            onSelect={(value) => {
              void mutate([{
                operation: "set_scalar",
                control_id: control.control_id,
                value,
              }]);
            }}
          />
        ))}
      </div>
    );
  }

  const constraints = constraintSummary(control);
  const commit = (candidate: string) => {
    if (candidate === serverValue || disabled || submitted.current === candidate) return;
    const parsed = parsedScalar(control, candidate);
    if (parsed === null) {
      setInvalid(`${control.label} requires a valid number.`);
      return;
    }
    submitted.current = candidate;
    void mutate([{
      operation: "set_scalar",
      control_id: control.control_id,
      value: parsed,
    }]).then((applied) => {
      if (!applied) {
        submitted.current = null;
        setBuffer(serverValue);
      }
    });
  };
  return (
    <div className="builder-inspector-stack">
      <Field
        stack
        label={control.label}
        value={buffer}
        onChange={(value) => {
          setBuffer(value);
          setInvalid(null);
          if (debounce.current !== null) window.clearTimeout(debounce.current);
          debounce.current = window.setTimeout(() => {
            debounce.current = null;
            commit(value);
          }, 350);
        }}
        onBlur={() => {
          if (debounce.current !== null) window.clearTimeout(debounce.current);
          debounce.current = null;
          commit(buffer);
        }}
      />
      {(control.description || constraints || invalid) && (
        <div className={invalid ? "builder-warning" : "builder-site-derived"}>
          {invalid ?? [control.description, constraints].filter(Boolean).join(" · ")}
        </div>
      )}
    </div>
  );
}

function ChoiceEditor({
  control,
  mutate,
  render,
}: {
  control: BuilderChoiceControl;
  mutate: (commands: ReadonlyArray<BuilderControlMutation>) => Promise<boolean>;
  render: (control: BuilderControl, depth: number) => ReactNode;
}) {
  const selected = control.branches.find((branch) => branch.selected);
  return (
    <div className="builder-inspector-stack">
      <SelectField
        stack
        label={control.label}
        value={selected?.branch_id ?? ""}
        onChange={(branchId) => {
          void mutate([{
            operation: "select_choice",
            control_id: control.control_id,
            branch_id: branchId,
          }]);
        }}
        options={control.branches.map((branch) => ({
          value: branch.branch_id,
          label: branch.label,
        }))}
      />
      {selected?.control && render(selected.control, 1)}
    </div>
  );
}

function SequenceEditor({
  control,
  disabled,
  mutate,
  render,
}: {
  control: BuilderSequenceControl;
  disabled: boolean;
  mutate: (commands: ReadonlyArray<BuilderControlMutation>) => Promise<boolean>;
  render: (control: BuilderControl, depth: number) => ReactNode;
}) {
  const items = control.items ?? [];
  const atMaximum = control.max_items !== null
    && control.max_items !== undefined
    && items.length >= control.max_items;
  return (
    <EditorCard title={control.label} summary={control.description ?? undefined} open>
      {items.length === 0 && (
        <div className="builder-zone-empty">No entries.</div>
      )}
      {items.map((item) => (
        <EditorCard
          key={item.control.json_pointer}
          title={`${control.label} ${item.index + 1}`}
          open
          actions={
            <div className="builder-preset-row">
              {control.can_reorder && item.index > 0 && (
                <Button
                  disabled={disabled}
                  onClick={() => void mutate([{
                    operation: "move_item",
                    control_id: control.control_id,
                    from_index: item.index,
                    to_index: item.index - 1,
                  }])}
                >↑</Button>
              )}
              {control.can_reorder && item.index + 1 < items.length && (
                <Button
                  disabled={disabled}
                  onClick={() => void mutate([{
                    operation: "move_item",
                    control_id: control.control_id,
                    from_index: item.index,
                    to_index: item.index + 1,
                  }])}
                >↓</Button>
              )}
              {control.can_remove && (
                <IconButton
                  icon="x"
                  size={12}
                  label={`Remove ${control.label} ${item.index + 1}`}
                  disabled={disabled}
                  onClick={() => void mutate([{
                    operation: "remove_item",
                    control_id: control.control_id,
                    index: item.index,
                  }])}
                />
              )}
            </div>
          }
        >
          {render(item.control, 1)}
        </EditorCard>
      ))}
      {control.can_add && (
        <Button
          disabled={disabled || atMaximum}
          onClick={() => void mutate([{
            operation: "insert_item",
            control_id: control.control_id,
            index: items.length,
          }])}
        >
          + add {control.label.toLowerCase()} entry
        </Button>
      )}
    </EditorCard>
  );
}

function MapKeyEditor({
  control,
  mapControlId,
  index,
  disabled,
  mutate,
}: {
  control: BuilderScalarControl;
  mapControlId: string;
  index: number;
  disabled: boolean;
  mutate: (commands: ReadonlyArray<BuilderControlMutation>) => Promise<boolean>;
}) {
  const serverValue = scalarText(control.value);
  const [buffer, setBuffer] = useState(serverValue);
  useEffect(() => setBuffer(serverValue), [control.control_id, serverValue]);
  return (
    <Field
      label="key"
      value={buffer}
      onChange={setBuffer}
      onBlur={() => {
        if (disabled || buffer === serverValue || !buffer) return;
        void mutate([{
          operation: "rename_map_key",
          control_id: mapControlId,
          index,
          key: buffer,
        }]).then((applied) => {
          if (!applied) setBuffer(serverValue);
        });
      }}
    />
  );
}

function MapEditor({
  control,
  disabled,
  mutate,
  render,
}: {
  control: BuilderMapControl;
  disabled: boolean;
  mutate: (commands: ReadonlyArray<BuilderControlMutation>) => Promise<boolean>;
  render: (control: BuilderControl, depth: number) => ReactNode;
}) {
  const entries = control.entries ?? [];
  const [newKey, setNewKey] = useState("");
  const [newValue, setNewValue] = useState("");
  const scalarValue = isScalar(control.add_value_control)
    ? parsedScalar(control.add_value_control, newValue)
    : null;
  const needsScalarValue = isScalar(control.add_value_control);
  const atMaximum = control.max_entries !== null
    && control.max_entries !== undefined
    && entries.length >= control.max_entries;
  return (
    <EditorCard title={control.label} summary={control.description ?? undefined} open>
      {entries.length === 0 && <div className="builder-zone-empty">No entries.</div>}
      {entries.map((entry, index) => (
        <EditorCard
          key={entry.value.json_pointer}
          title={`Entry ${index + 1}`}
          open
          actions={
            <IconButton
              icon="x"
              size={12}
              label={`Remove ${control.label} entry ${index + 1}`}
              disabled={disabled}
              onClick={() => void mutate([{
                operation: "remove_map_entry",
                control_id: control.control_id,
                index,
              }])}
            />
          }
        >
          {isScalar(entry.key) && (
            <MapKeyEditor
              control={entry.key}
              mapControlId={control.control_id}
              index={index}
              disabled={disabled}
              mutate={mutate}
            />
          )}
          {render(entry.value, 1)}
        </EditorCard>
      ))}
      <EditorCard title={`Add ${control.label} entry`} open>
        <Field label="key" value={newKey} onChange={setNewKey} />
        {needsScalarValue && (
          <Field label="value" value={newValue} onChange={setNewValue} />
        )}
        <Button
          disabled={
            disabled
            || atMaximum
            || !newKey
            || (needsScalarValue && scalarValue === null)
          }
          onClick={() => {
            void mutate([{
              operation: "insert_map_entry",
              control_id: control.control_id,
              key: newKey,
              value: needsScalarValue ? scalarValue : null,
            }]).then((applied) => {
              if (applied) {
                setNewKey("");
                setNewValue("");
              }
            });
          }}
        >
          + add entry
        </Button>
      </EditorCard>
    </EditorCard>
  );
}

export function GraphicalControlTreeEditor({
  tree,
  disabled = false,
  hideSpecialized = true,
  onMutate,
}: GraphicalControlTreeEditorProps) {
  const mutationActive = useRef(false);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const mutate = async (
    commands: ReadonlyArray<BuilderControlMutation>,
  ): Promise<boolean> => {
    if (disabled || mutationActive.current) return false;
    mutationActive.current = true;
    setPending(true);
    setError(null);
    try {
      await onMutate(commands);
      return true;
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
      return false;
    } finally {
      mutationActive.current = false;
      setPending(false);
    }
  };

  const render = (control: BuilderControl, depth: number): ReactNode => {
    if (hideSpecialized && control.specialized) return null;
    const locked = disabled || pending;
    if (isScalar(control)) {
      return (
        <ScalarEditor
          key={control.json_pointer}
          control={control}
          disabled={locked}
          mutate={mutate}
        />
      );
    }
    if (isChoice(control)) {
      return (
        <ChoiceEditor
          key={control.json_pointer}
          control={control}
          mutate={mutate}
          render={(child, childDepth) => render(child, depth + childDepth)}
        />
      );
    }
    if (isSequence(control)) {
      return (
        <SequenceEditor
          key={control.json_pointer}
          control={control}
          disabled={locked}
          mutate={mutate}
          render={(child, childDepth) => render(child, depth + childDepth)}
        />
      );
    }
    if (isMap(control)) {
      return (
        <MapEditor
          key={control.json_pointer}
          control={control}
          disabled={locked}
          mutate={mutate}
          render={(child, childDepth) => render(child, depth + childDepth)}
        />
      );
    }
    if (isObject(control)) {
      if (control.recursive_reference) {
        return (
          <div className="builder-site-derived" key={control.json_pointer}>
            {control.label} continues recursively after an entry is added.
          </div>
        );
      }
      if (control.empty_parameters) {
        return (
          <div className="builder-site-derived" key={control.json_pointer}>
            {control.label} has no parameters.
          </div>
        );
      }
      const contents = (control.fields ?? []).map((field) => (
        <div key={`${control.json_pointer}:${field.wire_name}`}>
          {render(field.control, depth + 1)}
        </div>
      ));
      if (depth === 0) return <div className="builder-inspector-stack">{contents}</div>;
      return (
        <EditorCard
          key={control.json_pointer}
          title={control.label}
          summary={control.description ?? undefined}
          open
        >
          {contents}
        </EditorCard>
      );
    }
    const unhandled: never = control;
    return unhandled;
  };

  return (
    <div className="builder-inspector-stack" data-testid="graphical-control-tree-editor">
      <fieldset className="catalog-draft-fieldset" disabled={disabled || pending}>
        {render(tree.root, 0)}
      </fieldset>
      {pending && <div className="builder-site-derived">applying graphical edit…</div>}
      {error && <div className="builder-warning">{error}</div>}
    </div>
  );
}
