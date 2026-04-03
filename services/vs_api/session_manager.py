# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Elastic License 2.0 (ELv2). See LICENSE file.
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
from collections.abc import Callable
from pathlib import Path

import yaml
from nodalarc.models.session import SessionConfig
from nodalarc.platform import get_platform_config

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
                if session.routing.stack is not None:
                    routing_label = Path(session.routing.stack).name
                else:
                    ext_str = (
                        "-".join(session.routing.extensions)
                        if session.routing.extensions
                        else "plain"
                    )
                    routing_label = f"{session.routing.protocol}-{ext_str}"
                if isinstance(session.constellation, dict):
                    const_label = session.constellation.get("name", "custom")
                else:
                    const_label = Path(session.constellation).stem
                results.append(
                    {
                        "name": session.session.name,
                        "file": str(yaml_path),
                        "constellation": const_label,
                        "routing_stack": routing_label,
                    }
                )
            except Exception as exc:
                log.warning(f"Failed to parse session {yaml_path}: {exc}")
        return results

    def list_sessions(self) -> list[dict]:
        """Return available sessions with active flag on current session."""
        result = [{**s, "active": s["file"] == self._current_session_file} for s in self._available]
        # If session is ready but no file match (Operator-deployed session), match by name
        if (
            self._status == "ready"
            and not any(s["active"] for s in result)
            and self._current_session_file
        ):
            try:
                import yaml

                raw = Path(self._current_session_file).read_text()
                name = yaml.safe_load(raw).get("session", {}).get("name", "")
                if name:
                    for s in result:
                        if s["name"] == name:
                            s["active"] = True
                            break
            except Exception:
                pass
        return result

    def set_active(self, session_file: str) -> None:
        """Mark a session file as the currently active session."""
        self._current_session_file = session_file

    def rescan(self) -> None:
        """Re-scan session directory to pick up newly added YAML files."""
        self._available = self.scan_sessions()

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

                for key in ("ome_pid", "mi_pid", "orchestrator_pid"):
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
        """Tear down current session and deploy new one via ConstellationSpec CRD.

        Replaces the deploy daemon approach with direct K8s API calls:
        1. Delete existing ConstellationSpec CR (Operator tears down)
        2. Apply new ConstellationSpec CR (Operator deploys)
        3. Poll CR status until Ready

        Args:
            session_path: Path to the new session YAML file.
            clear_state_fn: Callback to reset VS-API in-memory state.
            update_globals_fn: Callback(session_path, new_db_path) to update VS-API globals.
        """
        import time

        import kubernetes.client
        import kubernetes.config

        self.rescan()
        if session_path not in self._valid_session_files():
            self._status = "error"
            self._status_detail = f"Unknown session: {Path(session_path).name}"
            log.error(f"Rejected switch to unknown session path: {session_path}")
            return

        try:
            kubernetes.config.load_incluster_config()
        except kubernetes.config.ConfigException:
            kubernetes.config.load_kube_config()

        api = kubernetes.client.CustomObjectsApi()
        cfg = get_platform_config()
        ns = cfg.kubernetes_namespace

        try:
            self._status = "switching"
            self._status_detail = "Tearing down current session"
            log.info(f"Session switch: deploying {session_path} via CRD")

            # === Delete existing ConstellationSpec CR ===
            try:
                api.delete_namespaced_custom_object(
                    group="nodalarc.io",
                    version="v1alpha1",
                    namespace=ns,
                    plural="constellationspecs",
                    name="current-session",
                )
                log.info("Deleted existing ConstellationSpec CR")
                # Wait for CR to be fully deleted (avoid 409 Conflict on recreate)
                self._status_detail = "Waiting for old CR to finalize"
                for _ in range(60):
                    try:
                        api.get_namespaced_custom_object(
                            group="nodalarc.io",
                            version="v1alpha1",
                            namespace=ns,
                            plural="constellationspecs",
                            name="current-session",
                        )
                        time.sleep(2)  # Still exists, wait
                    except kubernetes.client.rest.ApiException as get_e:
                        if get_e.status == 404:
                            break  # Gone
                        raise
                # Wait for pods to terminate
                self._status_detail = "Waiting for old session pods to terminate"
                v1 = kubernetes.client.CoreV1Api()
                for _ in range(60):
                    pods = v1.list_namespaced_pod(ns, label_selector="nodalarc.io/node-id")
                    if len(pods.items) == 0:
                        break
                    time.sleep(2)
            except kubernetes.client.rest.ApiException as e:
                if e.status != 404:
                    raise

            # === Clear VS-API state ===
            self._status_detail = "Clearing in-memory state"
            clear_state_fn()

            # === Build ConstellationSpec CR with session YAML content ===
            # The CRD carries the complete session YAML so both pods can
            # read it without shared filesystem access.
            self._status_detail = "Building constellation spec"
            session_yaml_content = Path(session_path).read_text()
            cr_body = {
                "apiVersion": "nodalarc.io/v1alpha1",
                "kind": "ConstellationSpec",
                "metadata": {"name": "current-session", "namespace": ns},
                "spec": {
                    "sessionYaml": session_yaml_content,
                },
            }

            # === Apply ConstellationSpec CR ===
            self._status_detail = "Deploying constellation"
            api.create_namespaced_custom_object(
                group="nodalarc.io",
                version="v1alpha1",
                namespace=ns,
                plural="constellationspecs",
                body=cr_body,
            )
            log.info(f"Applied ConstellationSpec CR for {session_path}")

            # === Poll CR status until Ready ===
            self._status_detail = "Waiting for constellation to deploy"
            cr_ready = False
            for _ in range(300):  # 5 minutes max
                cr = api.get_namespaced_custom_object(
                    group="nodalarc.io",
                    version="v1alpha1",
                    namespace=ns,
                    plural="constellationspecs",
                    name="current-session",
                )
                phase = cr.get("status", {}).get("phase", "")
                message = cr.get("status", {}).get("message", "")
                self._status_detail = message or f"Phase: {phase}"
                if phase == "Ready":
                    cr_ready = True
                    break
                if phase == "Error":
                    raise RuntimeError(f"Operator error: {message}")
                time.sleep(1)

            if not cr_ready:
                # CR never reached Ready within timeout — set error, don't claim ready
                self._status = "error"
                self._status_detail = "Deploy timed out waiting for CR Ready"
                log.warning("Session switch timed out waiting for CR Ready")
                return

            # === Update VS-API globals ===
            self._status_detail = "Updating VS-API configuration"
            update_globals_fn(session_path, "")

            # === Update internal state ===
            self._current_session_file = session_path
            self._status = "ready"
            self._status_detail = ""
            log.info(f"Session switch complete: {session_path}")

        except Exception as exc:
            self._status = "error"
            self._status_detail = str(exc)
            log.error(f"Session switch failed: {exc}")
