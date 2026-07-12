"""Filesystem-backed scoped catalog repository with atomic generations."""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import tempfile
import threading
import uuid
import weakref
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from nodalarc.catalog_closure import (
    CatalogClosureCollector,
    CatalogClosureError,
    CatalogReadDocument,
    preserved_catalog_path,
)
from nodalarc.catalog_refs import (
    CatalogFamily,
    CatalogNamespace,
    CatalogRef,
    CatalogReferenceError,
    parse_catalog_reference,
)
from nodalarc.catalog_registry import (
    catalog_family_spec,
    validate_referenced_configuration_document,
)
from nodalarc.catalog_repository import (
    CatalogConflictError,
    CatalogContainmentError,
    CatalogDocument,
    CatalogEntry,
    CatalogGeneration,
    CatalogNotFoundError,
    CatalogReadOnlyError,
    CatalogReadSnapshot,
    CatalogRepository,
    CatalogRevision,
    CatalogScope,
    CatalogTransactionOrderError,
    CatalogTransactionStateError,
    CatalogUnitOfWork,
    CatalogValidationError,
    UnknownCatalogScopeError,
)
from nodalarc.configuration_yaml import load_configuration_yaml

_DIGEST_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")
_YAML_SUFFIXES = frozenset({".yaml", ".yml"})
_GENERATION_DOMAIN = b"nodalarc-user-catalog-generation-v1\0"


def catalog_content_revision(content: bytes) -> CatalogRevision:
    """Return the exact-byte revision used by compare-and-swap mutations."""

    if not isinstance(content, bytes):
        raise TypeError("catalog content must be bytes")
    return CatalogRevision(f"sha256:{hashlib.sha256(content).hexdigest()}")


def _validated_revision(value: CatalogRevision | str) -> CatalogRevision:
    raw = str(value)
    if not _DIGEST_PATTERN.fullmatch(raw):
        raise CatalogValidationError("catalog revision must be sha256:<64 lowercase hex>")
    return CatalogRevision(raw)


def _parsed_ref(value: str | CatalogRef) -> tuple[CatalogRef, CatalogNamespace, CatalogFamily]:
    try:
        ref = value if isinstance(value, CatalogRef) else CatalogRef(value)
        parsed = parse_catalog_reference(ref)
        if parsed.family is None:
            raise ValueError("catalog references must include a registered family directory")
        family = catalog_family_spec(parsed.family).family
    except (CatalogReferenceError, TypeError, ValueError) as exc:
        raise CatalogValidationError(f"invalid catalog reference {value!r}: {exc}") from exc
    return ref, parsed.namespace, family


def _writable_ref(value: str | CatalogRef) -> tuple[CatalogRef, CatalogFamily]:
    ref, namespace, family = _parsed_ref(value)
    if namespace != "user":
        raise CatalogReadOnlyError(f"shipped catalog reference {ref!r} is read-only")
    return ref, family


def _validated_document(ref: CatalogRef, content: bytes) -> CatalogFamily:
    _ref, _namespace, family = _parsed_ref(ref)
    try:
        text = content.decode("utf-8")
        data = load_configuration_yaml(text)
        _wrapper, _model = validate_referenced_configuration_document(ref, data)
    except (UnicodeDecodeError, yaml.YAMLError, TypeError, ValueError) as exc:
        raise CatalogValidationError(f"catalog document {ref!r} is invalid: {exc}") from exc
    return family


def _assert_injected_root(path: Path, *, label: str, create: bool) -> Path:
    absolute = path.absolute()
    current = Path(absolute.anchor)
    for part in absolute.parts[1:]:
        current = current / part
        if current.is_symlink():
            raise CatalogContainmentError(f"{label} path components must not be symlinks")
    if create:
        absolute.mkdir(parents=True, exist_ok=True)
    if not absolute.is_dir():
        raise CatalogContainmentError(f"{label} must be a directory")
    return absolute.resolve(strict=True)


def _paths_overlap(first: Path, second: Path) -> bool:
    return first == second or first in second.parents or second in first.parents


def _assert_no_symlink(root: Path, relative: Path) -> None:
    current = root
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            raise CatalogContainmentError(f"catalog path contains symlink {current}")


def _contained_file(root: Path, relative: Path) -> Path:
    if relative.is_absolute() or ".." in relative.parts:
        raise CatalogContainmentError("catalog path must remain relative and contained")
    _assert_no_symlink(root, relative)
    candidate = root / relative
    if not candidate.is_file():
        raise CatalogNotFoundError(f"catalog document does not exist: {relative.as_posix()}")
    try:
        candidate.resolve(strict=True).relative_to(root)
    except ValueError as exc:
        raise CatalogContainmentError(f"catalog path escapes injected root: {relative}") from exc
    return candidate


def _read_exact_file(root: Path, relative: Path) -> bytes:
    return _contained_file(root, relative).read_bytes()


def _iter_document_refs(
    root: Path,
    namespace: CatalogNamespace,
) -> tuple[CatalogRef, ...]:
    if not root.is_dir():
        raise CatalogContainmentError(f"catalog root is unavailable: {root}")

    refs: list[CatalogRef] = []
    for path in root.rglob("*"):
        relative = path.relative_to(root)
        _assert_no_symlink(root, relative)
        if path.is_dir():
            continue
        if not path.is_file():
            raise CatalogContainmentError(f"catalog entry is not a regular file: {path}")
        if path.suffix.lower() not in _YAML_SUFFIXES:
            raise CatalogValidationError(f"catalog contains non-YAML file: {relative.as_posix()}")
        ref, parsed_namespace, _family = _parsed_ref(f"{namespace}:{relative.as_posix()}")
        if parsed_namespace != namespace:
            raise CatalogContainmentError("catalog namespace changed during filesystem enumeration")
        refs.append(ref)
    return tuple(sorted(refs, key=str))


def _catalog_generation(documents: Iterable[tuple[CatalogRef, bytes]]) -> CatalogGeneration:
    digest = hashlib.sha256(_GENERATION_DOMAIN)
    for ref, content in sorted(documents, key=lambda item: str(item[0])):
        ref_bytes = str(ref).encode("utf-8")
        digest.update(len(ref_bytes).to_bytes(8, "big"))
        digest.update(ref_bytes)
        digest.update(len(content).to_bytes(8, "big"))
        digest.update(content)
    return CatalogGeneration(f"sha256:{digest.hexdigest()}")


def _validate_catalog_root(root: Path, namespace: CatalogNamespace) -> CatalogGeneration:
    documents: list[tuple[CatalogRef, bytes]] = []
    for ref in _iter_document_refs(root, namespace):
        content = _read_exact_file(root, ref.relative_path)
        _validated_document(ref, content)
        documents.append((ref, content))
    return _catalog_generation(documents)


def _write_exact_file(root: Path, relative: Path, content: bytes) -> None:
    if relative.is_absolute() or ".." in relative.parts:
        raise CatalogContainmentError("catalog write path must remain relative and contained")
    destination = root / relative
    destination.parent.mkdir(parents=True, exist_ok=True)
    _assert_no_symlink(root, relative.parent)
    try:
        destination.parent.resolve(strict=True).relative_to(root)
    except ValueError as exc:
        raise CatalogContainmentError(f"catalog write escapes staging root: {relative}") from exc
    with destination.open("wb") as handle:
        handle.write(content)
        handle.flush()
        os.fsync(handle.fileno())


def _prune_empty_directories(path: Path, stop: Path) -> None:
    current = path
    while current != stop:
        try:
            current.rmdir()
        except OSError:
            return
        current = current.parent


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


@dataclass(frozen=True, slots=True)
class _ScopeState:
    scope: CatalogScope
    root: Path
    generations_root: Path
    current_path: Path


@dataclass(frozen=True, slots=True)
class _Mutation:
    ref: CatalogRef
    family: CatalogFamily
    content: bytes | None
    expected_revision: CatalogRevision | None

    @property
    def is_delete(self) -> bool:
        return self.content is None

    @property
    def is_session_publication(self) -> bool:
        return self.family == "sessions" and self.content is not None


@dataclass(frozen=True, slots=True, weakref_slot=True)
class FilesystemCatalogSnapshot(CatalogReadSnapshot):
    """Exact-byte read view pinned to one immutable generation directory."""

    _scope: CatalogScope
    _generation: CatalogGeneration
    _shipped_root: Path = field(repr=False)
    _user_root: Path = field(repr=False)

    @property
    def scope(self) -> CatalogScope:
        return self._scope

    @property
    def generation(self) -> CatalogGeneration:
        return self._generation

    def _root_for(self, namespace: CatalogNamespace) -> Path:
        return self._shipped_root if namespace == "nodalarc" else self._user_root

    def read_bytes(self, ref: str | CatalogRef) -> bytes:
        return self.get(ref).content

    def read(self, ref: CatalogRef) -> CatalogReadDocument:
        parsed_ref, namespace, family = _parsed_ref(ref)
        try:
            content = _read_exact_file(self._root_for(namespace), parsed_ref.relative_path)
        except CatalogNotFoundError as exc:
            raise FileNotFoundError(str(parsed_ref)) from exc
        except CatalogContainmentError as exc:
            raise OSError(str(exc)) from exc
        return CatalogReadDocument(
            family=family,
            preserved_path=preserved_catalog_path(parsed_ref),
            yaml_bytes=content,
        )

    def get(self, ref: str | CatalogRef) -> CatalogDocument:
        parsed_ref, namespace, family = _parsed_ref(ref)
        content = _read_exact_file(self._root_for(namespace), parsed_ref.relative_path)
        validated_family = _validated_document(parsed_ref, content)
        if validated_family != family:
            raise CatalogValidationError(f"catalog family changed while reading {parsed_ref!r}")
        return CatalogDocument(
            ref=parsed_ref,
            namespace=namespace,
            family=family,
            content=content,
            revision=catalog_content_revision(content),
        )

    def list(
        self,
        *,
        namespace: CatalogNamespace | None = None,
        family: CatalogFamily | None = None,
    ) -> tuple[CatalogEntry, ...]:
        if namespace not in {None, "nodalarc", "user"}:
            raise CatalogValidationError(f"unknown catalog namespace {namespace!r}")
        if family is not None:
            try:
                catalog_family_spec(family)
            except ValueError as exc:
                raise CatalogValidationError(str(exc)) from exc

        namespaces: tuple[CatalogNamespace, ...] = (
            (namespace,) if namespace is not None else ("nodalarc", "user")
        )
        entries: list[CatalogEntry] = []
        for selected_namespace in namespaces:
            for ref in _iter_document_refs(self._root_for(selected_namespace), selected_namespace):
                document = self.get(ref)
                if family is not None and document.family != family:
                    continue
                entries.append(
                    CatalogEntry(
                        ref=document.ref,
                        namespace=document.namespace,
                        family=document.family,
                        revision=document.revision,
                        size_bytes=len(document.content),
                    )
                )
        return tuple(sorted(entries, key=lambda entry: str(entry.ref)))

    def export_documents(
        self,
        *,
        namespace: CatalogNamespace | None = None,
        family: CatalogFamily | None = None,
    ) -> tuple[CatalogDocument, ...]:
        return tuple(self.get(entry.ref) for entry in self.list(namespace=namespace, family=family))


class FilesystemCatalogUnitOfWork(CatalogUnitOfWork):
    """Mutable transaction builder whose commit is delegated to one repository."""

    def __init__(
        self,
        repository: FilesystemCatalogRepository,
        scope: CatalogScope,
        base_generation: CatalogGeneration,
    ) -> None:
        self._repository = repository
        self._scope = scope
        self._base_generation = base_generation
        self._mutations: dict[CatalogRef, _Mutation] = {}
        self._session_publication_seen = False
        self._closed = False

    @property
    def base_generation(self) -> CatalogGeneration:
        return self._base_generation

    def _assert_open(self) -> None:
        if self._closed:
            raise CatalogTransactionStateError("catalog unit of work is closed")

    def _assert_can_add(self, ref: CatalogRef, *, is_session_publication: bool) -> None:
        self._assert_open()
        if self._session_publication_seen and not is_session_publication:
            raise CatalogTransactionOrderError(
                "session publication must be the final logical transaction mutation"
            )
        if ref in self._mutations:
            raise CatalogTransactionStateError(f"catalog reference {ref!r} is mutated twice")

    def write_bytes(
        self,
        ref: str | CatalogRef,
        content: bytes,
        *,
        expected_revision: CatalogRevision | str | None,
    ) -> None:
        parsed_ref, family = _writable_ref(ref)
        is_session_publication = family == "sessions"
        self._assert_can_add(
            parsed_ref,
            is_session_publication=is_session_publication,
        )
        if not isinstance(content, bytes):
            raise TypeError("catalog content must be bytes")
        revision = None if expected_revision is None else _validated_revision(expected_revision)
        self._mutations[parsed_ref] = _Mutation(
            ref=parsed_ref,
            family=family,
            content=bytes(content),
            expected_revision=revision,
        )
        if is_session_publication:
            self._session_publication_seen = True

    def delete(
        self,
        ref: str | CatalogRef,
        *,
        expected_revision: CatalogRevision | str,
    ) -> None:
        parsed_ref, family = _writable_ref(ref)
        self._assert_can_add(parsed_ref, is_session_publication=False)
        self._mutations[parsed_ref] = _Mutation(
            ref=parsed_ref,
            family=family,
            content=None,
            expected_revision=_validated_revision(expected_revision),
        )

    def commit(self) -> FilesystemCatalogSnapshot:
        self._assert_open()
        try:
            if not self._mutations:
                raise CatalogTransactionStateError("catalog unit of work has no mutations")
            return self._repository._commit(
                self._scope,
                self._base_generation,
                tuple(self._mutations.values()),
            )
        finally:
            self._closed = True

    def abort(self) -> None:
        self._mutations.clear()
        self._closed = True


class FilesystemCatalogRepository(CatalogRepository):
    """Copy-on-write repository with one in-process writer lock.

    This class intentionally makes no cross-process, cross-pod, or HA writer
    guarantee. A deployment must designate one process with one repository
    instance as the sole writer for the injected scope roots. Readers need no
    lock because generations are immutable and activation replaces one
    ``CURRENT`` file atomically.
    """

    writer_coordination = "single-process"
    generation_retention = "current-plus-live-snapshots"

    def __init__(
        self,
        *,
        shipped_root: str | Path,
        scope_roots: Mapping[CatalogScope, str | Path],
    ) -> None:
        self._writer_lock = threading.RLock()
        self._active_snapshots: dict[tuple[CatalogScope, CatalogGeneration], int] = {}
        self._shipped_root = _assert_injected_root(
            Path(shipped_root),
            label="shipped catalog root",
            create=False,
        )

        states: dict[CatalogScope, _ScopeState] = {}
        resolved_scope_roots: list[Path] = []
        for scope, configured_root in scope_roots.items():
            if not isinstance(scope, CatalogScope):
                raise TypeError("scope_roots keys must be opaque CatalogScope instances")
            root = _assert_injected_root(
                Path(configured_root),
                label="user catalog scope root",
                create=True,
            )
            if _paths_overlap(root, self._shipped_root):
                raise CatalogContainmentError("user and shipped catalog roots must not overlap")
            if any(_paths_overlap(root, other) for other in resolved_scope_roots):
                raise CatalogContainmentError("injected user catalog scope roots must not overlap")
            resolved_scope_roots.append(root)

            unexpected = {
                path.name
                for path in root.iterdir()
                if path.name not in {"CURRENT", "generations"}
                and not path.name.startswith(".CURRENT-")
            }
            if unexpected:
                names = ", ".join(sorted(unexpected))
                raise CatalogContainmentError(
                    f"user catalog scope root has unexpected entries: {names}"
                )

            generations_root = root / "generations"
            if generations_root.exists() and generations_root.is_symlink():
                raise CatalogContainmentError("catalog generations root must not be a symlink")
            if generations_root.exists() and not generations_root.is_dir():
                raise CatalogContainmentError("catalog generations root must be a directory")
            generations_root.mkdir(exist_ok=True)
            generations_root = generations_root.resolve(strict=True)
            state = _ScopeState(
                scope=scope,
                root=root,
                generations_root=generations_root,
                current_path=root / "CURRENT",
            )
            states[scope] = state

        self._scope_states = states
        with self._writer_lock:
            for state in self._scope_states.values():
                self._recover_scope(state)

    def _scope_state(self, scope: CatalogScope) -> _ScopeState:
        try:
            return self._scope_states[scope]
        except KeyError as exc:
            raise UnknownCatalogScopeError(
                "catalog scope was not injected into this repository"
            ) from exc

    def _recover_scope(self, state: _ScopeState) -> None:
        for path in state.root.glob(".CURRENT-*.tmp"):
            if path.is_symlink():
                raise CatalogContainmentError("CURRENT temporary file must not be a symlink")
            if not path.is_file():
                raise CatalogContainmentError("CURRENT temporary entry must be a regular file")
            path.unlink()
        for path in state.generations_root.glob(".staging-*"):
            if path.is_symlink():
                raise CatalogContainmentError("catalog staging directory must not be a symlink")
            if not path.is_dir():
                raise CatalogContainmentError("catalog staging entry must be a directory")
            shutil.rmtree(path)

        if not state.current_path.exists():
            if state.current_path.is_symlink():
                raise CatalogContainmentError("CURRENT must not be a symlink")
            generation = _catalog_generation(())
            final_directory = state.generations_root / generation.removeprefix("sha256:")
            catalog_root = final_directory / "catalog"
            if final_directory.exists() and final_directory.is_symlink():
                raise CatalogContainmentError("catalog generation must not be a symlink")
            if final_directory.exists():
                actual = _validate_catalog_root(catalog_root, "user")
                if actual != generation:
                    raise CatalogContainmentError(
                        "existing empty generation directory failed integrity check"
                    )
            else:
                catalog_root.mkdir(parents=True)
            self._publish_current(state, generation)

        generation = self._read_current(state)
        catalog_root = self._generation_catalog_root(state, generation)
        actual = _validate_catalog_root(catalog_root, "user")
        if actual != generation:
            raise CatalogContainmentError(
                f"CURRENT generation digest {generation} does not match stored catalog {actual}"
            )
        self._cleanup_inactive_generations(state, generation)

    def _read_current(self, state: _ScopeState) -> CatalogGeneration:
        if state.current_path.is_symlink():
            raise CatalogContainmentError("CURRENT must not be a symlink")
        if not state.current_path.is_file():
            raise CatalogContainmentError("CURRENT must be a regular file")
        try:
            raw = state.current_path.read_text(encoding="ascii")
        except UnicodeDecodeError as exc:
            raise CatalogContainmentError("CURRENT must contain an ASCII generation") from exc
        if not raw.endswith("\n") or raw.count("\n") != 1:
            raise CatalogContainmentError("CURRENT must contain exactly one generation line")
        generation = raw[:-1]
        if not _DIGEST_PATTERN.fullmatch(generation):
            raise CatalogContainmentError("CURRENT contains an invalid catalog generation")
        return CatalogGeneration(generation)

    def _generation_catalog_root(
        self,
        state: _ScopeState,
        generation: CatalogGeneration,
    ) -> Path:
        _validated_revision(str(generation))
        directory = state.generations_root / str(generation).removeprefix("sha256:")
        relative = directory.relative_to(state.generations_root)
        _assert_no_symlink(state.generations_root, relative)
        catalog_root = directory / "catalog"
        if not catalog_root.is_dir() or catalog_root.is_symlink():
            raise CatalogContainmentError(f"catalog generation is unavailable: {generation}")
        return catalog_root.resolve(strict=True)

    def _snapshot_at(
        self,
        state: _ScopeState,
        generation: CatalogGeneration,
    ) -> FilesystemCatalogSnapshot:
        with self._writer_lock:
            snapshot = FilesystemCatalogSnapshot(
                _scope=state.scope,
                _generation=generation,
                _shipped_root=self._shipped_root,
                _user_root=self._generation_catalog_root(state, generation),
            )
            key = (state.scope, generation)
            self._active_snapshots[key] = self._active_snapshots.get(key, 0) + 1
            weakref.finalize(snapshot, self._release_snapshot, state.scope, generation)
            return snapshot

    def snapshot(self, scope: CatalogScope) -> FilesystemCatalogSnapshot:
        with self._writer_lock:
            state = self._scope_state(scope)
            return self._snapshot_at(state, self._read_current(state))

    def _release_snapshot(
        self,
        scope: CatalogScope,
        generation: CatalogGeneration,
    ) -> None:
        with self._writer_lock:
            key = (scope, generation)
            active = self._active_snapshots.get(key, 0)
            if active <= 1:
                self._active_snapshots.pop(key, None)
            else:
                self._active_snapshots[key] = active - 1
            state = self._scope_state(scope)
            self._cleanup_inactive_generations(state, self._read_current(state))

    def _cleanup_inactive_generations(
        self,
        state: _ScopeState,
        current_generation: CatalogGeneration,
    ) -> None:
        removed = False
        for directory in sorted(state.generations_root.iterdir()):
            if directory.name.startswith(".staging-"):
                continue
            if directory.is_symlink() or not directory.is_dir():
                raise CatalogContainmentError(
                    f"catalog generations root contains an unsafe entry: {directory.name}"
                )
            candidate = CatalogGeneration(f"sha256:{directory.name}")
            try:
                _validated_revision(candidate)
            except CatalogValidationError as exc:
                raise CatalogContainmentError(
                    f"catalog generations root contains an invalid generation: {directory.name}"
                ) from exc
            if candidate == current_generation:
                continue
            if self._active_snapshots.get((state.scope, candidate), 0) > 0:
                continue
            actual = _validate_catalog_root(directory / "catalog", "user")
            if actual != candidate:
                raise CatalogContainmentError(
                    f"inactive catalog generation {candidate} failed integrity validation"
                )
            shutil.rmtree(directory)
            removed = True
        if removed:
            _fsync_directory(state.generations_root)

    def begin(
        self,
        scope: CatalogScope,
        *,
        base_generation: CatalogGeneration | None = None,
    ) -> FilesystemCatalogUnitOfWork:
        if base_generation is None:
            generation = self.snapshot(scope).generation
        else:
            self._scope_state(scope)
            generation = CatalogGeneration(str(_validated_revision(str(base_generation))))
        return FilesystemCatalogUnitOfWork(self, scope, generation)

    def _check_preconditions(
        self,
        snapshot: FilesystemCatalogSnapshot,
        mutations: tuple[_Mutation, ...],
    ) -> None:
        for mutation in mutations:
            try:
                existing = snapshot.get(mutation.ref)
            except CatalogNotFoundError:
                existing = None

            if mutation.is_delete:
                if existing is None:
                    raise CatalogConflictError(
                        f"cannot delete absent catalog document {mutation.ref!r}"
                    )
                if existing.revision != mutation.expected_revision:
                    raise CatalogConflictError(
                        f"stale delete revision for {mutation.ref!r}: expected "
                        f"{mutation.expected_revision}, current {existing.revision}"
                    )
            elif mutation.expected_revision is None:
                if existing is not None:
                    raise CatalogConflictError(f"catalog document already exists: {mutation.ref!r}")
            elif existing is None:
                raise CatalogConflictError(
                    f"cannot replace absent catalog document {mutation.ref!r}"
                )
            elif existing.revision != mutation.expected_revision:
                raise CatalogConflictError(
                    f"stale write revision for {mutation.ref!r}: expected "
                    f"{mutation.expected_revision}, current {existing.revision}"
                )

    def _stage_base(
        self,
        snapshot: FilesystemCatalogSnapshot,
        catalog_root: Path,
    ) -> None:
        for document in snapshot.export_documents(namespace="user"):
            _write_exact_file(catalog_root, document.ref.relative_path, document.content)

    def _validate_staged_reference_graph(
        self,
        state: _ScopeState,
        base_generation: CatalogGeneration,
        catalog_root: Path,
    ) -> None:
        staged_view = FilesystemCatalogSnapshot(
            _scope=state.scope,
            _generation=base_generation,
            _shipped_root=self._shipped_root,
            _user_root=catalog_root,
        )
        refs = _iter_document_refs(catalog_root, "user")
        try:
            CatalogClosureCollector.collect_references(refs, staged_view)
        except CatalogClosureError as exc:
            raise CatalogValidationError(
                f"staged catalog reference graph is invalid [{exc.code.value}]: {exc}"
            ) from exc

    def _apply_mutations(self, catalog_root: Path, mutations: tuple[_Mutation, ...]) -> None:
        ordered = sorted(
            mutations,
            key=lambda mutation: (mutation.is_session_publication, str(mutation.ref)),
        )
        for mutation in ordered:
            destination = catalog_root / mutation.ref.relative_path
            if mutation.is_delete:
                if destination.is_symlink():
                    raise CatalogContainmentError("catalog delete target must not be a symlink")
                destination.unlink()
                _prune_empty_directories(destination.parent, catalog_root)
            else:
                assert mutation.content is not None
                _write_exact_file(catalog_root, mutation.ref.relative_path, mutation.content)

    def _finalize_generation(
        self,
        state: _ScopeState,
        staging_directory: Path,
        generation: CatalogGeneration,
    ) -> None:
        final_directory = state.generations_root / str(generation).removeprefix("sha256:")
        if final_directory.exists():
            if final_directory.is_symlink():
                raise CatalogContainmentError("catalog generation must not be a symlink")
            existing_root = final_directory / "catalog"
            existing_generation = _validate_catalog_root(existing_root, "user")
            if existing_generation != generation:
                raise CatalogContainmentError(
                    "existing generation directory failed integrity check"
                )
            shutil.rmtree(staging_directory)
            return
        os.replace(staging_directory, final_directory)
        _fsync_directory(state.generations_root)

    def _publish_current(self, state: _ScopeState, generation: CatalogGeneration) -> None:
        if state.current_path.is_symlink():
            raise CatalogContainmentError("CURRENT must not be a symlink")
        temporary = state.root / f".CURRENT-{uuid.uuid4().hex}.tmp"
        try:
            with temporary.open("xb") as handle:
                handle.write(f"{generation}\n".encode("ascii"))
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, state.current_path)
            _fsync_directory(state.root)
        finally:
            if temporary.exists() and not temporary.is_symlink():
                temporary.unlink()

    def _commit(
        self,
        scope: CatalogScope,
        base_generation: CatalogGeneration,
        mutations: tuple[_Mutation, ...],
    ) -> FilesystemCatalogSnapshot:
        state = self._scope_state(scope)
        with self._writer_lock:
            current_generation = self._read_current(state)
            if current_generation != base_generation:
                raise CatalogConflictError(
                    f"catalog generation is stale: expected {base_generation}, "
                    f"current {current_generation}"
                )
            base_snapshot = self._snapshot_at(state, current_generation)
            self._check_preconditions(base_snapshot, mutations)

            staging_directory = Path(
                tempfile.mkdtemp(prefix=".staging-", dir=state.generations_root)
            )
            catalog_root = staging_directory / "catalog"
            catalog_root.mkdir()
            try:
                self._stage_base(base_snapshot, catalog_root)
                self._apply_mutations(catalog_root, mutations)
                generation = _validate_catalog_root(catalog_root, "user")
                self._validate_staged_reference_graph(state, current_generation, catalog_root)
                self._finalize_generation(state, staging_directory, generation)
                self._publish_current(state, generation)
                self._cleanup_inactive_generations(state, generation)
            except BaseException:
                if staging_directory.exists() and not staging_directory.is_symlink():
                    shutil.rmtree(staging_directory)
                raise
            return self._snapshot_at(state, generation)
