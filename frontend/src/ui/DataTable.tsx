// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
/**
 * Dense operational table primitive — the shared column language for the log
 * window, event log, and future router-output tables. One grid template is
 * shared by header and rows (alignment is structural, not coincidental).
 *
 * Capabilities, per the operational-window contract: click-to-sort headers,
 * drag-to-reorder columns, drag-handle column resize, a flexible last column,
 * row click. Sorting/data stay controlled by the consumer — the table renders
 * and reports; it never owns data semantics.
 */

import { useRef, useState, type ReactNode } from "react";
import { Icon } from "./icons/Icon";

export interface TableColumn<K extends string = string> {
  key: K;
  label: string;
  /** Fixed px width; omit for the flexible column (usually the last). */
  width?: number;
  minWidth?: number;
  sortable?: boolean;
  /** Value renders in the data face by default. */
  mono?: boolean;
}

export interface SortState<K extends string = string> {
  key: K;
  dir: "asc" | "desc";
}

interface DataTableProps<Row, K extends string> {
  columns: readonly TableColumn<K>[];
  /** Column order/width changes (reorder, resize) — controlled. */
  onColumnsChange?: (columns: TableColumn<K>[]) => void;
  rows: readonly Row[];
  rowKey: (row: Row) => string;
  renderCell: (row: Row, key: K) => ReactNode;
  sort?: SortState<K> | null;
  onSortChange?: (sort: SortState<K> | null) => void;
  onRowClick?: (row: Row) => void;
  rowClassName?: (row: Row) => string;
  /** Consumer scroll management (follow-live) reads/controls this element. */
  scrollRef?: React.RefObject<HTMLDivElement | null>;
  label: string;
  emptyText?: string;
}

export function DataTable<Row, K extends string>({
  columns,
  onColumnsChange,
  rows,
  rowKey,
  renderCell,
  sort,
  onSortChange,
  onRowClick,
  rowClassName,
  scrollRef,
  label,
  emptyText = "No rows",
}: DataTableProps<Row, K>) {
  const [dragOver, setDragOver] = useState<K | null>(null);
  const dragKey = useRef<K | null>(null);

  const template = columns
    .map((c) => (c.width !== undefined ? `${c.width}px` : "minmax(0, 1fr)"))
    .join(" ");

  const headerClick = (col: TableColumn<K>) => {
    if (!col.sortable || !onSortChange) return;
    if (!sort || sort.key !== col.key) onSortChange({ key: col.key, dir: "desc" });
    else if (sort.dir === "desc") onSortChange({ key: col.key, dir: "asc" });
    else onSortChange(null);
  };

  const beginResize = (col: TableColumn<K>) => (e: React.PointerEvent) => {
    if (!onColumnsChange || col.width === undefined) return;
    e.preventDefault();
    e.stopPropagation();
    const startX = e.clientX;
    const startW = col.width;
    const min = col.minWidth ?? 40;
    const onMove = (ev: PointerEvent) => {
      const w = Math.max(min, startW + ev.clientX - startX);
      onColumnsChange(columns.map((c) => (c.key === col.key ? { ...c, width: w } : c)));
    };
    const onUp = () => {
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerup", onUp);
    };
    window.addEventListener("pointermove", onMove);
    window.addEventListener("pointerup", onUp);
  };

  const reorder = (from: K, to: K) => {
    if (!onColumnsChange || from === to) return;
    const next = [...columns];
    const fromIdx = next.findIndex((c) => c.key === from);
    const toIdx = next.findIndex((c) => c.key === to);
    if (fromIdx < 0 || toIdx < 0) return;
    const [moved] = next.splice(fromIdx, 1);
    next.splice(toIdx, 0, moved!);
    onColumnsChange(next);
  };

  return (
    <div className="ui-table" role="table" aria-label={label}>
      <div className="ui-table-head" role="row" style={{ gridTemplateColumns: template }}>
        {columns.map((col) => (
          <div
            key={col.key}
            role="columnheader"
            aria-sort={
              sort?.key === col.key ? (sort.dir === "asc" ? "ascending" : "descending") : undefined
            }
            className={`ui-table-th${dragOver === col.key ? " ui-table-th--drag-over" : ""}`}
            draggable={!!onColumnsChange}
            onDragStart={() => {
              dragKey.current = col.key;
            }}
            onDragOver={(e) => {
              e.preventDefault();
              setDragOver(col.key);
            }}
            onDragLeave={() => setDragOver((k) => (k === col.key ? null : k))}
            onDrop={() => {
              if (dragKey.current) reorder(dragKey.current, col.key);
              dragKey.current = null;
              setDragOver(null);
            }}
            onDragEnd={() => {
              dragKey.current = null;
              setDragOver(null);
            }}
          >
            <button
              className="ui-table-th-btn"
              onClick={() => headerClick(col)}
              disabled={!col.sortable || !onSortChange}
            >
              {col.label}
              {sort?.key === col.key && (
                <Icon name={sort.dir === "asc" ? "chevron-up" : "chevron-down"} size={11} className="ui-table-sort" />
              )}
            </button>
            {onColumnsChange && col.width !== undefined && (
              <span className="ui-table-resize" onPointerDown={beginResize(col)} />
            )}
          </div>
        ))}
      </div>
      <div className="ui-table-body" ref={scrollRef}>
        {rows.length === 0 && <div className="ui-table-empty">{emptyText}</div>}
        {rows.map((row) => (
          <div
            key={rowKey(row)}
            role="row"
            className={`ui-table-row${onRowClick ? " ui-table-row--click" : ""} ${rowClassName?.(row) ?? ""}`}
            style={{ gridTemplateColumns: template }}
            onClick={onRowClick ? () => onRowClick(row) : undefined}
          >
            {columns.map((col) => (
              <div key={col.key} role="cell" className={`ui-table-td${col.mono === false ? "" : " ui-table-td--mono"}`}>
                {renderCell(row, col.key)}
              </div>
            ))}
          </div>
        ))}
      </div>
    </div>
  );
}
