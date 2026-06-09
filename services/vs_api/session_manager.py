# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""Session manager — lists available sessions and orchestrates switching.

Scans catalog session YAML files, provides list with active flag,
and runs teardown + deploy in a thread executor during switch.

Session recovery: on startup without explicit --session/--db, scans known
data directories for session-state.json files with live PIDs and recovers
the newest one automatically.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
import threading
from pathlib import Path

import yaml
from nodalarc.catalog_paths import CatalogPathError
from nodalarc.models.resolved_session import SourceContext
from nodalarc.platform_config import get_platform_config
from nodalarc.resolve_session import resolve_session_with_assets

log = logging.getLogger(__name__)


# Maximum number of old session directories to keep
_MAX_KEPT_SESSIONS = 5


def _routing_label(resolved) -> str:
    routing = resolved.routing
    if routing is None or not routing.domains:
        return "unrouted"
    return " + ".join(f"{domain.id}:{domain.protocol}" for domain in routing.domains)


def _constellation_label(resolved) -> str:
    segments = sorted({node.segment_id for node in resolved.nodes if node.kind == "satellite"})
    return " + ".join(segments) if segments else "none"


def _cr_status_observes_current_generation(cr: dict) -> bool:
    """Return true when CR status belongs to the current spec generation."""
    metadata = cr.get("metadata") or {}
    status = cr.get("status") or {}
    try:
        generation = int(metadata.get("generation", 0))
        observed_generation = int(status.get("observedGeneration", 0))
    except TypeError, ValueError:
        return False
    return generation > 0 and observed_generation == generation


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

    def __init__(
        self,
        sessions_dir: str,
        initial_db_path: str | None = None,
        generated_sessions_dir: str | None = None,
    ) -> None:
        self._sessions_dir = Path(sessions_dir)
        self._generated_sessions_dir = (
            Path(generated_sessions_dir) if generated_sessions_dir else None
        )
        self._current_data_dir: Path | None = None
        self._current_session_file: str | None = None
        self._status: str = "idle"
        self._status_detail: str = ""
        self._detail_lock = threading.Lock()
        self._available: list[dict] = []
        self._session_file_paths: dict[str, Path] = {}
        self._session_parse_failures: dict[str, str] = {}

        # Derive initial data_dir from db_path parent if provided
        if initial_db_path:
            self._current_data_dir = Path(initial_db_path).parent

        # Scan sessions on init
        self._available = self.scan_sessions()

    def _scan_roots(self) -> tuple[tuple[Path, bool], ...]:
        """Return session roots as ``(path, required)`` pairs."""
        roots: list[tuple[Path, bool]] = [(self._sessions_dir, True)]
        if self._generated_sessions_dir is not None:
            roots.append((self._generated_sessions_dir, False))
        return tuple(roots)

    def _record_session_parse_failure(self, file_key: str, yaml_path: Path, exc: Exception) -> None:
        """Log a session parse failure when it first appears or changes."""
        error = str(exc)
        if self._session_parse_failures.get(file_key) != error:
            log.warning("Failed to parse session %s: %s", yaml_path, exc)
        else:
            log.debug("Repeated session parse failure for %s: %s", yaml_path, exc)
        self._session_parse_failures[file_key] = error

    @property
    def status(self) -> str:
        return self._status

    @property
    def status_detail(self) -> str:
        with self._detail_lock:
            return self._status_detail

    @status_detail.setter
    def status_detail(self, value: str) -> None:
        with self._detail_lock:
            self._status_detail = value

    def scan_sessions(self) -> list[dict]:
        """Read each segment YAML in sessions_dir, resolve it, return metadata."""
        results = []
        session_file_paths: dict[str, Path] = {}
        active_failures: set[str] = set()

        for scan_root, required in self._scan_roots():
            if not scan_root.is_dir():
                if required:
                    log.warning(f"Sessions directory not found: {scan_root}")
                continue
            root = scan_root.resolve(strict=True)

            for yaml_path in sorted(scan_root.glob("*.yaml")):
                file_key = str(yaml_path)
                resolved_path = yaml_path.resolve(strict=True)
                try:
                    resolved_path.relative_to(root)
                except ValueError as exc:
                    msg = f"Session file escapes sessions root: {yaml_path}"
                    log.error(msg)
                    raise CatalogPathError(msg) from exc

                try:
                    raw = yaml.safe_load(resolved_path.read_text())
                    resolution = resolve_session_with_assets(
                        raw,
                        source_context=SourceContext(origin="vs_api.session_manager"),
                    )
                    resolved = resolution.resolved
                    results.append(
                        {
                            "name": resolved.session.name,
                            "file": file_key,
                            "constellation": _constellation_label(resolved),
                            "routing_stack": _routing_label(resolved),
                        }
                    )
                    session_file_paths[file_key] = resolved_path
                    self._session_parse_failures.pop(file_key, None)
                except Exception as exc:
                    active_failures.add(file_key)
                    self._record_session_parse_failure(file_key, yaml_path, exc)
        for key in tuple(self._session_parse_failures):
            if key not in active_failures and key not in session_file_paths:
                self._session_parse_failures.pop(key, None)
        self._session_file_paths = session_file_paths
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
        return set(self._session_file_paths)

    def _validated_session_path(self, session_path: str) -> Path | None:
        """Return the resolved path for a scanned session file key."""
        return self._session_file_paths.get(session_path)

    def _collect_data_dirs(self) -> list[Path]:
        """Session data lives under the platform-owned root — a platform fact,
        not something derived by resolving every available session."""
        return [Path(get_platform_config().session_data_root)]

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
                self.status_detail = ""
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
                        except ProcessLookupError, PermissionError:
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

            # Separate complete (has session-state.json) from orphan dirs.
            # The generated-session library lives under the same data root but
            # is user content with its own lifecycle — never an orphan deploy.
            complete = []
            orphan = []
            for d in base.iterdir():
                if not d.is_dir() or d.name == "generated-sessions":
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

    async def switch(self, session_path: str, progress_fn=None) -> dict:
        """Tear down current session and deploy new one via ConstellationSpec CRD.

        Fully async — uses asyncio.sleep for polling, runs K8s API calls
        in executor to avoid blocking the event loop. Does NOT call any
        VS-API state callbacks — the caller (_run_switch) owns the
        SessionContext lifecycle.

        progress_fn: optional async callback(detail: str) called at each
        significant step so the browser can show real-time progress.
        """

        import kubernetes.client
        import kubernetes.config

        self.rescan()
        validated_session_path = self._validated_session_path(session_path)
        if validated_session_path is None:
            self._status = "error"
            self.status_detail = f"Unknown session: {Path(session_path).name}"
            log.error("Rejected switch to unknown session path: %s", session_path)
            raise ValueError(f"Unknown session: {session_path}")

        try:
            kubernetes.config.load_incluster_config()
        except kubernetes.config.ConfigException:
            kubernetes.config.load_kube_config()

        api = kubernetes.client.CustomObjectsApi()
        cfg = get_platform_config()
        ns = cfg.kubernetes_namespace
        loop = asyncio.get_running_loop()

        async def _progress(detail: str) -> None:
            self.status_detail = detail
            if progress_fn:
                await progress_fn(detail)

        try:
            self._status = "switching"
            await _progress("Tearing down current session")
            log.info("Session switch: deploying %s via CRD", session_path)

            # === Delete existing ConstellationSpec CR ===
            try:
                await loop.run_in_executor(
                    None,
                    lambda: api.delete_namespaced_custom_object(
                        group="nodalarc.io",
                        version="v1alpha1",
                        namespace=ns,
                        plural="constellationspecs",
                        name="current-session",
                    ),
                )
                log.info("Deleted existing ConstellationSpec CR")

                await _progress("Waiting for old session to finalize")
                old_cr_deleted = False
                for _ in range(60):
                    try:
                        await loop.run_in_executor(
                            None,
                            lambda: api.get_namespaced_custom_object(
                                group="nodalarc.io",
                                version="v1alpha1",
                                namespace=ns,
                                plural="constellationspecs",
                                name="current-session",
                            ),
                        )
                        await asyncio.sleep(2)
                    except kubernetes.client.rest.ApiException as get_e:
                        if get_e.status == 404:
                            old_cr_deleted = True
                            break
                        raise
                if not old_cr_deleted:
                    raise TimeoutError(
                        "Old ConstellationSpec did not finalize within 120 seconds; "
                        "refusing to deploy a new session over stale control-plane state"
                    )

                await _progress("Waiting for old session pods to terminate")
                v1 = kubernetes.client.CoreV1Api()
                remaining = 0
                for _ in range(60):
                    pods = await loop.run_in_executor(
                        None,
                        lambda: v1.list_namespaced_pod(ns, label_selector="nodalarc.io/node-id"),
                    )
                    remaining = len(pods.items)
                    if remaining == 0:
                        break
                    await _progress(f"Waiting for {remaining} old pods to terminate")
                    await asyncio.sleep(2)
                if remaining != 0:
                    raise TimeoutError(
                        f"{remaining} old session pod(s) still exist after 120 seconds; "
                        "refusing to deploy a new session over stale data-plane state"
                    )
            except kubernetes.client.rest.ApiException as e:
                if e.status != 404:
                    raise

            # === Build and apply ConstellationSpec CR ===
            await _progress("Deploying new constellation")
            session_yaml_content = validated_session_path.read_text()
            cr_body = {
                "apiVersion": "nodalarc.io/v1alpha1",
                "kind": "ConstellationSpec",
                "metadata": {"name": "current-session", "namespace": ns},
                "spec": {
                    "sessionYaml": session_yaml_content,
                },
            }
            await loop.run_in_executor(
                None,
                lambda: api.create_namespaced_custom_object(
                    group="nodalarc.io",
                    version="v1alpha1",
                    namespace=ns,
                    plural="constellationspecs",
                    body=cr_body,
                ),
            )
            log.info("Applied ConstellationSpec CR for %s", session_path)

            # === Poll CR status until Ready ===
            await _progress("Waiting for session to deploy")
            for _ in range(300):
                cr = await loop.run_in_executor(
                    None,
                    lambda: api.get_namespaced_custom_object(
                        group="nodalarc.io",
                        version="v1alpha1",
                        namespace=ns,
                        plural="constellationspecs",
                        name="current-session",
                    ),
                )
                phase = cr.get("status", {}).get("phase", "")
                message = cr.get("status", {}).get("message", "")
                if not _cr_status_observes_current_generation(cr):
                    await _progress("Waiting for operator to observe new session spec")
                    await asyncio.sleep(1)
                    continue
                if message:
                    await _progress(message)
                if phase == "Ready":
                    self._current_session_file = session_path
                    return cr
                if phase == "Error":
                    raise RuntimeError(f"Deploy failed: {message}")
                await asyncio.sleep(1)

            raise TimeoutError("Deploy timed out waiting for session Ready (5 minutes)")

        except Exception as exc:
            self._status = "error"
            self.status_detail = str(exc)
            log.error("Session switch failed: %s", exc)
            raise
