import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { OpenSessionPicker } from "../OpenSessionPicker";
import type {
  CatalogDocumentSummary,
  CatalogImportResult,
} from "../generated/builderApi";

const session = (ref: string): CatalogDocumentSummary => ({
  ref,
  namespace: ref.startsWith("user:") ? "user" : "nodalarc",
  family: "sessions",
  revision: `revision-${ref}`,
  size_bytes: 100,
  display_name: (ref.split("/").pop() ?? ref).replace(/\.ya?ml$/, ""),
  summary: "one exact session closure",
});

const result = (outcome: CatalogImportResult["outcome"]): CatalogImportResult => ({
  outcome,
  generation: "g1",
  document_digest: "document-digest",
  closure_digest: "closure-digest",
  proposed_writes:
    outcome === "proposed" || outcome === "committed"
      ? [
          {
            ref: "user:sessions/imported.yaml",
            family: "sessions",
            exact_yaml: "session: {}\n",
            document_digest: "document-digest",
          },
        ]
      : [],
  identical_refs: [],
  collisions: [],
});

afterEach(cleanup);

describe("OpenSessionPicker typed catalog transfer", () => {
  it("groups typed namespaces and exports the selected exact closure", async () => {
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
    fireEvent.click(screen.getAllByLabelText("Export this session and its exact YAML closure")[0]!);
    expect(onExport).toHaveBeenCalledWith(user);
  });

  it("preflights before an explicit atomic import commit", async () => {
    const payload = { session_ref: "user:sessions/imported.yaml" };
    const onImport = vi
      .fn<(value: unknown, commit: boolean) => Promise<CatalogImportResult>>()
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

    const file = new File([JSON.stringify(payload)], "session.nodalarc-session.json", {
      type: "application/json",
    });
    Object.defineProperty(file, "text", {
      value: () => Promise.resolve(JSON.stringify(payload)),
    });
    fireEvent.change(screen.getByLabelText("import exact YAML closure"), {
      target: { files: [file] },
    });

    const proposed = await screen.findByTestId("session-import-proposed");
    expect(onImport).toHaveBeenNthCalledWith(1, payload, false);
    fireEvent.click(screen.getByRole("button", { name: "Import atomically" }));
    await screen.findByTestId("session-import-committed");
    expect(onImport).toHaveBeenNthCalledWith(2, payload, true);
    expect(proposed).toBeTruthy();
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
