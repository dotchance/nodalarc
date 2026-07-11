"""Load an ordinary prepared session tree through the canonical file resolver."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from nodalarc.catalog_closure import (
    CatalogClosureCollector,
    CatalogClosureError,
    CatalogClosureErrorCode,
    FilesystemCatalogReadView,
)
from nodalarc.catalog_paths import CatalogPathError, CatalogRoots
from nodalarc.catalog_refs import CatalogRef
from nodalarc.resolve_session import SessionResolution, load_session_resolution_from_file
from nodalarc.runtime_support import RuntimeSupport


class PreparedTreeErrorCode(StrEnum):
    INVALID_SESSION_PATH = "prepared_tree.invalid_session_path"
    INVALID_CATALOG_TREE = "prepared_tree.invalid_catalog_tree"
    INSTALLED_SHIPPED_CATALOG_UNAVAILABLE = "prepared_tree.installed_shipped_catalog_unavailable"
    SHIPPED_ASSET_MISMATCH = "prepared_tree.shipped_asset_mismatch"
    MISSING_CATALOG_REFERENCE = "prepared_tree.missing_catalog_reference"


@dataclass(frozen=True, slots=True)
class PreparedTreeErrorEvidence:
    code: PreparedTreeErrorCode
    message: str
    ref: str | None = None
    expected_path: str | None = None
    observed_path: str | None = None
    cause_type: str | None = None


class PreparedTreeError(ValueError):
    """Typed refusal while discovering or verifying a prepared session tree."""

    def __init__(self, evidence: PreparedTreeErrorEvidence) -> None:
        super().__init__(evidence.message)
        self.evidence = evidence

    @property
    def code(self) -> PreparedTreeErrorCode:
        return self.evidence.code


def _error(
    code: PreparedTreeErrorCode,
    message: str,
    *,
    ref: str | None = None,
    expected_path: Path | None = None,
    observed_path: Path | None = None,
    cause: BaseException | None = None,
) -> PreparedTreeError:
    return PreparedTreeError(
        PreparedTreeErrorEvidence(
            code=code,
            message=message,
            ref=ref,
            expected_path=str(expected_path) if expected_path is not None else None,
            observed_path=str(observed_path) if observed_path is not None else None,
            cause_type=type(cause).__name__ if cause is not None else None,
        )
    )


def _session_file(session_path: str | Path) -> Path:
    path = Path(session_path)
    if not path.is_file():
        raise _error(
            PreparedTreeErrorCode.INVALID_SESSION_PATH,
            f"Prepared session YAML is not a file: {path}",
            expected_path=path,
        )
    return path


def _installed_shipped_root(installed_shipped_root: str | Path) -> Path:
    path = Path(installed_shipped_root)
    try:
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise _error(
            PreparedTreeErrorCode.INSTALLED_SHIPPED_CATALOG_UNAVAILABLE,
            f"Installed shipped catalog root is not available: {path}",
            expected_path=path,
            cause=exc,
        ) from exc
    if not resolved.is_dir():
        raise _error(
            PreparedTreeErrorCode.INSTALLED_SHIPPED_CATALOG_UNAVAILABLE,
            f"Installed shipped catalog root is not a directory: {path}",
            expected_path=path,
        )
    return resolved


def _optional_tree_directory(path: Path, *, label: str) -> Path | None:
    if not path.exists():
        return None
    if path.is_symlink() or not path.is_dir():
        raise _error(
            PreparedTreeErrorCode.INVALID_CATALOG_TREE,
            f"Prepared session {label} must be an ordinary directory: {path}",
            observed_path=path,
        )
    return path


def _assert_tree_shipped_assets(tree_root: Path | None, installed_root: Path) -> None:
    if tree_root is None:
        return
    for observed_path in sorted(tree_root.rglob("*")):
        if observed_path.is_symlink():
            raise _error(
                PreparedTreeErrorCode.INVALID_CATALOG_TREE,
                f"Prepared shipped catalog entries must be ordinary files: {observed_path}",
                observed_path=observed_path,
            )
        if observed_path.is_dir():
            continue
        if not observed_path.is_file():
            raise _error(
                PreparedTreeErrorCode.INVALID_CATALOG_TREE,
                f"Prepared shipped catalog entry is not a regular file: {observed_path}",
                observed_path=observed_path,
            )
        relative_path = observed_path.relative_to(tree_root)
        ref = f"nodalarc:{relative_path.as_posix()}"
        expected_path = installed_root / relative_path
        try:
            installed_path = expected_path.resolve(strict=True)
            installed_path.relative_to(installed_root)
            expected_bytes = installed_path.read_bytes()
            observed_bytes = observed_path.read_bytes()
        except (OSError, ValueError) as exc:
            raise _error(
                PreparedTreeErrorCode.SHIPPED_ASSET_MISMATCH,
                f"Prepared shipped asset {ref} is not present in the installed catalog",
                ref=ref,
                expected_path=expected_path,
                observed_path=observed_path,
                cause=exc,
            ) from exc
        if observed_bytes != expected_bytes:
            raise _error(
                PreparedTreeErrorCode.SHIPPED_ASSET_MISMATCH,
                f"Prepared shipped asset {ref} differs from the installed read-only asset",
                ref=ref,
                expected_path=installed_path,
                observed_path=observed_path,
            )


def _raise_typed_missing_reference(
    root_yaml: bytes,
    roots: CatalogRoots,
    *,
    session_path: Path,
    user_root: Path,
    cause: BaseException,
) -> None:
    try:
        CatalogClosureCollector.collect(root_yaml, FilesystemCatalogReadView(roots))
    except CatalogClosureError as diagnostic:
        evidence = diagnostic.evidence
        missing = evidence.code is CatalogClosureErrorCode.DANGLING_REFERENCE or (
            evidence.code is CatalogClosureErrorCode.READ_FAILED
            and evidence.cause_type in {"FileNotFoundError", "NotADirectoryError"}
        )
        if missing and evidence.ref is not None:
            ref = CatalogRef(evidence.ref)
            root = user_root if ref.namespace == "user" else roots.root
            expected_path = root / ref.relative_path
            raise _error(
                PreparedTreeErrorCode.MISSING_CATALOG_REFERENCE,
                f"Session {session_path} references {ref}; expected it at "
                f"{expected_path}; not found",
                ref=str(ref),
                expected_path=expected_path,
                cause=diagnostic,
            ) from cause
        raise diagnostic from cause


def load_prepared_tree_session_resolution(
    session_path: str | Path,
    *,
    installed_shipped_root: str | Path,
    runtime_support: RuntimeSupport | None = None,
    origin: str = "prepared-tree",
    run_id: str | None = None,
) -> SessionResolution:
    """Resolve a session plus its optional adjacent ordinary-file catalog tree.

    ``nodalarc:`` objects always resolve from the installed read-only catalog.
    Any adjacent ``catalog/nodalarc`` copies are exact-byte verified first.
    ``user:`` objects resolve from ``catalog/user`` beside the session file.
    """

    path = _session_file(session_path)
    installed_root = _installed_shipped_root(installed_shipped_root)
    tree_catalog_root = path.parent / "catalog"
    if tree_catalog_root.exists() and (
        tree_catalog_root.is_symlink() or not tree_catalog_root.is_dir()
    ):
        raise _error(
            PreparedTreeErrorCode.INVALID_CATALOG_TREE,
            f"Prepared session catalog must be an ordinary directory: {tree_catalog_root}",
            observed_path=tree_catalog_root,
        )
    tree_shipped_root = _optional_tree_directory(
        tree_catalog_root / "nodalarc",
        label="catalog/nodalarc tree",
    )
    discovered_user_root = _optional_tree_directory(
        tree_catalog_root / "user",
        label="catalog/user tree",
    )
    expected_user_root = tree_catalog_root / "user"

    _assert_tree_shipped_assets(tree_shipped_root, installed_root)
    roots = CatalogRoots.from_catalog_root(
        installed_root,
        user_root=discovered_user_root or expected_user_root,
    )
    try:
        return load_session_resolution_from_file(
            path,
            catalog_roots=roots,
            runtime_support=runtime_support,
            origin=origin,
            run_id=run_id,
        )
    except (FileNotFoundError, NotADirectoryError, CatalogPathError) as exc:
        _raise_typed_missing_reference(
            path.read_bytes(),
            roots,
            session_path=path,
            user_root=expected_user_root,
            cause=exc,
        )
        raise
