"""FastAPI operator console for NodalPath.

Serves on port 3100 (NODALPATH_CONSOLE_PORT), bound to 0.0.0.0.
"""

from __future__ import annotations

import logging

from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse

from nodalpath.console.state import ConsoleState

log = logging.getLogger(__name__)

_DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>NodalPath Operator Console</title>
<style>
  body { font-family: monospace; background: #0d1117; color: #c9d1d9; margin: 0; padding: 16px; }
  h1 { color: #58a6ff; margin: 0 0 8px; font-size: 1.2em; }
  h2 { color: #8b949e; font-size: 0.9em; margin: 16px 0 4px; text-transform: uppercase; letter-spacing: 0.1em; }
  .header { display: flex; justify-content: space-between; align-items: baseline; border-bottom: 1px solid #21262d; padding-bottom: 8px; margin-bottom: 12px; }
  .badge { display: inline-block; padding: 2px 8px; border-radius: 4px; font-size: 0.8em; }
  .badge-green { background: #1a4731; color: #3fb950; }
  .badge-yellow { background: #4b3000; color: #d29922; }
  .badge-red { background: #4b1317; color: #f85149; }
  .stats { display: flex; gap: 24px; margin-bottom: 12px; }
  .stat { text-align: center; }
  .stat-value { font-size: 1.6em; color: #58a6ff; }
  .stat-label { font-size: 0.75em; color: #8b949e; }
  table { width: 100%; border-collapse: collapse; font-size: 0.8em; }
  th { color: #8b949e; text-align: left; padding: 4px 8px; border-bottom: 1px solid #21262d; }
  td { padding: 3px 8px; border-bottom: 1px solid #161b22; }
  .ok { color: #3fb950; }
  .fail { color: #f85149; }
  .btn { background: #21262d; color: #c9d1d9; border: 1px solid #30363d; padding: 6px 14px; cursor: pointer; border-radius: 4px; font-family: monospace; font-size: 0.85em; }
  .btn:hover { background: #30363d; }
  #status-bar { font-size: 0.75em; color: #8b949e; margin-top: 8px; }
  .section { margin-bottom: 16px; }
  .empty { color: #8b949e; font-size: 0.8em; padding: 8px; }
</style>
</head>
<body>
<div class="header">
  <h1>NodalPath Operator Console</h1>
  <div id="session-label" style="font-size:0.8em;color:#8b949e"></div>
</div>
<div class="stats">
  <div class="stat"><div class="stat-value" id="transitions">—</div><div class="stat-label">Transitions</div></div>
  <div class="stat"><div class="stat-value" id="deviations">—</div><div class="stat-label">Deviations</div></div>
  <div class="stat"><div class="stat-value" id="recomputations">—</div><div class="stat-label">Recomputations</div></div>
  <div class="stat"><div class="stat-value" id="nodes">—</div><div class="stat-label">Nodes</div></div>
</div>
<div class="section">
  <button class="btn" onclick="triggerRecompute()">Manual Recompute</button>
  <span id="recompute-status" style="margin-left:8px;font-size:0.8em;color:#8b949e"></span>
</div>
<div class="section">
  <h2>Recent Pushes</h2>
  <table id="push-table">
    <thead><tr><th>Sim Time</th><th>State ID</th><th>Attempted</th><th>OK</th><th>Failed</th><th>ms</th></tr></thead>
    <tbody id="push-body"></tbody>
  </table>
</div>
<div class="section">
  <h2>Deviations</h2>
  <table id="dev-table">
    <thead><tr><th>Sim Time</th><th>Node A</th><th>Node B</th><th>Reason</th><th>State ID</th></tr></thead>
    <tbody id="dev-body"></tbody>
  </table>
</div>
<div class="section">
  <h2>Almanac History (last 20)</h2>
  <table id="alm-table">
    <thead><tr><th>Sim Time</th><th>State ID</th><th>Links</th><th>Tables</th></tr></thead>
    <tbody id="alm-body"></tbody>
  </table>
</div>
<div id="status-bar">Connecting...</div>
<script>
function shortId(id) { return id ? id.slice(0, 12) + '...' : '—'; }
function shortTime(t) { return t ? t.replace('T', ' ').slice(0, 19) : '—'; }

async function poll() {
  try {
    const r = await fetch('/api/status');
    const d = await r.json();
    document.getElementById('transitions').textContent = d.transition_count ?? '—';
    document.getElementById('deviations').textContent = d.deviation_count ?? '—';
    document.getElementById('recomputations').textContent = d.recomputation_count ?? '—';
    document.getElementById('nodes').textContent = d.nodes_in_registry ?? '—';
    document.getElementById('session-label').textContent = d.session_path ? d.session_path.split('/').pop() : '';

    const pb = document.getElementById('push-body');
    pb.innerHTML = '';
    (d.push_history || []).slice().reverse().slice(0, 20).forEach(p => {
      const tr = document.createElement('tr');
      const ok = p.nodes_failed === 0;
      tr.innerHTML = `<td>${shortTime(p.sim_time)}</td><td>${shortId(p.topology_state_id)}</td>` +
        `<td>${p.nodes_attempted}</td><td class="${ok ? 'ok' : ''}">${p.nodes_succeeded}</td>` +
        `<td class="${p.nodes_failed > 0 ? 'fail' : ''}">${p.nodes_failed}</td>` +
        `<td>${p.push_duration_ms?.toFixed(1) ?? '—'}</td>`;
      pb.appendChild(tr);
    });
    if (!d.push_history?.length) pb.innerHTML = '<tr><td colspan="6" class="empty">No pushes yet</td></tr>';

    const db = document.getElementById('dev-body');
    db.innerHTML = '';
    (d.deviation_history || []).slice().reverse().slice(0, 20).forEach(dv => {
      const tr = document.createElement('tr');
      tr.innerHTML = `<td>${shortTime(dv.sim_time)}</td><td>${dv.node_a}</td><td>${dv.node_b}</td>` +
        `<td class="fail">${dv.reason}</td><td>${shortId(dv.topology_state_id)}</td>`;
      db.appendChild(tr);
    });
    if (!d.deviation_history?.length) db.innerHTML = '<tr><td colspan="5" class="empty">No deviations detected</td></tr>';

    const ab = document.getElementById('alm-body');
    ab.innerHTML = '';
    (d.almanac_history || []).slice().reverse().slice(0, 20).forEach(a => {
      const tr = document.createElement('tr');
      tr.innerHTML = `<td>${shortTime(a.sim_time)}</td><td>${shortId(a.topology_state_id)}</td>` +
        `<td>${a.active_link_count}</td><td>${a.forwarding_table_count}</td>`;
      ab.appendChild(tr);
    });
    if (!d.almanac_history?.length) ab.innerHTML = '<tr><td colspan="4" class="empty">No almanac entries yet</td></tr>';

    document.getElementById('status-bar').textContent =
      `Last update: ${new Date().toLocaleTimeString()} — uptime since ${shortTime(d.start_wall_time)}`;
  } catch(e) {
    document.getElementById('status-bar').textContent = 'Error: ' + e.message;
  }
}

async function triggerRecompute() {
  const el = document.getElementById('recompute-status');
  el.textContent = 'Requesting...';
  try {
    const r = await fetch('/api/recompute', {method: 'POST'});
    const d = await r.json();
    el.textContent = d.ok ? 'Queued' : ('Error: ' + d.error);
    setTimeout(() => { el.textContent = ''; }, 3000);
  } catch(e) {
    el.textContent = 'Error: ' + e.message;
  }
}

poll();
setInterval(poll, 1000);
</script>
</body>
</html>"""


def build_app(state: ConsoleState) -> FastAPI:
    """Build the FastAPI console app bound to the given ConsoleState."""
    app = FastAPI(title="NodalPath Operator Console", docs_url=None, redoc_url=None)

    @app.get("/", response_class=HTMLResponse)
    async def dashboard() -> str:
        return _DASHBOARD_HTML

    @app.get("/api/health")
    async def health() -> dict:
        return {"status": "ok"}

    @app.get("/api/status")
    async def status() -> dict:
        return state.snapshot()

    @app.get("/api/almanac")
    async def almanac() -> list:
        return state.snapshot()["almanac_history"]

    @app.get("/api/pushes")
    async def pushes() -> list:
        return state.snapshot()["push_history"]

    @app.get("/api/deviations")
    async def deviations() -> list:
        return state.snapshot()["deviation_history"]

    @app.post("/api/recompute")
    async def recompute() -> dict:
        state.request_recompute()
        return {"ok": True, "message": "Recompute queued"}

    return app
