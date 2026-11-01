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
import type {
  DebugSourceControl,
  OpsLevel,
  ServiceSource,
  StampedOpsEvent,
  StateSocketCommand,
} from "../generated/vsApi";

interface LogPanelProps {
  /** VS-API's ops and debug events; each carries the arrival seq that identifies it. */
  events: readonly StampedOpsEvent[];
  debugEvents: readonly StampedOpsEvent[];
  /** VS-API's seq space; a new token means VS-API restarted and seq began again at 1. */
  opsLogToken: string | null;
  debugControl: readonly DebugSourceControl[];
  debugSubscriptionOpen: boolean;
  sendMessage: (command: StateSocketCommand) => void;
  onClose: () => void;
}

/** Why a request did not take, in the words the menu shows. */
function debugFailure(control: DebugSourceControl): string | null {
  switch (control.outcome) {
    case "acknowledged":
      return null;
    case "timed_out":
      return "no reply";
    case "no_responders":
      return "not running";
    case "rejected":
    case "failed":
      return control.error;
  }
}

const DEBUG_SOURCE_TYPES: readonly ServiceSource[] = [
  "ome",
  "scheduler",
  "node_agent",
  "operator",
  "vs_api",
];
/** The level chips; debug rows have their own control. */
const LEVELS: readonly OpsLevel[] = ["critical", "error", "warning", "info"];

const LEVEL_CLASS: Record<OpsLevel, string> = {
  critical: "log-level--fail",
  error: "log-level--fail",
  warning: "log-level--warn",
  info: "log-level--info",
  debug: "log-level--debug",
};

const LOG_COLUMNS: TableColumn[] = [
  { key: "timestamp", label: "timestamp", width: 90, minWidth: 60, sortable: true },
  { key: "source", label: "source", width: 80, minWidth: 50, sortable: true },
  { key: "level", label: "level", width: 65, minWidth: 45, sortable: true },
  { key: "code", label: "code", width: 100, minWidth: 50, sortable: true },
  { key: "message", label: "message", sortable: true, mono: false },
];


export function LogPanel({
  events,
  debugEvents,
  opsLogToken,
  debugControl,
  debugSubscriptionOpen,
  sendMessage,
  onClose,
}: LogPanelProps) {
  const [paused, setPaused] = useState(false);
  const [pausedEvents, setPausedEvents] = useState<StampedOpsEvent[]>([]);
  const [sort, setSort] = useState<SortState | null>({ key: "timestamp", dir: "desc" });
  // The levels the user hid; a row is dropped only when its level is hidden.
  const [hiddenLevels, setHiddenLevels] = useState<ReadonlySet<OpsLevel>>(new Set());
  const [sourceFilter, setSourceFilter] = useState<string>("all");
  const [searchPattern, setSearchPattern] = useState("");
  const [searchValid, setSearchValid] = useState(true);
  const [debugDropdownOpen, setDebugDropdownOpen] = useState(false);
  // An enable the page sent, by source: the request_seq VS-API showed for that source
  // when it was sent. It is pending until a later request's outcome arrives.
  const [debugPending, setDebugPending] = useState<Map<ServiceSource, number>>(new Map());
  const [fontSize, setFontSize] = useState(11);
  // A clear hides every row VS-API had sent when it was pressed: the newest seq then,
  // in the seq space of that token. Event timestamps come from the servers' clocks, so
  // they cannot be compared with the browser's.
  const [cleared, setCleared] = useState<{ token: string | null; throughSeq: number } | null>(null);
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

  // VS-API's last debug control request to each service, and what came back.
  const controlBySource = useMemo(
    () => new Map(debugControl.map((control) => [control.source, control])),
    [debugControl],
  );
  const debugOn = (source: ServiceSource) => {
    const control = controlBySource.get(source);
    return control?.request === "enable" && control.outcome === "acknowledged";
  };
  const debugActive = DEBUG_SOURCE_TYPES.filter(debugOn);

  useEffect(() => {
    if (debugPending.size === 0) return;
    setDebugPending((prev) => {
      const next = new Map(prev);
      for (const [source, sentAfter] of prev) {
        if ((controlBySource.get(source)?.request_seq ?? 0) > sentAfter) next.delete(source);
      }
      return next.size === prev.size ? prev : next;
    });
  }, [controlBySource, debugPending.size]);

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
    const allEvents = debugActive.length > 0 ? [...opsSource, ...debugEvents] : opsSource;
    // Under another token every row shown arrived after the clear.
    if (cleared === null || cleared.token !== opsLogToken) return allEvents;
    return allEvents.filter((e) => e.seq > cleared.throughSeq);
  }, [paused, pausedEvents, events, debugEvents, debugActive.length, cleared, opsLogToken]);

  const sortedLogs = useMemo(() => {
    let result = displayEvents.filter((e) => {
      if (hiddenLevels.has(e.level)) return false;
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
        // Timestamps compare as times (VS-API writes fractions only when they are
        // non-zero, so the strings do not order); events in one instant keep VS-API's
        // arrival order.
        let cmp: number;
        if (key === "timestamp") {
          cmp = Date.parse(a.timestamp) - Date.parse(b.timestamp) || a.seq - b.seq;
        } else {
          const av = a[key as keyof StampedOpsEvent] ?? "";
          const bv = b[key as keyof StampedOpsEvent] ?? "";
          cmp = av < bv ? -1 : av > bv ? 1 : 0;
        }
        return dir === "asc" ? cmp : -cmp;
      });
    }
    return result;
  }, [displayEvents, hiddenLevels, sourceFilter, searchRegex, sort]);


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

  const toggleLevel = (level: OpsLevel) => {
    setHiddenLevels((prev) => {
      const next = new Set(prev);
      if (next.has(level)) next.delete(level);
      else next.add(level);
      return next;
    });
  };

  const toggleDebugSource = (source: ServiceSource) => {
    if (debugOn(source)) {
      sendMessage({ action: "debug_stop", sources: [source] });
    } else {
      const sentAfter = controlBySource.get(source)?.request_seq ?? 0;
      setDebugPending((prev) => new Map(prev).set(source, sentAfter));
      sendMessage({ action: "debug_stream", sources: [source] });
    }
  };

  const disableAllDebug = () => {
    sendMessage({ action: "debug_stop_all" });
    setDebugPending(new Map());
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
                className={`log-chip ${LEVEL_CLASS[lvl]}${hiddenLevels.has(lvl) ? "" : " log-chip--on"}`}
              >
                {lvl}
              </button>
            ))}
            <span className="log-sep" />
            <div className="log-debug" ref={debugRootRef}>
              <button
                onClick={() => setDebugDropdownOpen((v) => !v)}
                className={`log-chip log-level--debug${debugActive.length > 0 ? " log-chip--on" : ""}`}
                aria-expanded={debugDropdownOpen}
              >
                {debugActive.length > 0 ? `debug (${debugActive.length})` : "debug"}
                <Icon name="chevron-down" size={10} />
              </button>
              {debugDropdownOpen && (
                <div className="log-debug-menu">
                  <div className="log-debug-head">Enable debug output:</div>
                  {debugActive.length > 0 && !debugSubscriptionOpen && (
                    <div className="log-debug-head log-level--fail">
                      VS-API is not reading the debug stream
                    </div>
                  )}
                  {DEBUG_SOURCE_TYPES.map((src) => {
                    const control = controlBySource.get(src);
                    const active = debugOn(src);
                    const pending = debugPending.has(src);
                    const failure = !pending && control ? debugFailure(control) : null;
                    const failed = failure !== null;
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
                        {active && control?.outcome === "acknowledged" && (
                          <small>one instance at {control.level}</small>
                        )}
                        {failed && (
                          <small>
                            {control?.request} failed: {failure}
                          </small>
                        )}
                      </button>
                    );
                  })}
                  {debugActive.length > 0 && (
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
        <button
          className="log-chip"
          onClick={() =>
            setCleared({
              token: opsLogToken,
              throughSeq: Math.max(0, ...events.map((e) => e.seq), ...debugEvents.map((e) => e.seq)),
            })
          }
          title="Hide the rows received so far"
        >
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
            // VS-API's seq identifies each event; two events alike in every shown
            // field are still two rows.
            rowKey={(e) => String(e.seq)}
            sort={sort}
            onSortChange={setSort}
            scrollRef={scrollRef}
            emptyText={events.length === 0 ? "No events received" : "No events match filters"}
            rowClassName={(e) => (e.level === "critical" || e.level === "error" ? "log-row--fail" : "")}
            renderCell={(e, key) => {
              if (key === "timestamp") return formatTime(e.timestamp);
              if (key === "level") return <span className={LEVEL_CLASS[e.level]}>{e.level}</span>;
              if (key === "message") {
                return (
                  <span title={e.details ? JSON.stringify(e.details) : e.message}>{highlight(e.message)}</span>
                );
              }
              return highlight(String(e[key as keyof StampedOpsEvent] ?? ""));
            }}
          />
      </div>
    </FloatingWindow>
  );
}
