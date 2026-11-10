// Copyright 2024-2026 .chance (dotchance)
// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms under AGPL-3.0 Section 7 are in the NOTICE file.
import { describe, it, expect, vi, afterEach } from "vitest";
import { render, screen, fireEvent, cleanup, waitFor } from "@testing-library/react";
import { useState } from "react";
import { DataTable, type TableColumn, type SortState } from "../DataTable";
import { FloatingWindow } from "../FloatingWindow";
import { Tabs } from "../Tabs";

afterEach(cleanup);

const COLUMNS: TableColumn[] = [
  { key: "time", label: "Time", width: 8, sortable: true },
  { key: "msg", label: "Message" },
];

type DemoRow = { id: string; time: string; msg: string };

const ROWS: DemoRow[] = [
  { id: "a", time: "01:00", msg: "first" },
  { id: "b", time: "02:00", msg: "second" },
];

function renderTable(over: { rows?: DemoRow[]; sort?: SortState | null; textSize?: number } = {}) {
  const onSortChange = vi.fn();
  const onColumnsChange = vi.fn();
  render(
    <DataTable
      label="t"
      columns={COLUMNS}
      onColumnsChange={onColumnsChange}
      rows={over.rows ?? ROWS}
      rowKey={(r: DemoRow) => r.id}
      renderCell={(r: DemoRow, k: string) => r[k as keyof DemoRow]}
      sort={over.sort ?? null}
      onSortChange={onSortChange}
      textSize={over.textSize}
    />,
  );
  return { onSortChange, onColumnsChange };
}

describe("DataTable", () => {
  it("sort header cycles desc → asc → off", () => {
    const { onSortChange } = renderTable();
    const th = screen.getByText("Time");
    fireEvent.click(th);
    expect(onSortChange).toHaveBeenLastCalledWith({ key: "time", dir: "desc" });
    cleanup();
    const second = renderTable({ sort: { key: "time", dir: "desc" } as SortState });
    fireEvent.click(screen.getByText("Time"));
    expect(second.onSortChange).toHaveBeenLastCalledWith({ key: "time", dir: "asc" });
    cleanup();
    const third = renderTable({ sort: { key: "time", dir: "asc" } as SortState });
    fireEvent.click(screen.getByText("Time"));
    expect(third.onSortChange).toHaveBeenLastCalledWith(null);
  });

  it("non-sortable headers do not sort", () => {
    const { onSortChange } = renderTable();
    fireEvent.click(screen.getByText("Message"));
    expect(onSortChange).not.toHaveBeenCalled();
  });

  it("sizes text and column widths from its one text size, and resizes in em of it", () => {
    const { onColumnsChange } = renderTable({ textSize: 14 });
    const table = screen.getByRole("table");
    expect(table.style.fontSize).toBe("14px");
    for (const row of screen.getAllByRole("row")) {
      expect(row.style.gridTemplateColumns).toBe("8em minmax(0, 1fr)");
    }

    // 28 px of pointer travel at 14 px text is 2 em.
    const handle = table.querySelector(".ui-table-resize")!;
    handle.dispatchEvent(new MouseEvent("pointerdown", { bubbles: true, clientX: 100 }));
    window.dispatchEvent(new MouseEvent("pointermove", { clientX: 128 }));
    window.dispatchEvent(new MouseEvent("pointerup", {}));
    const next = onColumnsChange.mock.lastCall![0] as TableColumn[];
    expect(next[0]!.width).toBe(10);
  });

  it("drag-and-drop reorders columns", () => {
    const { onColumnsChange } = renderTable();
    const headers = screen.getAllByRole("columnheader");
    fireEvent.dragStart(headers[0]!);
    fireEvent.dragOver(headers[1]!);
    fireEvent.drop(headers[1]!);
    expect(onColumnsChange).toHaveBeenCalledTimes(1);
    const next = onColumnsChange.mock.calls[0]![0] as TableColumn[];
    expect(next.map((c) => c.key)).toEqual(["msg", "time"]);
  });
});

describe("FloatingWindow", () => {
  it("labels the dialog, moves focus inside, and restores its opener", async () => {
    function Fixture() {
      const [open, setOpen] = useState(false);
      return (
        <>
          <button onClick={() => setOpen(true)}>Open logs</button>
          {open && (
            <FloatingWindow
              title="Logs"
              onClose={() => setOpen(false)}
              initial={{ x: 0, y: 0, w: 300, h: 200 }}
            >
              <button>First log action</button>
            </FloatingWindow>
          )}
        </>
      );
    }

    render(<Fixture />);
    const opener = screen.getByRole("button", { name: "Open logs" });
    opener.focus();
    fireEvent.click(opener);
    expect(screen.getByRole("dialog", { name: "Logs" })).toBeTruthy();
    await waitFor(() => {
      expect(screen.getByRole("button", { name: "First log action" })).toBe(document.activeElement);
    });
    fireEvent.keyDown(document, { key: "Escape" });
    expect(screen.queryByRole("dialog", { name: "Logs" })).toBeNull();
    expect(opener).toBe(document.activeElement);
  });
});

describe("Tabs", () => {
  it("selects on click and supports arrow-key navigation", () => {
    const onSelect = vi.fn();
    render(
      <Tabs
        label="demo"
        tabs={[
          { key: "a", label: "Alpha" },
          { key: "b", label: "Beta" },
        ]}
        active="a"
        onSelect={onSelect}
      />,
    );
    fireEvent.click(screen.getByText("Beta"));
    expect(onSelect).toHaveBeenLastCalledWith("b");
    fireEvent.keyDown(screen.getByRole("tablist"), { key: "ArrowRight" });
    expect(onSelect).toHaveBeenLastCalledWith("b");
  });

  it("close affordance fires onClose, not onSelect", () => {
    const onSelect = vi.fn();
    const onClose = vi.fn();
    render(
      <Tabs
        label="demo"
        tabs={[{ key: "a", label: "Session", closable: true }]}
        active="a"
        onSelect={onSelect}
        onClose={onClose}
      />,
    );
    fireEvent.click(screen.getByLabelText("Close Session"));
    expect(onClose).toHaveBeenCalledWith("a");
    expect(onSelect).not.toHaveBeenCalled();
  });
});
