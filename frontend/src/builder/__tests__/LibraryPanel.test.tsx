import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

const sessionEntry = {
  ref: "user:sessions/saved-session.yaml",
  family: "sessions" as const,
  namespace: "user" as const,
  revision: "session-revision",
  size_bytes: 128,
  display_name: "Saved session",
  summary: "A user-authored session",
};

vi.mock("../useBuilderWorld", () => ({
  claimLibraryReveal: () => null,
  deleteUserObject: vi.fn(),
  exportCatalogObject: vi.fn(),
  useBuilderBootstrap: () => ({
    bootstrap: {
      contract_version: 1,
      public_grammar_href: "/docs/ops/configuration-grammar.md",
      capabilities: { user_catalog_write: true, deploy_yaml_closure: true },
      families: [
        {
          family: "constellations",
          wrapper: "constellation",
          direct_user_write: true,
          component_fork: true,
          session_draft_save: false,
          suggested_object_id: "my-constellation",
        },
        {
          family: "sessions",
          wrapper: null,
          direct_user_write: false,
          component_fork: false,
          session_draft_save: true,
          suggested_object_id: null,
        },
      ],
      scheduling_presets: [
        { id: "leo-fast-handover", label: "LEO fast handover — make-before-break" },
        { id: "geo-longest-pass", label: "GEO longest pass — break-before-make" },
      ],
    },
    error: null,
    refresh: vi.fn(),
  }),
  useBuilderCatalog: (family: string) => ({
    entries: family === "sessions" ? [sessionEntry] : [],
    error: null,
    refresh: vi.fn(),
  }),
  useLibraryReveal: () => null,
}));

const { LibraryPanel, presentationForFamily } = await import("../LibraryPanel");

describe("LibraryPanel backend family registry", () => {
  it("renders backend-advertised families even without custom presentation metadata", () => {
    render(
      <LibraryPanel
        onUse={vi.fn()}
        onCustomize={vi.fn()}
        onInspect={vi.fn()}
        onNew={vi.fn()}
      />,
    );

    expect(screen.getAllByRole("tab").map((tab) => tab.textContent)).toEqual([
      "Constellations",
      "Sessions",
    ]);
    fireEvent.click(screen.getByRole("tab", { name: "Sessions" }));
    expect(screen.getByText("★ Saved session")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "+ new" })).toBeNull();
  });

  it("uses a neutral non-authoring fallback for an uncustomized family", () => {
    expect(presentationForFamily("sessions")).toEqual({
      family: "sessions",
      label: "Sessions",
      icon: "layers",
      tone: "component",
      useTitle: null,
      editor: false,
    });
  });
});
