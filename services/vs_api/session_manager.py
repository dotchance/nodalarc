# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""Runtime session switch coordinator and process-state recovery."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
import threading
from collections.abc import Awaitable, Callable, Mapping
from pathlib import Path
from typing import Any, Protocol

from nodalarc.platform_config import get_platform_config
from nodalarc.workloads.refs import SelectionRef

from .catalog_context import CatalogContext
from .catalog_upload_store import CatalogUploadResourceEvidence, KubernetesCatalogUploadStore
from .session_deployment import (
    PreparedCatalogSessionDeployment,
    assert_catalog_session_deployment_current,
    cleanup_unselected_catalog_session_upload,
    constellation_spec_body,
    persist_catalog_session_upload,
)

log = logging.getLogger(__name__)


# Maximum number of old session directories to keep
_MAX_KEPT_SESSIONS = 5

_CR_GROUP = "nodalarc.io"
_CR_VERSION = "v1alpha1"
_CR_PLURAL = "constellationspecs"
_CR_NAME = "current-session"


class _CustomObjectsSwitchApi(Protocol):
    def delete_namespaced_custom_object(self, **kwargs: Any) -> Any: ...

    def get_namespaced_custom_object(self, **kwargs: Any) -> dict[str, Any]: ...

    def create_namespaced_custom_object(self, **kwargs: Any) -> Any: ...


class _CoreV1PodApi(Protocol):
    def list_namespaced_pod(self, *args: Any, **kwargs: Any) -> Any: ...


_ProgressCallback = Callable[[str], Awaitable[None]]
_TransitionStartedCallback = Callable[[], Awaitable[None]]
_UploadResourceObservedCallback = Callable[[CatalogUploadResourceEvidence], None]
_ConstellationSpecObservedCallback = Callable[[Mapping[str, Any]], Awaitable[None]]
_DeploymentFreshnessCheck = Callable[[PreparedCatalogSessionDeployment], None]


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


def _api_status(exc: BaseException) -> int | None:
    status = getattr(exc, "status", None)
    return status if isinstance(status, int) else None


def _selected_spec_matches(observed_cr: Mapping[str, Any], intended_cr: Mapping[str, Any]) -> bool:
    observed_spec = observed_cr.get("spec")
    intended_spec = intended_cr.get("spec")
    return isinstance(observed_spec, Mapping) and observed_spec == intended_spec


def _selected_catalog_upload_matches(
    observed_cr: Mapping[str, Any],
    intended_cr: Mapping[str, Any],
) -> bool:
    observed_spec = observed_cr.get("spec")
    intended_spec = intended_cr.get("spec")
    if not isinstance(observed_spec, Mapping) or not isinstance(intended_spec, Mapping):
        return False
    intended_upload = intended_spec.get("catalogUpload")
    return intended_upload is not None and observed_spec.get("catalogUpload") == intended_upload


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
    """Coordinates prepared catalog-session switches and runtime recovery."""

    def __init__(self, initial_db_path: str | None = None) -> None:
        self._current_data_dir: Path | None = None
        self._current_source_id: str | None = None
        self._status: str = "idle"
        self._status_detail: str = ""
        self._detail_lock = threading.Lock()

        if initial_db_path:
            self._current_data_dir = Path(initial_db_path).parent

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

    @property
    def active_source_id(self) -> str | None:
        return self._current_source_id

    def set_active(self, source_id: str) -> None:
        """Mark one catalog session reference as the active source."""
        self._current_source_id = source_id

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

        Only state-marked directories with dead recorded processes are eligible.
        Unknown sibling directories are never inferred to be session artifacts.
        """
        import shutil

        removed = 0
        data_dirs = self._collect_data_dirs()

        for base in data_dirs:
            if not base.is_dir():
                continue

            # Unknown sibling directories may belong to catalog or operational
            # storage. Only directories carrying our state marker are owned by
            # this cleanup path.
            complete = []
            for d in base.iterdir():
                if not d.is_dir():
                    continue
                if (d / "session-state.json").exists():
                    complete.append(d)

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

        return removed

    async def _switch_constellation_spec(
        self,
        *,
        source_id: str,
        cr_body: Mapping[str, Any],
        custom_objects_api: _CustomObjectsSwitchApi,
        core_v1_api: _CoreV1PodApi,
        namespace: str,
        progress: _ProgressCallback,
        before_teardown: Callable[[], None] | None = None,
        selection_started: threading.Event | None = None,
        constellation_spec_observed: _ConstellationSpecObservedCallback | None = None,
    ) -> dict:
        """Apply one already-built CR through the common destructive switch path."""
        loop = asyncio.get_running_loop()
        await progress("Tearing down current session")
        log.info("Session switch: deploying %s via CRD", source_id)

        def _delete_existing_cr() -> bool:
            if before_teardown is not None:
                before_teardown()
            try:
                custom_objects_api.delete_namespaced_custom_object(
                    group=_CR_GROUP,
                    version=_CR_VERSION,
                    namespace=namespace,
                    plural=_CR_PLURAL,
                    name=_CR_NAME,
                )
            except Exception as exc:
                if _api_status(exc) == 404:
                    return False
                raise
            return True

        deleted_existing_cr = await loop.run_in_executor(None, _delete_existing_cr)
        if deleted_existing_cr:
            log.info("Deleted existing ConstellationSpec CR %s/%s", namespace, _CR_NAME)
            await progress("Waiting for old session to finalize")
            old_cr_deleted = False
            for _ in range(60):
                try:
                    await loop.run_in_executor(
                        None,
                        lambda: custom_objects_api.get_namespaced_custom_object(
                            group=_CR_GROUP,
                            version=_CR_VERSION,
                            namespace=namespace,
                            plural=_CR_PLURAL,
                            name=_CR_NAME,
                        ),
                    )
                    await asyncio.sleep(2)
                except Exception as exc:
                    if _api_status(exc) == 404:
                        old_cr_deleted = True
                        break
                    raise
            if not old_cr_deleted:
                raise TimeoutError(
                    "Old ConstellationSpec did not finalize within 120 seconds; "
                    "refusing to deploy a new session over stale control-plane state"
                )

            await progress("Waiting for old session pods to terminate")
            remaining = 0
            for _ in range(60):
                pods = await loop.run_in_executor(
                    None,
                    lambda: core_v1_api.list_namespaced_pod(
                        namespace,
                        label_selector="nodalarc.io/node-id",
                    ),
                )
                remaining = len(pods.items)
                if remaining == 0:
                    break
                await progress(f"Waiting for {remaining} old pods to terminate")
                await asyncio.sleep(2)
            if remaining != 0:
                raise TimeoutError(
                    f"{remaining} old session pod(s) still exist after 120 seconds; "
                    "refusing to deploy a new session over stale data-plane state"
                )

        await progress("Deploying new constellation")

        def _create_cr() -> None:
            try:
                custom_objects_api.create_namespaced_custom_object(
                    group=_CR_GROUP,
                    version=_CR_VERSION,
                    namespace=namespace,
                    plural=_CR_PLURAL,
                    body=dict(cr_body),
                )
            except Exception as create_exc:
                try:
                    observed = custom_objects_api.get_namespaced_custom_object(
                        group=_CR_GROUP,
                        version=_CR_VERSION,
                        namespace=namespace,
                        plural=_CR_PLURAL,
                        name=_CR_NAME,
                    )
                except Exception as observe_exc:
                    if selection_started is not None and _api_status(observe_exc) != 404:
                        selection_started.set()
                    raise create_exc from observe_exc
                if selection_started is not None and _selected_catalog_upload_matches(
                    observed,
                    cr_body,
                ):
                    selection_started.set()
                if _selected_spec_matches(observed, cr_body):
                    log.warning(
                        "ConstellationSpec create reported %s, but exact intended spec exists",
                        type(create_exc).__name__,
                    )
                    return
                raise
            if selection_started is not None:
                selection_started.set()

        await loop.run_in_executor(None, _create_cr)
        log.info("Applied ConstellationSpec CR for %s", source_id)
        selected_cr = await loop.run_in_executor(
            None,
            lambda: custom_objects_api.get_namespaced_custom_object(
                group=_CR_GROUP,
                version=_CR_VERSION,
                namespace=namespace,
                plural=_CR_PLURAL,
                name=_CR_NAME,
            ),
        )
        if constellation_spec_observed is not None:
            await constellation_spec_observed(selected_cr)

        await progress("Waiting for session to deploy")
        for _ in range(300):
            cr = await loop.run_in_executor(
                None,
                lambda: custom_objects_api.get_namespaced_custom_object(
                    group=_CR_GROUP,
                    version=_CR_VERSION,
                    namespace=namespace,
                    plural=_CR_PLURAL,
                    name=_CR_NAME,
                ),
            )
            phase = cr.get("status", {}).get("phase", "")
            message = cr.get("status", {}).get("message", "")
            if not _cr_status_observes_current_generation(cr):
                await progress("Waiting for operator to observe new session spec")
                await asyncio.sleep(1)
                continue
            if message:
                await progress(message)
            if phase == "Ready":
                if constellation_spec_observed is not None:
                    await constellation_spec_observed(cr)
                self._current_source_id = source_id
                return cr
            if phase == "Error":
                if constellation_spec_observed is not None:
                    await constellation_spec_observed(cr)
                raise RuntimeError(f"Deploy failed: {message}")
            await asyncio.sleep(1)

        raise TimeoutError("Deploy timed out waiting for session Ready (5 minutes)")

    async def _switch_prepared_session(
        self,
        deployment: PreparedCatalogSessionDeployment,
        *,
        final_preflight: _DeploymentFreshnessCheck,
        upload_store: KubernetesCatalogUploadStore,
        custom_objects_api: _CustomObjectsSwitchApi,
        core_v1_api: _CoreV1PodApi,
        namespace: str,
        workload_selection: SelectionRef | None = None,
        progress_fn: _ProgressCallback | None = None,
        transition_started: _TransitionStartedCallback | None = None,
        upload_resource_observed: _UploadResourceObservedCallback | None = None,
        constellation_spec_observed: _ConstellationSpecObservedCallback | None = None,
    ) -> dict:
        """Persist and select one exact prepared YAML file closure.

        The caller owns Kubernetes client construction and source authority.
        Upload readback and the final repository-currentness assertion both
        complete before the first destructive Kubernetes operation.
        """
        if deployment.receipt is not None:
            raise ValueError("catalog switch requires an unpersisted prepared deployment")
        persisted = deployment
        selection_started = threading.Event()

        async def _progress(detail: str) -> None:
            self.status_detail = detail
            if progress_fn is not None:
                await progress_fn(detail)

        async def _cleanup_unselected() -> None:
            if persisted.receipt is None or selection_started.is_set():
                return
            try:
                await asyncio.get_running_loop().run_in_executor(
                    None,
                    lambda: cleanup_unselected_catalog_session_upload(persisted, upload_store),
                )
            except Exception:
                log.exception(
                    "Failed to clean up unselected catalog upload %s",
                    persisted.upload.selection.upload_id,
                )

        try:
            self._status = "switching"
            await _progress("Uploading exact session catalog")
            persisted = await asyncio.get_running_loop().run_in_executor(
                None,
                lambda: persist_catalog_session_upload(
                    persisted,
                    upload_store,
                    resource_observer=upload_resource_observed,
                ),
            )
            await _progress("Verifying exact session catalog")
            await asyncio.get_running_loop().run_in_executor(
                None,
                lambda: final_preflight(persisted),
            )
            if transition_started is not None:
                await transition_started()

            await _progress("Switching runtime session")
            cr_body = constellation_spec_body(
                persisted, namespace=namespace, workload_selection=workload_selection
            )

            return await self._switch_constellation_spec(
                source_id=str(persisted.prepared.source.logical_id),
                cr_body=cr_body,
                custom_objects_api=custom_objects_api,
                core_v1_api=core_v1_api,
                namespace=namespace,
                progress=_progress,
                before_teardown=lambda: final_preflight(persisted),
                selection_started=selection_started,
                constellation_spec_observed=constellation_spec_observed,
            )
        except asyncio.CancelledError:
            await _cleanup_unselected()
            raise
        except Exception as exc:
            await _cleanup_unselected()
            self._status = "error"
            self.status_detail = str(exc)
            log.error("Catalog session switch failed: %s", exc)
            raise

    async def switch_catalog(
        self,
        deployment: PreparedCatalogSessionDeployment,
        *,
        context: CatalogContext,
        upload_store: KubernetesCatalogUploadStore,
        custom_objects_api: _CustomObjectsSwitchApi,
        core_v1_api: _CoreV1PodApi,
        namespace: str,
        workload_selection: SelectionRef | None = None,
        progress_fn: _ProgressCallback | None = None,
        transition_started: _TransitionStartedCallback | None = None,
        upload_resource_observed: _UploadResourceObservedCallback | None = None,
        constellation_spec_observed: _ConstellationSpecObservedCallback | None = None,
    ) -> dict:
        """Select one current repository session through the shared file core."""

        return await self._switch_prepared_session(
            deployment,
            final_preflight=lambda current: assert_catalog_session_deployment_current(
                context,
                current,
            ),
            upload_store=upload_store,
            custom_objects_api=custom_objects_api,
            core_v1_api=core_v1_api,
            namespace=namespace,
            workload_selection=workload_selection,
            progress_fn=progress_fn,
            transition_started=transition_started,
            upload_resource_observed=upload_resource_observed,
            constellation_spec_observed=constellation_spec_observed,
        )
