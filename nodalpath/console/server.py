"""FastAPI operator console for NodalPath.

Serves on NODALPATH_CONSOLE_PORT (3100), bound to 0.0.0.0 (network-accessible).
No authentication — deploy behind a firewall or on a private interface.

The dashboard is a single inline HTML page that auto-refreshes state from
/api/status every 1 second via JavaScript fetch. No npm, no Vite, no React.
(React + D3 topology graph comes in Chunk 5b.)
"""

from __future__ import annotations

import logging

from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse

from nodalpath.console.state import ConsoleState

log = logging.getLogger(__name__)


def build_app(state: ConsoleState) -> FastAPI:
    """Build and return the FastAPI application.

    Args:
        state: The shared ConsoleState instance written by LiveOrchestrator.

    Returns:
        A configured FastAPI app ready to be served by uvicorn.
    """
    app = FastAPI(title="NodalPath Console", docs_url=None, redoc_url=None)

    # ── REST endpoints ───────────────────────────────────────────────────────

    @app.get("/api/health")
    async def health() -> JSONResponse:
        return JSONResponse({"status": "ok"})

    @app.get("/api/status")
    async def status() -> JSONResponse:
        return JSONResponse(state.snapshot())

    @app.get("/api/almanac")
    async def almanac() -> JSONResponse:
        snap = state.snapshot()
        return JSONResponse(snap["almanac_history"])

    @app.get("/api/pushes")
    async def pushes() -> JSONResponse:
        snap = state.snapshot()
        return JSONResponse(snap["push_history"])

    @app.get("/api/deviations")
    async def deviations() -> JSONResponse:
        snap = state.snapshot()
        return JSONResponse(snap["deviation_history"])

    @app.get("/api/events")
    async def events() -> JSONResponse:
        """Unified event log, newest-first."""
        snap = state.snapshot()
        return JSONResponse(snap["event_log"])

    @app.post("/api/recompute")
    async def recompute() -> JSONResponse:
        state.request_recompute()
        log.info("Manual recompute requested via console")
        return JSONResponse({"ok": True})

    # ── HTML Dashboard ───────────────────────────────────────────────────────

    @app.get("/", response_class=HTMLResponse)
    async def dashboard() -> HTMLResponse:
        snap = state.snapshot()
        return HTMLResponse(_render_dashboard(snap))

    return app


# ── Dashboard rendering ──────────────────────────────────────────────────────

_EVENT_TYPE_CLASS = {
    "TRANSITION": "ev-transition",
    "PUSH":       "ev-push",
    "DEVIATE":    "ev-deviate",
    "RECOMPUTE":  "ev-recompute",
}

_DASHBOARD_CSS = """
:root {
  --bg-body:    #0d0d1a;
  --bg-panel:   #1a1a2e;
  --bg-topbar:  #16162a;
  --text-pri:   #e0e0e0;
  --text-sec:   #888899;
  --text-acc:   #00d4aa;
  --divider:    #2a2a4e;
  --green:      #00d4aa;
  --amber:      #f5a623;
  --red:        #e74c3c;
  --blue:       #4a9eff;
  --font:       "JetBrains Mono", "Fira Code", "Source Code Pro", monospace;
}

*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }

body {
  background: var(--bg-body);
  color: var(--text-pri);
  font-family: var(--font);
  font-size: 13px;
  min-height: 100vh;
}

/* ── Top bar ── */
#topbar {
  background: var(--bg-topbar);
  border-bottom: 1px solid var(--divider);
  padding: 0 20px;
  height: 48px;
  display: flex;
  align-items: center;
  gap: 24px;
}
#topbar .brand { color: var(--text-acc); font-size: 15px; font-weight: 700; letter-spacing: 0.04em; }
#topbar .meta  { color: var(--text-sec); font-size: 12px; }
#topbar .spacer { flex: 1; }

/* Status pill */
.pill {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 3px 10px;
  border-radius: 12px;
  font-size: 11px;
  font-weight: 600;
  letter-spacing: 0.06em;
  text-transform: uppercase;
}
.pill .dot { width: 7px; height: 7px; border-radius: 50%; }
.pill-live   { background: #00d4aa22; color: var(--green); }
.pill-live   .dot { background: var(--green); }
.pill-idle   { background: #88889922; color: var(--text-sec); }
.pill-idle   .dot { background: var(--text-sec); }
.pill-error  { background: #e74c3c22; color: var(--red); }
.pill-error  .dot { background: var(--red); }

/* Recompute button */
#btn-recompute {
  background: #2a2a4e;
  color: var(--text-acc);
  border: 1px solid var(--divider);
  border-radius: 6px;
  padding: 5px 14px;
  font-family: var(--font);
  font-size: 12px;
  cursor: pointer;
  letter-spacing: 0.04em;
}
#btn-recompute:hover { background: #3a3a5e; border-color: var(--text-acc); }
#btn-recompute:active { transform: scale(0.97); }
#btn-recompute.working { color: var(--amber); border-color: var(--amber); }

/* ── Stats row ── */
#stats {
  display: flex;
  gap: 1px;
  background: var(--divider);
  border-bottom: 1px solid var(--divider);
}
.stat-cell {
  background: var(--bg-panel);
  padding: 14px 20px;
  flex: 1;
}
.stat-cell .label { color: var(--text-sec); font-size: 11px; text-transform: uppercase; letter-spacing: 0.08em; margin-bottom: 6px; }
.stat-cell .value { color: var(--text-pri); font-size: 22px; }
.stat-cell .value.accent { color: var(--text-acc); }
.stat-cell .value.warn   { color: var(--amber); }
.stat-cell .value.danger { color: var(--red); }

/* ── Event log ── */
#event-log-wrap {
  padding: 16px 20px;
}
#event-log-wrap h2 {
  color: var(--text-sec);
  font-size: 11px;
  text-transform: uppercase;
  letter-spacing: 0.08em;
  margin-bottom: 10px;
}
#event-table {
  width: 100%;
  border-collapse: collapse;
}
#event-table th {
  text-align: left;
  color: var(--text-sec);
  font-size: 11px;
  font-weight: 400;
  text-transform: uppercase;
  letter-spacing: 0.06em;
  padding: 0 10px 8px 0;
  border-bottom: 1px solid var(--divider);
}
#event-table td {
  padding: 7px 10px 7px 0;
  border-bottom: 1px solid #1f1f35;
  vertical-align: top;
  white-space: nowrap;
}
#event-table td.summary { white-space: normal; }
#event-table tr:hover td { background: #1f1f35; }

/* Event type badges */
.badge {
  display: inline-block;
  padding: 1px 7px;
  border-radius: 4px;
  font-size: 10px;
  font-weight: 700;
  letter-spacing: 0.08em;
  text-transform: uppercase;
}
.ev-transition { background: #4a9eff22; color: var(--blue); }
.ev-push       { background: #00d4aa22; color: var(--green); }
.ev-deviate    { background: #e74c3c22; color: var(--red); }
.ev-recompute  { background: #f5a62322; color: var(--amber); }

.ts { color: var(--text-sec); font-size: 11px; }

/* ── Footer ── */
#footer {
  color: var(--text-sec);
  font-size: 11px;
  padding: 12px 20px;
  border-top: 1px solid var(--divider);
  display: flex;
  gap: 24px;
}
#refresh-indicator { transition: opacity 0.3s; }
"""

_DASHBOARD_JS = """
let _refreshTimer = null;
let _recomputing = false;

function _wallTimestamp(iso) {
  if (!iso) return '\u2014';
  const d = new Date(iso);
  return d.toLocaleTimeString([], {hour12: false, hour:'2-digit', minute:'2-digit', second:'2-digit'});
}

function _badge(evType) {
  const cls = {TRANSITION:'ev-transition', PUSH:'ev-push', DEVIATE:'ev-deviate', RECOMPUTE:'ev-recompute'}[evType] || '';
  return `<span class="badge ${cls}">${evType}</span>`;
}

function _updateStats(s) {
  document.getElementById('s-nodes').textContent       = s.nodes_in_registry;
  document.getElementById('s-transitions').textContent = s.transition_count;
  document.getElementById('s-pushes').textContent      = s.push_history ? s.push_history.length : 0;
  const devEl = document.getElementById('s-deviations');
  devEl.textContent = s.deviation_count;
  devEl.className = 'value' + (s.deviation_count > 0 ? ' warn' : ' accent');
  const lastEl = document.getElementById('s-lastsim');
  lastEl.textContent = s.last_sim_time ? s.last_sim_time.substring(11, 19) : '\u2014';
}

function _updateEventLog(events) {
  const tbody = document.getElementById('event-tbody');
  if (!events || events.length === 0) {
    tbody.innerHTML = '<tr><td colspan="3" style="color:var(--text-sec);padding:20px 0">No events yet.</td></tr>';
    return;
  }
  const rows = events.slice(0, 80).map(ev => {
    const ts = _wallTimestamp(ev.wall_time);
    return `<tr>
      <td class="ts">${ts}</td>
      <td>${_badge(ev.event_type)}</td>
      <td class="summary">${_esc(ev.summary || '')}</td>
    </tr>`;
  });
  tbody.innerHTML = rows.join('');
}

function _esc(s) {
  return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}

async function _refresh() {
  const ind = document.getElementById('refresh-indicator');
  ind.style.opacity = '1';
  try {
    const r = await fetch('/api/status');
    if (!r.ok) return;
    const s = await r.json();
    _updateStats(s);
    _updateEventLog(s.event_log);
    ind.style.opacity = '0';
  } catch(e) {
    ind.textContent = '\\u26a0 disconnected';
    ind.style.opacity = '1';
  }
}

async function triggerRecompute() {
  if (_recomputing) return;
  _recomputing = true;
  const btn = document.getElementById('btn-recompute');
  btn.classList.add('working');
  btn.textContent = 'Requesting\\u2026';
  try {
    await fetch('/api/recompute', {method: 'POST'});
  } finally {
    setTimeout(() => {
      btn.classList.remove('working');
      btn.textContent = 'Recompute';
      _recomputing = false;
    }, 1500);
  }
}

document.addEventListener('DOMContentLoaded', () => {
  _refresh();
  _refreshTimer = setInterval(_refresh, 1000);
});
"""


def _render_dashboard(snap: dict) -> str:
    """Render the full HTML dashboard string from a ConsoleState snapshot."""
    nodes = snap.get("nodes_in_registry", 0)
    transitions = snap.get("transition_count", 0)
    deviations = snap.get("deviation_count", 0)
    recomputes = snap.get("recomputation_count", 0)
    transport = snap.get("transport", "\u2014")
    dry_run = snap.get("dry_run", False)
    session = snap.get("session_path", "\u2014")
    last_sim = snap.get("last_sim_time") or "\u2014"
    push_history = snap.get("push_history", [])
    dev_class = "warn" if deviations > 0 else "accent"
    dry_tag = ' <span style="color:var(--amber)">[dry-run]</span>' if dry_run else ""

    # Build event log rows for initial HTML render (JS takes over on first refresh)
    event_log = snap.get("event_log", [])
    event_rows = ""
    if event_log:
        for ev in event_log[:80]:
            wt = ev.get("wall_time", "")
            ts = wt[11:19] if len(wt) >= 19 else "\u2014"
            ev_type = ev.get("event_type", "")
            badge_cls = _EVENT_TYPE_CLASS.get(ev_type, "")
            summary = ev.get("summary", "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            event_rows += (
                f'<tr><td class="ts">{ts}</td>'
                f'<td><span class="badge {badge_cls}">{ev_type}</span></td>'
                f'<td class="summary">{summary}</td></tr>'
            )
    else:
        event_rows = '<tr><td colspan="3" style="color:var(--text-sec);padding:20px 0">No events yet.</td></tr>'

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>NodalPath Console</title>
<style>{_DASHBOARD_CSS}</style>
</head>
<body>

<div id="topbar">
  <span class="brand">NodalPath</span>
  <span class="meta">session: {session}{dry_tag}</span>
  <span class="meta">transport: {transport}</span>
  <span class="spacer"></span>
  <span class="pill pill-live"><span class="dot"></span>LIVE</span>
  <button id="btn-recompute" onclick="triggerRecompute()">Recompute</button>
</div>

<div id="stats">
  <div class="stat-cell">
    <div class="label">Nodes</div>
    <div class="value accent" id="s-nodes">{nodes}</div>
  </div>
  <div class="stat-cell">
    <div class="label">Transitions</div>
    <div class="value" id="s-transitions">{transitions}</div>
  </div>
  <div class="stat-cell">
    <div class="label">Pushes</div>
    <div class="value accent" id="s-pushes">{len(push_history)}</div>
  </div>
  <div class="stat-cell">
    <div class="label">Deviations</div>
    <div class="value {dev_class}" id="s-deviations">{deviations}</div>
  </div>
  <div class="stat-cell">
    <div class="label">Recomputes</div>
    <div class="value" id="s-recomputes">{recomputes}</div>
  </div>
  <div class="stat-cell">
    <div class="label">Last Sim Time</div>
    <div class="value accent" id="s-lastsim" style="font-size:14px">{last_sim[11:19] if len(last_sim) >= 19 else last_sim}</div>
  </div>
</div>

<div id="event-log-wrap">
  <h2>Event Log</h2>
  <table id="event-table">
    <thead>
      <tr>
        <th style="width:72px">Time</th>
        <th style="width:110px">Type</th>
        <th>Summary</th>
      </tr>
    </thead>
    <tbody id="event-tbody">
      {event_rows}
    </tbody>
  </table>
</div>

<div id="footer">
  <span>NodalPath Operator Console</span>
  <span id="refresh-indicator" style="opacity:0">\u21bb</span>
</div>

<script>{_DASHBOARD_JS}</script>
</body>
</html>"""
