// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
import { describe, it, expect, vi, afterEach } from "vitest";
import { render, screen, fireEvent, cleanup } from "@testing-library/react";
import { DataTable, type TableColumn, type SortState } from "../DataTable";
import { FloatingWindow } from "../FloatingWindow";
import { Tabs } from "../Tabs";

afterEach(cleanup);

const COLUMNS: TableColumn[] = [
  { key: "time", label: "Time", width: 90, sortable: true },
  { key: "msg", label: "Message" },
];

type DemoRow = { id: string; time: string; msg: string };

const ROWS: DemoRow[] = [
  { id: "a", time: "01:00", msg: "first" },
  { id: "b", time: "02:00", msg: "second" },
];

function renderTable(over: { rows?: DemoRow[]; sort?: SortState | null } = {}) {
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
    />,
  );
  return { onSortChange, onColumnsChange };
}

describe("DataTable", () => {
  it("renders rows and cells", () => {
    renderTable();
    expect(screen.getByText("first")).toBeTruthy();
    expect(screen.getByText("second")).toBeTruthy();
  });

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

  it("shows the empty state", () => {
    renderTable({ rows: [] });
    expect(screen.getByText("No rows")).toBeTruthy();
  });
});

describe("FloatingWindow", () => {
  it("renders title and content, and the close button closes", () => {
    const onClose = vi.fn();
    render(
      <FloatingWindow title="Logs" onClose={onClose} initial={{ x: 0, y: 0, w: 300, h: 200 }}>
        <div>body</div>
      </FloatingWindow>,
    );
    expect(screen.getByText("Logs")).toBeTruthy();
    expect(screen.getByText("body")).toBeTruthy();
    fireEvent.click(screen.getByLabelText("Close"));
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("renders all eight resize edges", () => {
    const { container } = render(
      <FloatingWindow title="W" onClose={vi.fn()} initial={{ x: 0, y: 0, w: 300, h: 200 }}>
        x
      </FloatingWindow>,
    );
    expect(container.querySelectorAll(".ui-window-edge")).toHaveLength(8);
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
