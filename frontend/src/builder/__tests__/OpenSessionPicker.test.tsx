import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { OpenSessionPicker } from "../OpenSessionPicker";
import type {
  CatalogDocumentSummary,
  CatalogSessionYamlImportResult,
  CatalogYamlImportFile,
} from "../generated/builderApi";

const session = (ref: string): CatalogDocumentSummary => ({
  ref,
  namespace: ref.startsWith("user:") ? "user" : "nodalarc",
  family: "sessions",
  revision: `revision-${ref}`,
  size_bytes: 100,
  display_name: (ref.split("/").pop() ?? ref).replace(/\.ya?ml$/, ""),
  summary: "one session",
});

const result = (
  outcome: CatalogSessionYamlImportResult["outcome"],
): CatalogSessionYamlImportResult => ({
  root_ref: "user:sessions/imported.yaml",
  outcome,
  generation: "g1",
  proposed_writes:
    outcome === "proposed" || outcome === "committed"
      ? [
          {
            ref: "user:sessions/imported.yaml",
            family: "sessions",
            logical_path: "catalog/user/sessions/imported.yaml",
            canonical_yaml: "session: {}\n",
            canonicalization_changed: true,
          },
        ]
      : [],
  identical_refs: [],
  collisions: [],
  ...(outcome === "proposed" ? { proposal_token: "proposal-token" } : {}),
} as CatalogSessionYamlImportResult);

afterEach(cleanup);

describe("OpenSessionPicker YAML transfer", () => {
  it("groups typed namespaces and exports the selected YAML files", async () => {
    const user = session("user:sessions/mine.yaml");
    const shipped = session("nodalarc:sessions/example.yaml");
    const onExport = vi.fn(() => Promise.resolve());
    render(
      <OpenSessionPicker
        sessions={[user, shipped]}
        sessionsError={null}
        openError={null}
        onOpen={() => undefined}
        onExport={onExport}
        onImport={() => Promise.resolve(result("unchanged"))}
      />,
    );

    expect(screen.getByText("★ yours")).toBeTruthy();
    expect(screen.getByText("nodalarc library")).toBeTruthy();
    fireEvent.click(screen.getAllByLabelText("Export this session as YAML files")[0]!);
    expect(onExport).toHaveBeenCalledWith(user);
  });

  it("preflights before an explicit atomic import commit", async () => {
    const yamlFiles = [
      "session:\n  name: imported\n",
      "terminal:\n  id: imported-terminal\n",
    ];
    const onImport = vi
      .fn<
        (
          value: readonly CatalogYamlImportFile[],
          proposalToken: string | null,
        ) => Promise<CatalogSessionYamlImportResult>
      >()
      .mockResolvedValueOnce(result("proposed"))
      .mockResolvedValueOnce(result("committed"));
    render(
      <OpenSessionPicker
        sessions={[]}
        sessionsError={null}
        openError={null}
        onOpen={() => undefined}
        onExport={() => Promise.resolve()}
        onImport={onImport}
      />,
    );

    const sessionFile = new File([yamlFiles[0]!], "session.yaml", {
      type: "text/yaml",
    });
    const terminalFile = new File([yamlFiles[1]!], "terminal.yml", {
      type: "text/yaml",
    });
    Object.defineProperty(sessionFile, "text", {
      value: () => Promise.resolve(yamlFiles[0]),
    });
    Object.defineProperty(terminalFile, "text", {
      value: () => Promise.resolve(yamlFiles[1]),
    });
    fireEvent.change(screen.getByLabelText("import session YAML files"), {
      target: { files: [sessionFile, terminalFile] },
    });

    const proposed = await screen.findByTestId("session-import-proposed");
    const importFiles = yamlFiles.map((yaml_text) => ({ yaml_text }));
    expect(onImport).toHaveBeenNthCalledWith(1, importFiles, null);
    expect(proposed.textContent).toContain("user:sessions/imported.yaml");
    expect(proposed.textContent).toContain("catalog/user/sessions/imported.yaml");
    expect(proposed.textContent).toContain("comments or formatting will be canonicalized");
    fireEvent.click(screen.getByRole("button", { name: "Import atomically" }));
    await screen.findByTestId("session-import-committed");
    expect(onImport).toHaveBeenNthCalledWith(2, importFiles, "proposal-token");
    expect(proposed).toBeTruthy();
  });

  it("keeps a stale proposal refusal visible instead of silently reproposing", async () => {
    const yaml = "session:\n  name: imported\n";
    const onImport = vi
      .fn<
        (
          value: readonly CatalogYamlImportFile[],
          proposalToken: string | null,
        ) => Promise<CatalogSessionYamlImportResult>
      >()
      .mockResolvedValueOnce(result("proposed"))
      .mockRejectedValueOnce(new Error("the reviewed YAML proposal is stale"));
    render(
      <OpenSessionPicker
        sessions={[]}
        sessionsError={null}
        openError={null}
        onOpen={() => undefined}
        onExport={() => Promise.resolve()}
        onImport={onImport}
      />,
    );
    const file = new File([yaml], "session.yaml", { type: "text/yaml" });
    Object.defineProperty(file, "text", { value: () => Promise.resolve(yaml) });
    fireEvent.change(screen.getByLabelText("import session YAML files"), {
      target: { files: [file] },
    });
    await screen.findByTestId("session-import-proposed");
    fireEvent.click(screen.getByRole("button", { name: "Import atomically" }));
    expect(await screen.findByText("the reviewed YAML proposal is stale")).toBeTruthy();
    expect(onImport).toHaveBeenNthCalledWith(2, [{ yaml_text: yaml }], "proposal-token");
    expect(screen.queryByRole("button", { name: "Import atomically" })).toBeNull();
  });

  it("preserves nested directory hints and encoded double-underscore paths", async () => {
    const sessionYaml = "session:\n  name: imported\n";
    const firstYaml = "node:\n  id: shared\n";
    const secondYaml = "node:\n  id: shared\n  display_name: second\n";
    const onImport = vi.fn(() => Promise.resolve(result("proposed")));
    render(
      <OpenSessionPicker
        sessions={[]}
        sessionsError={null}
        openError={null}
        onOpen={() => undefined}
        onExport={() => Promise.resolve()}
        onImport={onImport}
      />,
    );
    const sessionFile = new File([sessionYaml], "imported.yaml");
    const first = new File([firstYaml], "shared.yaml");
    const encoded = new File(
      [secondYaml],
      "catalog%2Fuser%2Fnodes%2Fa__b%2Fshared.yaml",
    );
    Object.defineProperties(sessionFile, {
      text: { value: () => Promise.resolve(sessionYaml) },
      webkitRelativePath: {
        value: "imported-nodalarc-session/catalog/user/sessions/imported.yaml",
      },
    });
    Object.defineProperties(first, {
      text: { value: () => Promise.resolve(firstYaml) },
      webkitRelativePath: {
        value: "imported-nodalarc-session/catalog/user/nodes/first/shared.yaml",
      },
    });
    Object.defineProperty(encoded, "text", { value: () => Promise.resolve(secondYaml) });

    fireEvent.change(screen.getByLabelText("import YAML directory"), {
      target: { files: [sessionFile, first, encoded] },
    });
    await screen.findByTestId("session-import-proposed");
    expect(onImport).toHaveBeenCalledWith([
      {
        yaml_text: sessionYaml,
        logical_path_hint: "catalog/user/sessions/imported.yaml",
      },
      {
        yaml_text: firstYaml,
        logical_path_hint: "catalog/user/nodes/first/shared.yaml",
      },
      {
        yaml_text: secondYaml,
        logical_path_hint: "catalog/user/nodes/a__b/shared.yaml",
      },
    ], null);
  });

  it("requires an available explicit user target for a shipped session copy", () => {
    const shipped = session("nodalarc:sessions/example.yaml");
    const occupied = session("user:sessions/example-copy.yaml");
    const onOpen = vi.fn();
    render(
      <OpenSessionPicker
        sessions={[occupied, shipped]}
        sessionsError={null}
        openError={null}
        onOpen={onOpen}
        onExport={() => Promise.resolve()}
        onImport={() => Promise.resolve(result("unchanged"))}
      />,
    );

    fireEvent.click(screen.getByTitle("Open example"));
    const target = screen.getByTestId("shipped-session-target");
    const targetInput = within(target).getByRole("textbox") as HTMLInputElement;
    expect(targetInput.value).toBe(
      "example-copy-2",
    );
    expect(target.textContent).toContain("sets its formal session.name");
    expect(target.textContent).toContain("Every other session field is preserved");
    fireEvent.change(targetInput, {
      target: { value: "my-example" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Open editable copy" }));
    expect(onOpen).toHaveBeenCalledWith(shipped, "user:sessions/my-example.yaml");
  });
});
