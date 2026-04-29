// Copyright 2024-2026 .chance (dotchance)
// Licensed under the NodalArc Source Available License 1.0. See LICENSE file.
/** Floating system log panel — OpsEvents from NATS via VS-API WebSocket. */

import { useState, useEffect, useRef, useCallback, useMemo } from "react";
import type { OpsEvent } from "../types";

interface LogPanelProps {
  events: OpsEvent[];
  onClose: () => void;
}

type SortField = "timestamp" | "source" | "level" | "code" | "message";
type SortDir = "asc" | "desc";

const LEVELS = ["critical", "error", "warning", "info", "debug"] as const;
const LEVEL_COLORS: Record<string, string> = {
  critical: "var(--accent-red)",
  error: "var(--accent-red)",
  warning: "#ffaa33",
  info: "var(--accent-blue)",
  debug: "var(--text-secondary)",
};

const MIN_WIDTH = 500;
const MIN_HEIGHT = 200;
const DEFAULT_WIDTH = 800;
const DEFAULT_HEIGHT = 350;

const EDGE_SIZE = 6;

const COLUMNS: { field: SortField; label: string; defaultWidth: number; minWidth: number }[] = [
  { field: "timestamp", label: "timestamp", defaultWidth: 90, minWidth: 60 },
  { field: "source", label: "source", defaultWidth: 80, minWidth: 50 },
  { field: "level", label: "level", defaultWidth: 65, minWidth: 45 },
  { field: "code", label: "code", defaultWidth: 100, minWidth: 50 },
  { field: "message", label: "message", defaultWidth: 0, minWidth: 100 },
];

export function LogPanel({ events, onClose }: LogPanelProps) {
  const [paused, setPaused] = useState(false);
  const [pausedEvents, setPausedEvents] = useState<OpsEvent[]>([]);
  const [sortField, setSortField] = useState<SortField>("timestamp");
  const [sortDir, setSortDir] = useState<SortDir>("desc");
  const [levelFilter, setLevelFilter] = useState<Set<string>>(new Set(LEVELS));
  const [sourceFilter, setSourceFilter] = useState<string>("all");
  const [searchPattern, setSearchPattern] = useState("");
  const [searchValid, setSearchValid] = useState(true);
  const [fontSize, setFontSize] = useState(11);
  const [cleared, setCleared] = useState(false);
  const [clearedAt, setClearedAt] = useState(0);

  const [pos, setPos] = useState({ x: 100, y: window.innerHeight - DEFAULT_HEIGHT - 60 });
  const [size, setSize] = useState({ w: DEFAULT_WIDTH, h: DEFAULT_HEIGHT });
  const [colWidths, setColWidths] = useState<number[]>(COLUMNS.map((c) => c.defaultWidth));

  const panelRef = useRef<HTMLDivElement>(null);
  const tableBodyRef = useRef<HTMLDivElement>(null);
  const dragRef = useRef<{ startX: number; startY: number; startPosX: number; startPosY: number } | null>(null);
  const resizeRef = useRef<{ startX: number; startY: number; startW: number; startH: number; edge: string } | null>(null);
  const colResizeRef = useRef<{ startX: number; colIdx: number; startWidth: number } | null>(null);
  const autoScrollRef = useRef(true);

  useEffect(() => {
    if (!paused) {
      setPausedEvents([]);
    }
  }, [paused]);

  const displayEvents = useMemo(() => {
    const source = paused ? pausedEvents : events;
    if (!cleared) return source;
    return source.filter((e) => {
      try {
        return new Date(e.timestamp).getTime() > clearedAt;
      } catch {
        return true;
      }
    });
  }, [paused, pausedEvents, events, cleared, clearedAt]);

  useEffect(() => {
    if (paused && pausedEvents.length === 0 && events.length > 0) {
      setPausedEvents([...events]);
    }
  }, [paused, pausedEvents.length, events]);

  const searchRegex = useMemo(() => {
    if (!searchPattern) return null;
    try {
      const re = new RegExp(searchPattern, "i");
      setSearchValid(true);
      return re;
    } catch {
      setSearchValid(false);
      return null;
    }
  }, [searchPattern]);

  const filtered = useMemo(() => {
    let result = displayEvents.filter((e) => {
      if (!levelFilter.has(e.level)) return false;
      if (sourceFilter !== "all" && e.source !== sourceFilter) return false;
      return true;
    });
    if (searchRegex) {
      result = result.filter(
        (e) => searchRegex.test(e.message) || searchRegex.test(e.code) || searchRegex.test(e.source),
      );
    }
    return result;
  }, [displayEvents, levelFilter, sourceFilter, searchRegex]);

  const sorted = useMemo(() => {
    const copy = [...filtered];
    copy.sort((a, b) => {
      const av = a[sortField] ?? "";
      const bv = b[sortField] ?? "";
      const cmp = av < bv ? -1 : av > bv ? 1 : 0;
      return sortDir === "asc" ? cmp : -cmp;
    });
    return copy;
  }, [filtered, sortField, sortDir]);

  useEffect(() => {
    if (autoScrollRef.current && tableBodyRef.current) {
      tableBodyRef.current.scrollTop = tableBodyRef.current.scrollHeight;
    }
  }, [sorted]);

  const handleScroll = useCallback(() => {
    if (!tableBodyRef.current) return;
    const { scrollTop, scrollHeight, clientHeight } = tableBodyRef.current;
    autoScrollRef.current = scrollHeight - scrollTop - clientHeight < 30;
  }, []);

  const handleSort = useCallback(
    (field: SortField) => {
      if (sortField === field) {
        setSortDir((d) => (d === "asc" ? "desc" : "asc"));
      } else {
        setSortField(field);
        setSortDir("desc");
      }
    },
    [sortField],
  );

  const toggleLevel = useCallback((level: string) => {
    setLevelFilter((prev) => {
      const next = new Set(prev);
      if (next.has(level)) next.delete(level);
      else next.add(level);
      return next;
    });
  }, []);

  const sources = useMemo(() => {
    const s = new Set(events.map((e) => e.source));
    return Array.from(s).sort();
  }, [events]);

  const handleClear = useCallback(() => {
    setCleared(true);
    setClearedAt(Date.now());
  }, []);

  // Drag handling (title bar)
  const handleDragStart = useCallback(
    (e: React.MouseEvent) => {
      e.preventDefault();
      dragRef.current = { startX: e.clientX, startY: e.clientY, startPosX: pos.x, startPosY: pos.y };
      const onMove = (ev: MouseEvent) => {
        if (!dragRef.current) return;
        setPos({
          x: dragRef.current.startPosX + (ev.clientX - dragRef.current.startX),
          y: dragRef.current.startPosY + (ev.clientY - dragRef.current.startY),
        });
      };
      const onUp = () => {
        dragRef.current = null;
        document.removeEventListener("mousemove", onMove);
        document.removeEventListener("mouseup", onUp);
      };
      document.addEventListener("mousemove", onMove);
      document.addEventListener("mouseup", onUp);
    },
    [pos],
  );

  // Edge resize handling — all four edges and corners
  const handleEdgeResizeStart = useCallback(
    (e: React.MouseEvent, edge: string) => {
      e.preventDefault();
      e.stopPropagation();
      resizeRef.current = { startX: e.clientX, startY: e.clientY, startW: size.w, startH: size.h, edge };
      const startPos = { ...pos };
      const onMove = (ev: MouseEvent) => {
        if (!resizeRef.current) return;
        const dx = ev.clientX - resizeRef.current.startX;
        const dy = ev.clientY - resizeRef.current.startY;
        const ed = resizeRef.current.edge;

        let newW = resizeRef.current.startW;
        let newH = resizeRef.current.startH;
        let newX = startPos.x;
        let newY = startPos.y;

        if (ed.includes("right")) newW = Math.max(MIN_WIDTH, resizeRef.current.startW + dx);
        if (ed.includes("bottom")) newH = Math.max(MIN_HEIGHT, resizeRef.current.startH + dy);
        if (ed.includes("left")) {
          newW = Math.max(MIN_WIDTH, resizeRef.current.startW - dx);
          if (newW > MIN_WIDTH) newX = startPos.x + dx;
        }
        if (ed.includes("top")) {
          newH = Math.max(MIN_HEIGHT, resizeRef.current.startH - dy);
          if (newH > MIN_HEIGHT) newY = startPos.y + dy;
        }

        setSize({ w: newW, h: newH });
        setPos({ x: newX, y: newY });
      };
      const onUp = () => {
        resizeRef.current = null;
        document.removeEventListener("mousemove", onMove);
        document.removeEventListener("mouseup", onUp);
      };
      document.addEventListener("mousemove", onMove);
      document.addEventListener("mouseup", onUp);
    },
    [size, pos],
  );

  // Column resize handling
  const handleColResizeStart = useCallback(
    (e: React.MouseEvent, colIdx: number) => {
      e.preventDefault();
      e.stopPropagation();
      const col = COLUMNS[colIdx]!;
      colResizeRef.current = { startX: e.clientX, colIdx, startWidth: colWidths[colIdx] ?? col.defaultWidth };
      const onMove = (ev: MouseEvent) => {
        if (!colResizeRef.current) return;
        const dx = ev.clientX - colResizeRef.current.startX;
        const newWidth = Math.max(col.minWidth, colResizeRef.current.startWidth + dx);
        setColWidths((prev) => {
          const next = [...prev];
          next[colResizeRef.current!.colIdx] = newWidth;
          return next;
        });
      };
      const onUp = () => {
        colResizeRef.current = null;
        document.removeEventListener("mousemove", onMove);
        document.removeEventListener("mouseup", onUp);
      };
      document.addEventListener("mousemove", onMove);
      document.addEventListener("mouseup", onUp);
    },
    [colWidths],
  );

  const highlightText = useCallback(
    (text: string) => {
      if (!searchRegex) return text;
      const parts = text.split(searchRegex);
      const matches = text.match(searchRegex);
      if (!matches || parts.length <= 1) return text;
      return parts.reduce<(string | React.ReactElement)[]>((acc, part, i) => {
        acc.push(part as unknown as React.ReactElement);
        if (i < matches.length) {
          acc.push(
            <span key={i} style={{ background: "rgba(255,200,0,0.3)", color: "#ffe066" }}>
              {matches[i]}
            </span>,
          );
        }
        return acc;
      }, []);
    },
    [searchRegex],
  );

  const formatTime = (ts: string) => {
    try {
      return new Date(ts).toISOString().slice(11, 23);
    } catch {
      return ts;
    }
  };

  const sortIndicator = (field: SortField) => {
    if (sortField !== field) return null;
    return sortDir === "asc" ? " ▲" : " ▼";
  };

  const gridCols = colWidths.map((w, i) => (i === colWidths.length - 1 ? "1fr" : `${w}px`)).join(" ");

  const edgeCursor: Record<string, string> = {
    top: "ns-resize",
    bottom: "ns-resize",
    left: "ew-resize",
    right: "ew-resize",
    "top-left": "nwse-resize",
    "top-right": "nesw-resize",
    "bottom-left": "nesw-resize",
    "bottom-right": "nwse-resize",
  };

  return (
    <div
      ref={panelRef}
      className="log-panel"
      style={{
        position: "fixed",
        left: pos.x,
        top: pos.y,
        width: size.w,
        height: size.h,
        zIndex: 9999,
        display: "flex",
        flexDirection: "column",
        background: "rgba(13,13,26,0.96)",
        backdropFilter: "blur(12px)",
        border: "1px solid var(--border)",
        borderRadius: 6,
        boxShadow: "0 8px 32px rgba(0,0,0,0.6)",
        fontFamily: "var(--font-family)",
        fontSize,
        color: "var(--text-primary)",
        overflow: "hidden",
      }}
    >
      {/* Edge resize handles */}
      {["top", "bottom", "left", "right", "top-left", "top-right", "bottom-left", "bottom-right"].map((edge) => {
        const s: React.CSSProperties = {
          position: "absolute",
          zIndex: 10,
          cursor: edgeCursor[edge],
        };
        if (edge === "top") { s.top = 0; s.left = EDGE_SIZE; s.right = EDGE_SIZE; s.height = EDGE_SIZE; }
        else if (edge === "bottom") { s.bottom = 0; s.left = EDGE_SIZE; s.right = EDGE_SIZE; s.height = EDGE_SIZE; }
        else if (edge === "left") { s.left = 0; s.top = EDGE_SIZE; s.bottom = EDGE_SIZE; s.width = EDGE_SIZE; }
        else if (edge === "right") { s.right = 0; s.top = EDGE_SIZE; s.bottom = EDGE_SIZE; s.width = EDGE_SIZE; }
        else if (edge === "top-left") { s.top = 0; s.left = 0; s.width = EDGE_SIZE * 2; s.height = EDGE_SIZE * 2; }
        else if (edge === "top-right") { s.top = 0; s.right = 0; s.width = EDGE_SIZE * 2; s.height = EDGE_SIZE * 2; }
        else if (edge === "bottom-left") { s.bottom = 0; s.left = 0; s.width = EDGE_SIZE * 2; s.height = EDGE_SIZE * 2; }
        else if (edge === "bottom-right") { s.bottom = 0; s.right = 0; s.width = EDGE_SIZE * 2; s.height = EDGE_SIZE * 2; }
        return <div key={edge} style={s} onMouseDown={(e) => handleEdgeResizeStart(e, edge)} />;
      })}

      {/* Title bar — draggable */}
      <div
        onMouseDown={handleDragStart}
        style={{
          display: "flex",
          alignItems: "center",
          gap: 8,
          padding: "6px 10px",
          background: "var(--bg-bar)",
          borderBottom: "1px solid var(--border)",
          cursor: "move",
          userSelect: "none",
          flexShrink: 0,
        }}
      >
        <span style={{ fontWeight: 600, fontSize: 12 }}>System Logs</span>
        <span style={{ color: "var(--text-secondary)", fontSize: 10 }}>
          {filtered.length}/{displayEvents.length} events
        </span>
        <div style={{ flex: 1 }} />
        <button
          onClick={handleClear}
          title="Clear log"
          style={{
            background: "transparent",
            border: "1px solid var(--border)",
            borderRadius: 3,
            color: "var(--text-secondary)",
            padding: "2px 8px",
            cursor: "pointer",
            fontSize: 10,
          }}
        >
          Clear
        </button>
        <button
          onClick={() => setPaused((p) => !p)}
          title={paused ? "Resume" : "Pause"}
          style={{
            background: paused ? "rgba(255,170,51,0.2)" : "transparent",
            border: "1px solid var(--border)",
            borderRadius: 3,
            color: paused ? "#ffaa33" : "var(--text-secondary)",
            padding: "2px 8px",
            cursor: "pointer",
            fontSize: 10,
          }}
        >
          {paused ? "▶ Resume" : "⏸ Pause"}
        </button>
        <span style={{ display: "flex", alignItems: "center", gap: 0 }}>
          <button
            onClick={() => setFontSize((s) => Math.max(9, s - 1))}
            title="Decrease font size"
            style={{
              background: "none",
              border: "1px solid var(--border)",
              borderRadius: "3px 0 0 3px",
              color: "var(--text-secondary)",
              fontSize: 10,
              cursor: "pointer",
              padding: "2px 6px",
            }}
          >A-</button>
          <button
            onClick={() => setFontSize((s) => Math.min(18, s + 1))}
            title="Increase font size"
            style={{
              background: "none",
              border: "1px solid var(--border)",
              borderLeft: "none",
              borderRadius: "0 3px 3px 0",
              color: "var(--text-secondary)",
              fontSize: 12,
              cursor: "pointer",
              padding: "2px 6px",
            }}
          >A+</button>
        </span>
        <button
          onClick={onClose}
          title="Close"
          style={{
            background: "transparent",
            border: "none",
            color: "var(--text-secondary)",
            cursor: "pointer",
            fontSize: 14,
            padding: "0 4px",
            lineHeight: 1,
          }}
        >
          ×
        </button>
      </div>

      {/* Filter bar */}
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: 6,
          padding: "4px 10px",
          borderBottom: "1px solid var(--border)",
          flexShrink: 0,
          flexWrap: "wrap",
        }}
      >
        {LEVELS.map((lvl) => (
          <button
            key={lvl}
            onClick={() => toggleLevel(lvl)}
            style={{
              background: levelFilter.has(lvl) ? `${LEVEL_COLORS[lvl]}22` : "transparent",
              border: `1px solid ${levelFilter.has(lvl) ? LEVEL_COLORS[lvl] : "var(--border)"}`,
              borderRadius: 3,
              color: levelFilter.has(lvl) ? LEVEL_COLORS[lvl] : "var(--text-dim)",
              padding: "1px 6px",
              cursor: "pointer",
              fontSize: 10,
              textTransform: "uppercase",
            }}
          >
            {lvl}
          </button>
        ))}
        <span style={{ color: "var(--border)" }}>|</span>
        <select
          value={sourceFilter}
          onChange={(e) => setSourceFilter(e.target.value)}
          style={{
            background: "var(--bg-panel)",
            border: "1px solid var(--border)",
            borderRadius: 3,
            color: "var(--text-primary)",
            fontSize: 10,
            padding: "2px 4px",
          }}
        >
          <option value="all">All sources</option>
          {sources.map((s) => (
            <option key={s} value={s}>
              {s}
            </option>
          ))}
        </select>
        <span style={{ color: "var(--border)" }}>|</span>
        <input
          type="text"
          placeholder="Search (regex)..."
          value={searchPattern}
          onChange={(e) => setSearchPattern(e.target.value)}
          style={{
            background: "var(--bg-panel)",
            border: `1px solid ${searchValid ? "var(--border)" : "var(--accent-red)"}`,
            borderRadius: 3,
            color: "var(--text-primary)",
            fontSize: 10,
            padding: "2px 6px",
            width: 160,
            outline: "none",
          }}
        />
        {!searchValid && (
          <span style={{ color: "var(--accent-red)", fontSize: 10 }}>invalid regex</span>
        )}
      </div>

      {/* Table header with resizable columns */}
      <div
        style={{
          display: "grid",
          gridTemplateColumns: gridCols,
          gap: 0,
          padding: "4px 10px",
          borderBottom: "1px solid var(--border)",
          background: "var(--bg-panel)",
          flexShrink: 0,
          userSelect: "none",
        }}
      >
        {COLUMNS.map((col, idx) => (
          <div
            key={col.field}
            style={{ position: "relative", display: "flex", alignItems: "center" }}
          >
            <div
              onClick={() => handleSort(col.field)}
              style={{
                cursor: "pointer",
                color: sortField === col.field ? "var(--accent-blue)" : "var(--text-secondary)",
                fontSize: 10,
                fontWeight: 600,
                textTransform: "uppercase",
                whiteSpace: "nowrap",
                overflow: "hidden",
                flex: 1,
              }}
            >
              {col.label}
              {sortIndicator(col.field)}
            </div>
            {idx < COLUMNS.length - 1 && (
              <div
                onMouseDown={(e) => handleColResizeStart(e, idx)}
                className="col-resize-handle"
                style={{
                  position: "absolute",
                  right: -5,
                  top: 0,
                  bottom: 0,
                  width: 10,
                  cursor: "col-resize",
                  zIndex: 5,
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "center",
                }}
              >
                <div style={{
                  width: 2,
                  height: "60%",
                  background: "var(--border)",
                  borderRadius: 1,
                  opacity: 0.5,
                }} />
              </div>
            )}
          </div>
        ))}
      </div>

      {/* Table body — scrollable */}
      <div
        ref={tableBodyRef}
        onScroll={handleScroll}
        style={{
          flex: 1,
          overflowY: "auto",
          overflowX: "hidden",
          minHeight: 0,
        }}
      >
        {sorted.length === 0 ? (
          <div
            style={{
              padding: 20,
              textAlign: "center",
              color: "var(--text-dim)",
            }}
          >
            {events.length === 0 ? "No events received" : "No events match filters"}
          </div>
        ) : (
          sorted.map((e, i) => (
            <div
              key={`${e.timestamp}-${e.code}-${i}`}
              style={{
                display: "grid",
                gridTemplateColumns: gridCols,
                gap: 0,
                padding: "2px 10px",
                borderBottom: "1px solid rgba(255,255,255,0.03)",
                fontSize,
                lineHeight: `${fontSize + 7}px`,
                cursor: e.details ? "help" : undefined,
              }}
              title={e.details ? JSON.stringify(e.details) : undefined}
            >
              <span style={{ color: "var(--text-secondary)", whiteSpace: "nowrap" }}>
                {formatTime(e.timestamp)}
              </span>
              <span style={{ color: "var(--text-secondary)" }}>{highlightText(e.source)}</span>
              <span
                style={{
                  color: LEVEL_COLORS[e.level] ?? "var(--text-primary)",
                  fontWeight: e.level === "error" || e.level === "critical" ? 600 : 400,
                }}
              >
                {e.level}
              </span>
              <span style={{ color: "var(--text-secondary)" }}>{highlightText(e.code)}</span>
              <span
                style={{
                  whiteSpace: "nowrap",
                  overflow: "hidden",
                  textOverflow: "ellipsis",
                }}
              >
                {highlightText(e.message)}
              </span>
            </div>
          ))
        )}
      </div>
    </div>
  );
}
