import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import {
  GraphicalControlTreeEditor,
  type BuilderControlMutation,
} from "../GraphicalControlTreeEditor";
import type { BuilderControlTree, BuilderObjectField } from "../generated/builderApi";

vi.mock("../useBuilderWorld", () => ({
  useBuilderCatalog: (family: string) => ({
    entries: family === "terminals"
      ? [{
          ref: "nodalarc:terminals/rf/selected.yaml",
          namespace: "nodalarc",
          display_name: "Selected terminal",
        }]
      : [],
    error: null,
    refresh: () => Promise.resolve(),
  }),
}));

const controlId = (suffix: string) => `ctl_${suffix.padEnd(32, "0").slice(0, 32)}`;
const optionId = (suffix: string) => `opt_${suffix.padEnd(32, "0").slice(0, 32)}`;

function tree(fields: ReadonlyArray<BuilderObjectField>): BuilderControlTree {
  return {
    projection_revision: 3,
    root: {
      control_id: controlId("root"),
      json_pointer: "",
      label: "Configuration",
      required: true,
      present: true,
      model_name: "tests.Configuration",
      fields,
    },
  };
}

describe("GraphicalControlTreeEditor", () => {
  it("hides specialized fields and commits backend-bound scalar edits on blur", async () => {
    const onMutate = vi.fn(async (_commands: ReadonlyArray<BuilderControlMutation>) => undefined);
    render(
      <GraphicalControlTreeEditor
        tree={tree([
          {
            field_name: "display_name",
            wire_name: "display_name",
            control: {
              control_id: controlId("special"),
              json_pointer: "/body/display_name",
              label: "Display name",
              required: true,
              present: true,
              specialized: true,
              scalar_kind: "text",
              value: "Handled elsewhere",
            },
          },
          {
            field_name: "notes",
            wire_name: "notes",
            control: {
              control_id: controlId("notes"),
              json_pointer: "/body/notes",
              label: "Notes",
              required: false,
              present: true,
              scalar_kind: "text",
              value: "Original",
            },
          },
        ])}
        onMutate={onMutate}
      />,
    );

    expect(screen.queryByLabelText("Display name")).toBeNull();
    const notes = screen.getByLabelText("Notes");
    fireEvent.change(notes, { target: { value: "Preserved and edited" } });
    fireEvent.blur(notes);

    await waitFor(() => expect(onMutate).toHaveBeenCalledWith([{
      operation: "set_scalar",
      control_id: controlId("notes"),
      value: "Preserved and edited",
    }]));
  });

  it("uses typed choices and catalog reference pickers", async () => {
    const onMutate = vi.fn(async (_commands: ReadonlyArray<BuilderControlMutation>) => undefined);
    render(
      <GraphicalControlTreeEditor
        tree={tree([
          {
            field_name: "forwarding",
            wire_name: "forwarding",
            control: {
              control_id: controlId("choice"),
              json_pointer: "/node/forwarding",
              label: "Forwarding",
              required: true,
              present: true,
              branches: [
                {
                  branch_id: optionId("routed"),
                  label: "Routed",
                  branch_kind: "literal",
                  selected: true,
                  literal_value: "routed",
                },
                {
                  branch_id: optionId("host"),
                  label: "Host",
                  branch_kind: "literal",
                  selected: false,
                  literal_value: "host",
                },
              ],
            },
          },
          {
            field_name: "terminal",
            wire_name: "terminal",
            control: {
              control_id: controlId("reference"),
              json_pointer: "/slot/terminal",
              label: "Terminal",
              required: true,
              present: true,
              scalar_kind: "reference",
              value: "nodalarc:terminals/rf/original.yaml",
              reference_families: ["terminals"],
            },
          },
        ])}
        onMutate={onMutate}
      />,
    );

    fireEvent.change(screen.getByLabelText("Forwarding"), {
      target: { value: optionId("host") },
    });
    await waitFor(() => expect(onMutate).toHaveBeenCalledWith([{
      operation: "select_choice",
      control_id: controlId("choice"),
      branch_id: optionId("host"),
    }]));

    fireEvent.change(screen.getByLabelText("Terminal"), {
      target: { value: "nodalarc:terminals/rf/selected.yaml" },
    });
    await waitFor(() => expect(onMutate).toHaveBeenCalledWith([{
      operation: "set_scalar",
      control_id: controlId("reference"),
      value: "nodalarc:terminals/rf/selected.yaml",
    }]));
  });

  it("emits structural sequence and map commands without client-authored documents", async () => {
    const onMutate = vi.fn(async (_commands: ReadonlyArray<BuilderControlMutation>) => undefined);
    render(
      <GraphicalControlTreeEditor
        tree={tree([
          {
            field_name: "tags",
            wire_name: "tags",
            control: {
              control_id: controlId("sequence"),
              json_pointer: "/node/tags",
              label: "Tags",
              required: false,
              present: true,
              items: [{
                index: 0,
                control: {
                  control_id: controlId("tag0"),
                  json_pointer: "/node/tags/0",
                  label: "Item 1",
                  required: true,
                  present: true,
                  scalar_kind: "text",
                  value: "edge",
                },
              }],
              add_item_control: {
                control_id: controlId("tagnew"),
                json_pointer: "/node/tags/-",
                label: "New item",
                required: true,
                present: false,
                scalar_kind: "text",
              },
              can_add: true,
              can_remove: true,
              can_reorder: true,
            },
          },
          {
            field_name: "terminals",
            wire_name: "terminals",
            control: {
              control_id: controlId("map"),
              json_pointer: "/site/nodes/0/terminals",
              label: "Terminals",
              required: true,
              present: true,
              entries: [{
                key: {
                  control_id: controlId("existingkey"),
                  json_pointer: "/site/nodes/0/terminals/access",
                  label: "Key",
                  required: true,
                  present: true,
                  scalar_kind: "text",
                  value: "access",
                },
                value: {
                  control_id: controlId("existingvalue"),
                  json_pointer: "/site/nodes/0/terminals/access",
                  label: "Access",
                  required: true,
                  present: true,
                  model_name: "tests.TerminalInstallation",
                  fields: [],
                },
              }],
              add_key_control: {
                control_id: controlId("mapkey"),
                json_pointer: "/site/nodes/0/terminals",
                label: "New key",
                required: true,
                present: false,
                scalar_kind: "text",
              },
              add_value_control: {
                control_id: controlId("mapvalue"),
                json_pointer: "/site/nodes/0/terminals/-",
                label: "New value",
                required: true,
                present: false,
                model_name: "tests.TerminalInstallation",
                fields: [],
              },
            },
          },
        ])}
        onMutate={onMutate}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "+ add tags entry" }));
    await waitFor(() => expect(onMutate).toHaveBeenCalledWith([{
      operation: "insert_item",
      control_id: controlId("sequence"),
      index: 1,
    }]));

    const [existingKey] = screen.getAllByLabelText("key");
    fireEvent.change(existingKey!, { target: { value: "access-renamed" } });
    fireEvent.blur(existingKey!);
    await waitFor(() => expect(onMutate).toHaveBeenCalledWith([{
      operation: "rename_map_key",
      control_id: controlId("map"),
      index: 0,
      key: "access-renamed",
    }]));

    const addMapCard = screen.getByText("Add Terminals entry").closest(".builder-card");
    const keyInput = addMapCard?.querySelector("input");
    expect(keyInput).not.toBeNull();
    fireEvent.change(keyInput!, { target: { value: "access" } });
    fireEvent.click(screen.getByRole("button", { name: "+ add entry" }));
    await waitFor(() => expect(onMutate).toHaveBeenCalledWith([{
      operation: "insert_map_entry",
      control_id: controlId("map"),
      key: "access",
      value: null,
    }]));
  });
});
