"""Shared startup loading and health for runtime service processes."""

from __future__ import annotations

import contextlib
import json
import logging
import shutil
import tempfile
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from nodalarc.catalog_upload import CatalogUploadSelection, sha256_digest
from nodalarc.kubernetes_runtime_config import (
    RUNTIME_CONFIG_PROOF_FILENAME,
    load_kubernetes_runtime_config,
    write_runtime_config_proof,
)
from nodalarc.models.resolved_session import SourceContext
from nodalarc.runtime_config import (
    RUNTIME_DEPLOYMENT_CONTEXT_FILENAME,
    ResolvedRuntimeConfig,
    RuntimeConfigProof,
    RuntimeDeploymentContext,
)
from nodalarc.session_identity import read_runtime_session_run_id_file

SESSION_YAML_FILENAME = "session.yaml"
SESSION_RUN_ID_FILENAME = "session_run_id"
CATALOG_UPLOAD_SELECTION_FILENAME = "catalog-upload-selection.json"
DEFAULT_SESSION_CONFIG_DIRECTORY = Path("/etc/nodalarc/session-config")
DEFAULT_INSTALLED_SHIPPED_CATALOG_ROOT = Path("catalog/nodalarc")


@dataclass(frozen=True, slots=True)
class MountedSessionConfig:
    """Exact files observed from one directory-mounted ConfigMap generation."""

    generation_root: Path
    root_yaml: bytes
    run_id: str
    catalog_upload: CatalogUploadSelection
    deployment_context: RuntimeDeploymentContext


@dataclass(frozen=True, slots=True)
class RuntimeConfigReadiness:
    ready: bool
    detail: str
    proof: RuntimeConfigProof | None = None


def _mounted_generation_root(config_directory: Path) -> Path:
    data_link = config_directory / "..data"
    if data_link.is_symlink() or data_link.exists():
        return data_link.resolve(strict=True)
    return config_directory.resolve(strict=True)


def read_mounted_session_config(config_directory: str | Path) -> MountedSessionConfig:
    """Read root, upload selection, and deployment context from one generation."""
    root = _mounted_generation_root(Path(config_directory))
    root_yaml = (root / SESSION_YAML_FILENAME).read_bytes()
    run_id = read_runtime_session_run_id_file(root / SESSION_RUN_ID_FILENAME)
    catalog_upload = CatalogUploadSelection.model_validate_json(
        (root / CATALOG_UPLOAD_SELECTION_FILENAME).read_bytes(),
        strict=True,
    )
    deployment_context = RuntimeDeploymentContext.model_validate_json(
        (root / RUNTIME_DEPLOYMENT_CONTEXT_FILENAME).read_bytes(),
        strict=True,
    )
    return MountedSessionConfig(
        generation_root=root,
        root_yaml=root_yaml,
        run_id=run_id,
        catalog_upload=catalog_upload,
        deployment_context=deployment_context,
    )


def wait_for_mounted_session_config(
    config_directory: str | Path,
    *,
    poll_seconds: float = 5.0,
    sleep: Callable[[float], None] = time.sleep,
    log: logging.Logger | None = None,
) -> MountedSessionConfig:
    """Wait for a complete directory-mounted runtime selection."""
    directory = Path(config_directory)
    if poll_seconds <= 0:
        raise ValueError("poll_seconds must be positive")
    while True:
        try:
            if (directory / SESSION_YAML_FILENAME).is_file():
                return read_mounted_session_config(directory)
        except FileNotFoundError:
            pass
        if log is not None:
            log.debug("Waiting for session config in %s...", directory)
        sleep(poll_seconds)


def _incluster_core_v1() -> Any:
    from kubernetes import client, config

    config.load_incluster_config()
    return client.CoreV1Api()


def _new_process_runtime_destination(runtime_parent: str | Path | None, origin: str) -> Path:
    parent = Path(runtime_parent) if runtime_parent is not None else None
    if parent is not None:
        parent = parent.resolve(strict=True)
        if not parent.is_dir():
            raise ValueError("runtime_parent must be a directory")
    safe_origin = "".join(character if character.isalnum() else "-" for character in origin)
    process_root = Path(
        tempfile.mkdtemp(
            prefix=f"nodalarc-{safe_origin or 'runtime'}-",
            dir=parent,
        )
    )
    return process_root / "runtime"


def load_mounted_runtime_config(
    *,
    config_directory: str | Path,
    installed_shipped_root: str | Path,
    origin: str,
    namespace: str,
    pod_uid: str,
    release: str,
    build: str,
    core_v1: Any | None = None,
    runtime_parent: str | Path | None = None,
    poll_seconds: float = 5.0,
    sleep: Callable[[float], None] = time.sleep,
    log: logging.Logger | None = None,
) -> ResolvedRuntimeConfig:
    """Load one required upload selection and return its deployment-bound proof."""
    if not isinstance(origin, str) or not origin.strip():
        raise TypeError("origin must be a non-empty string")
    if not isinstance(namespace, str) or not namespace.strip():
        raise TypeError("namespace must be a non-empty string")
    mounted = wait_for_mounted_session_config(
        config_directory,
        poll_seconds=poll_seconds,
        sleep=sleep,
        log=log,
    )
    destination = _new_process_runtime_destination(runtime_parent, origin)
    process_root = destination.parent
    source_context = SourceContext(origin=origin, run_id=mounted.run_id)
    try:
        context = mounted.deployment_context
        selection = mounted.catalog_upload
        if context.release != release:
            raise ValueError("mounted deployment release differs from the running service")
        if context.build != build:
            raise ValueError("mounted deployment build differs from the running service")
        if context.session_run_id != mounted.run_id:
            raise ValueError("mounted deployment context has the wrong session run ID")
        if context.upload_id != selection.upload_id:
            raise ValueError("mounted deployment context has the wrong upload ID")
        if context.document_digest != sha256_digest(mounted.root_yaml):
            raise ValueError("mounted deployment context has the wrong session digest")
        if context.closure_digest != selection.closure_digest:
            raise ValueError("mounted deployment context has the wrong closure digest")
        runtime_config = load_kubernetes_runtime_config(
            core_v1 if core_v1 is not None else _incluster_core_v1(),
            namespace=namespace,
            root_yaml=mounted.root_yaml,
            selection=selection,
            destination=destination,
            installed_shipped_root=installed_shipped_root,
            source_context=source_context,
        )
        bound = replace(
            runtime_config,
            proof=runtime_config.proof.bind_deployment_identity(
                context,
                pod_uid=pod_uid,
            ),
        )
        try:
            write_runtime_config_proof(bound, destination=destination)
        except Exception:
            shutil.rmtree(destination, ignore_errors=True)
            raise
        return bound
    except Exception:
        shutil.rmtree(process_root, ignore_errors=True)
        raise


class RuntimeConfigHealth:
    """Liveness-independent readiness for the currently mounted file set."""

    def __init__(self, config_directory: str | Path, *, pod_uid: str) -> None:
        if not isinstance(pod_uid, str) or not pod_uid.strip():
            raise ValueError("pod_uid must be a non-empty string")
        self._config_directory = Path(config_directory)
        self._pod_uid = pod_uid
        self._proof_path: Path | None = None
        self._lock = threading.Lock()

    def mark_loaded(self, runtime_config: ResolvedRuntimeConfig) -> None:
        proof_path = runtime_config.session_path.parent / RUNTIME_CONFIG_PROOF_FILENAME
        proof = RuntimeConfigProof.model_validate_json(proof_path.read_bytes())
        if proof != runtime_config.proof:
            raise ValueError("persisted runtime proof differs from the loaded proof")
        with self._lock:
            self._proof_path = proof_path

    def liveness(self) -> RuntimeConfigReadiness:
        return RuntimeConfigReadiness(True, "process alive")

    def readiness(self) -> RuntimeConfigReadiness:
        session_path = self._config_directory / SESSION_YAML_FILENAME
        if not session_path.is_file():
            return RuntimeConfigReadiness(True, "waiting for session")
        with self._lock:
            proof_path = self._proof_path
        if proof_path is None:
            return RuntimeConfigReadiness(False, "session present without runtime proof")
        try:
            mounted = read_mounted_session_config(self._config_directory)
            proof = RuntimeConfigProof.model_validate_json(proof_path.read_bytes())
        except Exception as exc:
            return RuntimeConfigReadiness(False, f"runtime proof unavailable: {type(exc).__name__}")
        context = mounted.deployment_context
        selection = mounted.catalog_upload
        if sha256_digest(mounted.root_yaml) != proof.document_digest:
            return RuntimeConfigReadiness(False, "mounted session differs from runtime proof")
        if mounted.run_id != proof.run_id:
            return RuntimeConfigReadiness(False, "mounted run identity differs from runtime proof")
        if {
            "upload_id": selection.upload_id,
            "closure_digest": selection.closure_digest,
            "file_count": selection.file_count,
        } != {
            "upload_id": proof.upload_id,
            "closure_digest": proof.closure_digest,
            "file_count": proof.file_count,
        }:
            return RuntimeConfigReadiness(False, "mounted upload selection differs from proof")
        expected_identity = {
            "cr_uid": context.cr_uid,
            "cr_generation": context.cr_generation,
            "pod_uid": self._pod_uid,
            "release": context.release,
            "build": context.build,
        }
        observed_identity = {
            "cr_uid": proof.cr_uid,
            "cr_generation": proof.cr_generation,
            "pod_uid": proof.pod_uid,
            "release": proof.release,
            "build": proof.build,
        }
        if observed_identity != expected_identity:
            return RuntimeConfigReadiness(False, "mounted deployment identity differs from proof")
        if {
            "run_id": context.session_run_id,
            "upload_id": context.upload_id,
            "document_digest": context.document_digest,
            "closure_digest": context.closure_digest,
            "resolved_semantic_digest": context.resolved_semantic_digest,
        } != {
            "run_id": proof.run_id,
            "upload_id": proof.upload_id,
            "document_digest": proof.document_digest,
            "closure_digest": proof.closure_digest,
            "resolved_semantic_digest": proof.resolved_semantic_digest,
        }:
            return RuntimeConfigReadiness(False, "mounted deployment context differs from proof")
        return RuntimeConfigReadiness(True, "runtime configuration verified", proof)


def start_runtime_health_server(
    health: RuntimeConfigHealth,
    *,
    port: int = 8081,
) -> ThreadingHTTPServer:
    """Start liveness and proof-gated readiness endpoints in a daemon thread."""

    class _Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            payload: dict[str, Any]
            if self.path in {"/", "/livez"}:
                liveness = health.liveness()
                status = 200 if liveness.ready else 503
                payload = {"status": "ok", "detail": liveness.detail}
            elif self.path == "/readyz":
                readiness = health.readiness()
                status = 200 if readiness.ready else 503
                payload = {
                    "status": "ready" if readiness.ready else "not-ready",
                    "detail": readiness.detail,
                }
                if readiness.ready and readiness.proof is not None:
                    payload["proof"] = readiness.proof.model_dump(mode="json")
            else:
                status = 404
                payload = {"status": "not-found"}
            content = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            with contextlib.suppress(BrokenPipeError, ConnectionResetError):
                self.wfile.write(content)

        def log_message(self, *args: object) -> None:
            pass

    server = ThreadingHTTPServer(("0.0.0.0", port), _Handler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server
