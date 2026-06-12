// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
/**
 * System Logs window — OpsEvents and opt-in per-service debug streams.
 * Chrome comes from FloatingWindow; the column language from DataTable.
 * Geometry persists per window; '/' focuses the search while it is open.
 */

import { useState, useEffect, useRef, useCallback, useMemo } from "react";
import { FloatingWindow } from "../ui/FloatingWindow";
import { DataTable, type SortState, type TableColumn } from "../ui/DataTable";
import { IconButton } from "../ui/Button";
import { Icon } from "../ui/icons/Icon";
import { setSearchTarget } from "../ui/searchFocus";
import { formatTime } from "../translate";
import type { OpsEvent } from "../types";

interface LogPanelProps {
  events: OpsEvent[];
  debugEvents: OpsEvent[];
  debugSources: string[];
  sendMessage: (data: Record<string, unknown>) => void;
  onClose: () => void;
}

const DEBUG_SOURCE_TYPES = ["ome", "scheduler", "node_agent", "operator"] as const;
const LEVELS = ["critical", "error", "warning", "info"] as const;

function levelClass(level: string): string {
  if (level === "critical" || level === "error") return "log-level--fail";
  if (level === "warning") return "log-level--warn";
  if (level === "info") return "log-level--info";
  return "log-level--debug";
}

const LOG_COLUMNS: TableColumn[] = [
  { key: "timestamp", label: "timestamp", width: 90, minWidth: 60, sortable: true },
  { key: "source", label: "source", width: 80, minWidth: 50, sortable: true },
  { key: "level", label: "level", width: 65, minWidth: 45, sortable: true },
  { key: "code", label: "code", width: 100, minWidth: 50, sortable: true },
  { key: "message", label: "message", sortable: true, mono: false },
];


export function LogPanel({ events, debugEvents, debugSources, sendMessage, onClose }: LogPanelProps) {
  const [paused, setPaused] = useState(false);
  const [pausedEvents, setPausedEvents] = useState<OpsEvent[]>([]);
  const [sort, setSort] = useState<SortState | null>({ key: "timestamp", dir: "desc" });
  const [levelFilter, setLevelFilter] = useState<Set<string>>(new Set(LEVELS));
  const [sourceFilter, setSourceFilter] = useState<string>("all");
  const [searchPattern, setSearchPattern] = useState("");
  const [searchValid, setSearchValid] = useState(true);
  const [debugDropdownOpen, setDebugDropdownOpen] = useState(false);
  const [debugPending, setDebugPending] = useState<Set<string>>(new Set());
  const [debugFailed, setDebugFailed] = useState<Set<string>>(new Set());
  const [fontSize, setFontSize] = useState(11);
  const [clearedAt, setClearedAt] = useState<number | null>(null);
  const [logColumns, setLogColumns] = useState(LOG_COLUMNS);

  const scrollRef = useRef<HTMLDivElement | null>(null);
  const autoScrollRef = useRef(true);
  const prevFontSizeRef = useRef(fontSize);
  const debugRootRef = useRef<HTMLDivElement>(null);

  // Column widths track the font scale so resized columns stay proportional.
  useEffect(() => {
    if (prevFontSizeRef.current === fontSize) return;
    const scale = fontSize / prevFontSizeRef.current;
    prevFontSizeRef.current = fontSize;
    const rescale = (cols: TableColumn[]) =>
      cols.map((c) =>
        c.width !== undefined ? { ...c, width: Math.max(c.minWidth ?? 40, Math.round(c.width * scale)) } : c,
      );
    setLogColumns(rescale);
  }, [fontSize]);

  useEffect(() => {
    if (!paused) setPausedEvents([]);
  }, [paused]);

  useEffect(() => {
    if (paused && pausedEvents.length === 0 && events.length > 0) {
      setPausedEvents([...events]);
    }
  }, [paused, pausedEvents.length, events]);

  // Debug enable lifecycle: pending until confirmed in debugSources; failed on
  // the DEBUG_ENABLE_FAILED ops code.
  useEffect(() => {
    if (debugPending.size === 0) return;
    setDebugPending((prev) => {
      const next = new Set(prev);
      for (const s of debugSources) next.delete(s);
      return next.size === prev.size ? prev : next;
    });
  }, [debugSources, debugPending.size]);

  useEffect(() => {
    for (const e of events) {
      if (e.code === "DEBUG_ENABLE_FAILED" && e.message) {
        for (const src of DEBUG_SOURCE_TYPES) {
          if (e.message.includes(src) && debugPending.has(src)) {
            setDebugPending((prev) => { const n = new Set(prev); n.delete(src); return n; });
            setDebugFailed((prev) => { const n = new Set(prev); n.add(src); return n; });
          }
        }
      }
    }
  }, [events, debugPending]);

  useEffect(() => {
    if (!debugDropdownOpen) return;
    const onPointerDown = (e: PointerEvent) => {
      const root = debugRootRef.current;
      if (root && e.target instanceof Node && !root.contains(e.target)) setDebugDropdownOpen(false);
    };
    document.addEventListener("pointerdown", onPointerDown);
    return () => document.removeEventListener("pointerdown", onPointerDown);
  }, [debugDropdownOpen]);

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

  const displayEvents = useMemo(() => {
    const opsSource = paused ? pausedEvents : events;
    const allEvents = debugSources.length > 0 ? [...opsSource, ...debugEvents] : opsSource;
    if (clearedAt === null) return allEvents;
    return allEvents.filter((e) => {
      try {
        return new Date(e.timestamp).getTime() > clearedAt;
      } catch {
        return true;
      }
    });
  }, [paused, pausedEvents, events, debugEvents, debugSources, clearedAt]);

  const sortedLogs = useMemo(() => {
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
    if (sort) {
      const { key, dir } = sort;
      result = [...result].sort((a, b) => {
        const av = a[key as keyof OpsEvent] ?? "";
        const bv = b[key as keyof OpsEvent] ?? "";
        const cmp = av < bv ? -1 : av > bv ? 1 : 0;
        return dir === "asc" ? cmp : -cmp;
      });
    }
    return result;
  }, [displayEvents, levelFilter, sourceFilter, searchRegex, sort]);


  // Follow-live auto-scroll: newest-first sorts pin to the top, otherwise the
  // bottom; manual scrolling away suspends following until scrolled back.
  const newestFirst = sort?.dir === "desc" && sort.key === "timestamp";
  const rowCount = sortedLogs.length;

  useEffect(() => {
    const el = scrollRef.current;
    if (!el || !autoScrollRef.current) return;
    el.scrollTop = newestFirst ? 0 : el.scrollHeight;
  }, [rowCount, newestFirst]);

  const handleScroll = useCallback(() => {
    const el = scrollRef.current;
    if (!el) return;
    autoScrollRef.current = newestFirst
      ? el.scrollTop < 30
      : el.scrollHeight - el.scrollTop - el.clientHeight < 30;
  }, [newestFirst]);

  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    el.addEventListener("scroll", handleScroll);
    return () => el.removeEventListener("scroll", handleScroll);
  }, [handleScroll]);

  const toggleLevel = (level: string) => {
    setLevelFilter((prev) => {
      const next = new Set(prev);
      if (next.has(level)) next.delete(level);
      else next.add(level);
      return next;
    });
  };

  const toggleDebugSource = (source: string) => {
    if (debugSources.includes(source)) {
      sendMessage({ action: "debug_stop", sources: [source] });
    } else {
      setDebugPending((prev) => new Set(prev).add(source));
      setDebugFailed((prev) => { const n = new Set(prev); n.delete(source); return n; });
      sendMessage({ action: "debug_stream", sources: [source] });
    }
  };

  const disableAllDebug = () => {
    sendMessage({ action: "debug_stop_all" });
    setDebugPending(new Set());
    setDebugFailed(new Set());
    setDebugDropdownOpen(false);
  };

  const sources = useMemo(() => Array.from(new Set(events.map((e) => e.source))).sort(), [events]);

  const highlight = useCallback(
    (text: string) => {
      if (!searchRegex) return text;
      const m = text.match(searchRegex);
      if (!m || m.index === undefined) return text;
      return (
        <>
          {text.slice(0, m.index)}
          <mark className="log-mark">{m[0]}</mark>
          {text.slice(m.index + m[0].length)}
        </>
      );
    },
    [searchRegex],
  );

  const title = `System Logs (${sortedLogs.length})`;

  return (
    <FloatingWindow
      title={title}
      onClose={onClose}
      initial={{ x: 100, y: Math.max(0, window.innerHeight - 350 - 60), w: 800, h: 350 }}
      minWidth={500}
      minHeight={200}
      persistKey="logs"
      headerExtras={
        <>
          <IconButton icon="minus" label="Smaller text" onClick={() => setFontSize((s) => Math.max(9, s - 1))} />
          <IconButton icon="plus" label="Larger text" onClick={() => setFontSize((s) => Math.min(16, s + 1))} />
        </>
      }
    >
      <div className="log-toolbar">
        {LEVELS.map((lvl) => (
              <button
                key={lvl}
                onClick={() => toggleLevel(lvl)}
                className={`log-chip ${levelClass(lvl)}${levelFilter.has(lvl) ? " log-chip--on" : ""}`}
              >
                {lvl}
              </button>
            ))}
            <span className="log-sep" />
            <div className="log-debug" ref={debugRootRef}>
              <button
                onClick={() => setDebugDropdownOpen((v) => !v)}
                className={`log-chip log-level--debug${debugSources.length > 0 ? " log-chip--on" : ""}`}
                aria-expanded={debugDropdownOpen}
              >
                {debugSources.length > 0 ? `debug (${debugSources.length})` : "debug"}
                <Icon name="chevron-down" size={10} />
              </button>
              {debugDropdownOpen && (
                <div className="log-debug-menu">
                  <div className="log-debug-head">Enable debug output:</div>
                  {DEBUG_SOURCE_TYPES.map((src) => {
                    const active = debugSources.includes(src);
                    const pending = debugPending.has(src);
                    const failed = debugFailed.has(src);
                    return (
                      <button key={src} className="log-debug-row" onClick={() => toggleDebugSource(src)}>
                        <span className={`log-debug-state${failed ? " log-level--fail" : active ? " log-debug-state--on" : ""}`}>
                          <Icon
                            name={active ? "circle-check" : pending ? "history" : failed ? "circle-x" : "minus"}
                            size={12}
                          />
                        </span>
                        {src.replace("_", " ")}
                        {pending && <small>enabling…</small>}
                        {failed && <small>failed</small>}
                      </button>
                    );
                  })}
                  {debugSources.length > 0 && (
                    <button className="log-debug-row log-level--fail" onClick={disableAllDebug}>
                      Disable all
                    </button>
                  )}
                </div>
              )}
            </div>
            <span className="log-sep" />
            <select className="log-select" value={sourceFilter} onChange={(e) => setSourceFilter(e.target.value)}>
              <option value="all">All sources</option>
              {sources.map((s) => (
                <option key={s} value={s}>{s}</option>
              ))}
        </select>
        <span className="log-sep" />
        <input
          ref={setSearchTarget}
          type="text"
          placeholder="Search (regex)… /"
          value={searchPattern}
          onChange={(e) => setSearchPattern(e.target.value)}
          className={`log-search${searchValid ? "" : " log-search--invalid"}`}
        />
        {!searchValid && <span className="log-invalid">invalid regex</span>}
        <span className="log-spring" />
        <button className="log-chip" onClick={() => setClearedAt(Date.now())} title="Hide rows older than now">
          clear
        </button>
        <button
          className={`log-chip${paused ? " log-chip--on log-level--warn" : ""}`}
          onClick={() => setPaused((p) => !p)}
          title={paused ? "Resume live feed" : "Pause live feed"}
        >
          <Icon name={paused ? "play" : "pause"} size={10} />
          {paused ? "resume" : "pause"}
        </button>
      </div>
      <div className="log-table" style={{ fontSize }}>
        <DataTable
            label="System logs"
            columns={logColumns}
            onColumnsChange={setLogColumns}
            rows={sortedLogs}
            rowKey={(e) => `${e.timestamp}-${e.source}-${e.code}-${e.message}`}
            sort={sort}
            onSortChange={setSort}
            scrollRef={scrollRef}
            emptyText={events.length === 0 ? "No events received" : "No events match filters"}
            rowClassName={(e) => (e.level === "critical" || e.level === "error" ? "log-row--fail" : "")}
            renderCell={(e, key) => {
              if (key === "timestamp") return formatTime(e.timestamp);
              if (key === "level") return <span className={levelClass(e.level)}>{e.level}</span>;
              if (key === "message") {
                return (
                  <span title={e.details ? JSON.stringify(e.details) : e.message}>{highlight(e.message)}</span>
                );
              }
              return highlight(String(e[key as keyof OpsEvent] ?? ""));
            }}
          />
      </div>
    </FloatingWindow>
  );
}
