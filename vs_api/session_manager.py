"""Session manager — lists available sessions and orchestrates switching.

Scans configs/sessions/ for YAML files, provides list with active flag,
and runs teardown + deploy in a thread executor during switch.

Session recovery: on startup without explicit --session/--db, scans known
data directories for session-state.json files with live PIDs and recovers
the newest one automatically.
"""

from __future__ import annotations

import json
import logging
import os
import signal
import subprocess
import sys
from pathlib import Path
from typing import Callable

import yaml

from nodalarc.models.session import SessionConfig

log = logging.getLogger(__name__)

# Maximum number of old session directories to keep
_MAX_KEPT_SESSIONS = 5


def _pid_alive(pid: int) -> bool:
    """Check if a process with the given PID is still running."""
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)  # Signal 0 = just check, don't actually signal
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        # Process exists but we don't have permission to signal it
        return True


class SessionManager:
    """Manages session listing, switching, and status tracking."""

    def __init__(self, sessions_dir: str, initial_db_path: str | None = None) -> None:
        self._sessions_dir = Path(sessions_dir)
        self._current_data_dir: Path | None = None
        self._current_session_file: str | None = None
        self._status: str = "idle"
        self._status_detail: str = ""
        self._available: list[dict] = []

        # Derive initial data_dir from db_path parent if provided
        if initial_db_path:
            self._current_data_dir = Path(initial_db_path).parent

        # Scan sessions on init
        self._available = self.scan_sessions()

    @property
    def status(self) -> str:
        return self._status

    @property
    def status_detail(self) -> str:
        return self._status_detail

    def scan_sessions(self) -> list[dict]:
        """Read each YAML in sessions_dir, parse with SessionConfig, return metadata."""
        results = []
        if not self._sessions_dir.is_dir():
            log.warning(f"Sessions directory not found: {self._sessions_dir}")
            return results

        for yaml_path in sorted(self._sessions_dir.glob("*.yaml")):
            try:
                raw = yaml.safe_load(yaml_path.read_text())
                session = SessionConfig.model_validate(raw)
                results.append({
                    "name": session.session.name,
                    "file": str(yaml_path),
                    "constellation": Path(session.constellation).stem,
                    "routing_stack": Path(session.routing.stack).name,
                })
            except Exception as exc:
                log.warning(f"Failed to parse session {yaml_path}: {exc}")
        return results

    def list_sessions(self) -> list[dict]:
        """Return available sessions with active flag on current session."""
        return [
            {**s, "active": s["file"] == self._current_session_file}
            for s in self._available
        ]

    def set_active(self, session_file: str) -> None:
        """Mark a session file as the currently active session."""
        self._current_session_file = session_file

    def _valid_session_files(self) -> set[str]:
        """Return the set of known session file paths from the initial scan."""
        return {s["file"] for s in self._available}

    def _collect_data_dirs(self) -> list[Path]:
        """Collect all unique data_dir paths from scanned session configs."""
        dirs: set[str] = set()
        for s in self._available:
            try:
                raw = yaml.safe_load(Path(s["file"]).read_text())
                cfg = SessionConfig.model_validate(raw)
                dirs.add(cfg.session.data_dir)
            except Exception:
                pass
        return [Path(d) for d in dirs]

    def recover_session(self) -> dict | None:
        """Scan data directories for the newest session-state.json with live PIDs.

        Returns the session state dict if a live session is found, None otherwise.
        The dict includes: session_id, data_dir, session_config, db_path,
        mi_pid, orchestrator_pid, vsapi_pid.
        """
        data_dirs = self._collect_data_dirs()
        if not data_dirs:
            return None

        # Collect all session-state.json files across all data dirs
        candidates: list[tuple[Path, float]] = []
        for base in data_dirs:
            if not base.is_dir():
                continue
            for subdir in base.iterdir():
                if not subdir.is_dir():
                    continue
                state_file = subdir / "session-state.json"
                if state_file.exists():
                    candidates.append((state_file, state_file.stat().st_mtime))

        # Sort newest first
        candidates.sort(key=lambda x: x[1], reverse=True)

        for state_file, _mtime in candidates:
            try:
                state = json.loads(state_file.read_text())
            except Exception:
                continue

            mi_pid = state.get("mi_pid", 0)
            orch_pid = state.get("orchestrator_pid", 0)

            # A session is "live" if either MI or orchestrator is running
            if _pid_alive(mi_pid) or _pid_alive(orch_pid):
                log.info(
                    f"Recovered live session: {state.get('session_id')} "
                    f"(mi={mi_pid} alive={_pid_alive(mi_pid)}, "
                    f"orch={orch_pid} alive={_pid_alive(orch_pid)})"
                )
                # Update internal state
                self._current_data_dir = state_file.parent
                session_config = state.get("session_config", "")
                if session_config:
                    self._current_session_file = session_config
                self._status = "ready"
                self._status_detail = ""
                return state

        log.info("No live session found during recovery scan")
        return None

    def kill_all_session_processes(self) -> int:
        """Find and kill ALL session processes across all data directories.

        Returns the number of processes killed. Used during teardown to ensure
        no orphan MI/orchestrator processes survive.
        """
        killed = 0
        data_dirs = self._collect_data_dirs()

        for base in data_dirs:
            if not base.is_dir():
                continue
            for subdir in base.iterdir():
                if not subdir.is_dir():
                    continue
                state_file = subdir / "session-state.json"
                if not state_file.exists():
                    continue
                try:
                    state = json.loads(state_file.read_text())
                except Exception:
                    continue

                for key in ("mi_pid", "orchestrator_pid"):
                    pid = state.get(key, 0)
                    if pid and _pid_alive(pid):
                        try:
                            os.kill(pid, signal.SIGTERM)
                            log.info(f"Killed orphan {key}={pid} from {subdir.name}")
                            killed += 1
                        except (ProcessLookupError, PermissionError):
                            pass
        return killed

    def cleanup_old_sessions(self, keep: int = _MAX_KEPT_SESSIONS) -> int:
        """Remove old session directories, keeping the newest `keep` per data_dir.

        Only removes directories where all PIDs are dead. Also removes orphan
        directories that lack session-state.json (partial/failed deploys).
        Returns count removed.
        """
        import shutil

        removed = 0
        data_dirs = self._collect_data_dirs()

        for base in data_dirs:
            if not base.is_dir():
                continue

            # Separate complete (has session-state.json) from orphan dirs
            complete = []
            orphan = []
            for d in base.iterdir():
                if not d.is_dir():
                    continue
                if (d / "session-state.json").exists():
                    complete.append(d)
                else:
                    orphan.append(d)

            # Sort complete dirs by mtime (newest first), keep newest `keep`
            complete.sort(key=lambda d: d.stat().st_mtime, reverse=True)
            for subdir in complete[keep:]:
                state_file = subdir / "session-state.json"
                try:
                    state = json.loads(state_file.read_text())
                    mi_pid = state.get("mi_pid", 0)
                    orch_pid = state.get("orchestrator_pid", 0)
                    if _pid_alive(mi_pid) or _pid_alive(orch_pid):
                        log.info(f"Skipping cleanup of {subdir.name} — processes still live")
                        continue
                except Exception:
                    pass

                try:
                    shutil.rmtree(subdir)
                    log.info(f"Cleaned up old session directory: {subdir.name}")
                    removed += 1
                except Exception as exc:
                    log.warning(f"Failed to remove {subdir}: {exc}")

            # Remove orphan directories (no session-state.json = failed/partial deploy)
            for subdir in orphan:
                try:
                    shutil.rmtree(subdir)
                    log.info(f"Cleaned up orphan directory: {subdir.name}")
                    removed += 1
                except Exception as exc:
                    log.warning(f"Failed to remove orphan {subdir}: {exc}")

        return removed

    def switch(
        self,
        session_path: str,
        clear_state_fn: Callable[[], None],
        update_globals_fn: Callable[[str, str], None],
    ) -> None:
        """Tear down current session and deploy new one (blocking — run in executor).

        Args:
            session_path: Path to the new session YAML file.
            clear_state_fn: Callback to reset VS-API in-memory state.
            update_globals_fn: Callback(session_path, new_db_path) to update VS-API globals.
        """
        # Validate session_path against scanned sessions — reject unknown paths
        if session_path not in self._valid_session_files():
            self._status = "error"
            self._status_detail = f"Unknown session: {Path(session_path).name}"
            log.error(f"Rejected switch to unknown session path: {session_path}")
            return

        try:
            self._status = "switching"
            self._status_detail = "Tearing down current session"
            log.info(f"Session switch: tearing down, deploying {session_path}")

            # === Teardown: kill ALL session processes (not just current) ===
            self._status_detail = "Stopping all session processes"
            killed = self.kill_all_session_processes()
            if killed:
                log.info(f"Killed {killed} session process(es) during teardown")

            # === Teardown: uninstall ANY existing helm releases in namespace ===
            kubeconfig = "KUBECONFIG=/etc/rancher/k3s/k3s.yaml"
            self._status_detail = "Checking for existing helm releases"
            result = subprocess.run(
                ["sudo", "env", kubeconfig,
                 "helm", "list", "-n", "nodalarc", "-q"],
                capture_output=True, text=True, timeout=30,
            )
            releases = [r.strip() for r in result.stdout.strip().split("\n") if r.strip()]
            for release in releases:
                self._status_detail = f"Uninstalling helm release {release}"
                log.info(f"Uninstalling stale helm release: {release}")
                subprocess.run(
                    ["sudo", "env", kubeconfig,
                     "helm", "uninstall", release, "-n", "nodalarc"],
                    capture_output=True, text=True, timeout=60,
                )

            if releases:
                self._status_detail = "Waiting for pods to terminate"
                subprocess.run(
                    ["sudo", "env", kubeconfig,
                     "kubectl", "wait", "--for=delete", "pod",
                     "-l", "nodalarc.io/node-id",
                     "-n", "nodalarc", "--timeout=60s"],
                    capture_output=True, text=True, timeout=90,
                )

            # === Clear VS-API state ===
            self._status_detail = "Clearing in-memory state"
            clear_state_fn()

            # === Deploy new session ===
            self._status_detail = "Starting deployment"
            kubeconfig = "KUBECONFIG=/etc/rancher/k3s/k3s.yaml"
            proc = subprocess.Popen(
                ["sudo", "env", kubeconfig,
                 sys.executable, "-u", "-m", "tools.na_deploy",
                 "--session", session_path,
                 "--skip-vsapi"],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
            )
            # Stream output and update status_detail from "Step N:" log lines
            last_lines: list[str] = []
            veth_count = 0
            veth_total = 0
            for line in proc.stdout:
                line = line.rstrip()
                if line:
                    last_lines.append(line)
                    if len(last_lines) > 20:
                        last_lines.pop(0)
                    # Extract step info from log lines like "... Step 5: Deploy K3s pods"
                    if " Step " in line:
                        # Pull everything after "Step "
                        idx = line.index(" Step ")
                        self._status_detail = line[idx + 1:]
                        veth_count = 0
                        veth_total = 0
                    elif "Waiting for" in line:
                        idx = line.index("Waiting for")
                        self._status_detail = line[idx:]
                    elif "Helm install" in line:
                        self._status_detail = "Helm install running"
                    elif "All " in line and " pods Running" in line:
                        self._status_detail = line[line.index("All "):]
                    elif "veth pairs to create" in line:
                        # Parse total from "N veth pairs to create"
                        try:
                            veth_total = int(line.split("veth pairs")[0].strip().split()[-1])
                        except (ValueError, IndexError):
                            pass
                    elif "Created " in line and " veth" in line:
                        veth_count += 1
                        if veth_total > 0:
                            self._status_detail = f"Creating connections {veth_count} of {veth_total}"
                        else:
                            self._status_detail = f"Creating connections ({veth_count})"
            proc.wait()
            if proc.returncode != 0:
                tail = "\n".join(last_lines[-5:])
                raise RuntimeError(f"Deploy failed (rc={proc.returncode}):\n{tail}")

            # === Find new session-state.json ===
            self._status_detail = "Locating new session data"
            raw = yaml.safe_load(Path(session_path).read_text())
            session_cfg = SessionConfig.model_validate(raw)
            data_base = Path(session_cfg.session.data_dir)

            # Find newest subdirectory (the one just created)
            if data_base.is_dir():
                subdirs = sorted(
                    [d for d in data_base.iterdir() if d.is_dir()],
                    key=lambda d: d.stat().st_mtime,
                    reverse=True,
                )
                new_data_dir = None
                for d in subdirs:
                    if (d / "session-state.json").exists():
                        new_data_dir = d
                        break

                if new_data_dir is None:
                    raise RuntimeError(f"No session-state.json found under {data_base}")

                new_state = json.loads((new_data_dir / "session-state.json").read_text())
                new_db_path = new_state["db_path"]
            else:
                raise RuntimeError(f"Data directory not found: {data_base}")

            # === Update VS-API globals ===
            self._status_detail = "Updating VS-API configuration"
            update_globals_fn(session_path, new_db_path)

            # === Update internal state ===
            self._current_data_dir = new_data_dir
            self._current_session_file = session_path
            self._status = "ready"
            self._status_detail = ""
            log.info(f"Session switch complete: {session_path}")

            # === Cleanup old session directories (non-blocking, best-effort) ===
            try:
                cleaned = self.cleanup_old_sessions()
                if cleaned:
                    log.info(f"Cleaned up {cleaned} old session directory(ies)")
            except Exception as exc:
                log.warning(f"Session cleanup failed (non-fatal): {exc}")

        except Exception as exc:
            self._status = "error"
            self._status_detail = str(exc)
            log.error(f"Session switch failed: {exc}")
