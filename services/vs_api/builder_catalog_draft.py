"""Backend-owned, lossless catalog component draft authoring."""

from __future__ import annotations

import copy
import json
import re
from dataclasses import dataclass
from typing import Any, NoReturn, cast

import yaml
from nodalarc.catalog_closure import (
    CatalogClosureCollector,
    CatalogClosureError,
    CatalogReadDocument,
    CatalogReadView,
    preserved_catalog_path,
)
from nodalarc.catalog_refs import CatalogRef
from nodalarc.catalog_registry import catalog_family_spec
from nodalarc.catalog_repository import (
    CatalogDocument,
    CatalogNotFoundError,
    CatalogReadSnapshot,
    CatalogRepositoryError,
)
from nodalarc.configuration_yaml import load_configuration_yaml
from nodalarc.models.builder_api import JsonDocument
from nodalarc.models.builder_catalog_api import (
    CatalogComponentDraftEnvelope,
    CatalogComponentFamily,
    CatalogDocumentWriteRequest,
    CatalogDraftAddNodeEthernetPortRequest,
    CatalogDraftAddNodeTerminalMountRequest,
    CatalogDraftAddSiteNodeRequest,
    CatalogDraftCompileRequest,
    CatalogDraftCompileResult,
    CatalogDraftIssue,
    CatalogDraftNewRequest,
    CatalogDraftOpenRequest,
    CatalogDraftPatchCommand,
    CatalogDraftPatchRequest,
    CatalogDraftReplaceObjectRequest,
    CatalogDraftSaveRequest,
    CatalogDraftSaveResult,
    CatalogOperationRefusal,
)
from nodalarc.models.catalog import Node, Terminal
from nodalarc.runtime_support import RuntimeSupport, UnsupportedFeature
from pydantic import ValidationError

from .builder_catalog_service import BuilderCatalogAuthoringService, CatalogAuthoringError
from .builder_compiler import (
    CanonicalConfigurationDocument,
    canonicalize_persisted_configuration,
)
from .builder_visual_defaults import DEFAULT_TERMINAL_MOUNT_COUNT
from .catalog_context import CatalogContext

_MAX_POINTER_DEPTH = 32
_FORBIDDEN_POINTER_TOKENS = frozenset({".", "..", "__proto__", "constructor", "prototype"})
_ARRAY_INDEX = re.compile(r"^(?:0|[1-9][0-9]*)$")


def _reject_json_constant(value: str) -> NoReturn:
    raise ValueError(f"non-finite JSON number {value!r} is not supported")


def _unique_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key {key!r} is not supported")
        result[key] = value
    return result


def _wrapper(family: CatalogComponentFamily) -> str:
    wrapper = catalog_family_spec(family).wrapper
    if wrapper is None:
        raise AssertionError("component draft families must use catalog object wrappers")
    return wrapper


def _refuse(
    code: str,
    message: str,
    *,
    ref: CatalogRef | None = None,
    expected_revision: str | None = None,
    current_revision: str | None = None,
    cause: BaseException | None = None,
) -> NoReturn:
    raise CatalogAuthoringError(
        CatalogOperationRefusal(
            code=code,
            message=message,
            ref=str(ref) if ref is not None else None,
            expected_revision=expected_revision,
            current_revision=current_revision,
            cause_type=type(cause).__name__ if cause is not None else None,
        )
    ) from cause


def _escape_pointer_token(value: object) -> str:
    return str(value).replace("~", "~0").replace("/", "~1")


def _pointer(parts: tuple[object, ...]) -> str:
    return "/" + "/".join(_escape_pointer_token(part) for part in parts)


def _decode_pointer(value: str) -> tuple[str, ...]:
    if not value.startswith("/"):
        _refuse("catalog_authoring.invalid_patch", "JSON pointers must start with '/'")
    raw_tokens = value.split("/")[1:]
    if not raw_tokens or len(raw_tokens) > _MAX_POINTER_DEPTH:
        _refuse(
            "catalog_authoring.invalid_patch",
            f"JSON pointers must contain between 1 and {_MAX_POINTER_DEPTH} tokens",
        )
    tokens: list[str] = []
    for raw in raw_tokens:
        decoded: list[str] = []
        index = 0
        while index < len(raw):
            character = raw[index]
            if character != "~":
                decoded.append(character)
                index += 1
                continue
            if index + 1 >= len(raw) or raw[index + 1] not in {"0", "1"}:
                _refuse(
                    "catalog_authoring.invalid_patch",
                    f"JSON pointer {value!r} contains an invalid escape",
                )
            decoded.append("~" if raw[index + 1] == "0" else "/")
            index += 2
        token = "".join(decoded)
        if (
            not token
            or token in _FORBIDDEN_POINTER_TOKENS
            or any(ord(character) < 32 for character in token)
        ):
            _refuse(
                "catalog_authoring.invalid_patch",
                f"JSON pointer {value!r} contains a forbidden token",
            )
        tokens.append(token)
    return tuple(tokens)


def _array_index(token: str, *, length: int, allow_end: bool) -> int:
    if not _ARRAY_INDEX.fullmatch(token):
        _refuse(
            "catalog_authoring.invalid_patch",
            f"Array pointer token {token!r} is not a canonical non-negative index",
        )
    index = int(token)
    maximum = length if allow_end else length - 1
    if index > maximum:
        _refuse(
            "catalog_authoring.invalid_patch",
            f"Array pointer index {index} is outside the current draft",
        )
    return index


def _apply_command(document: JsonDocument, command: CatalogDraftPatchCommand) -> None:
    tokens = _decode_pointer(command.pointer)
    current: Any = document
    for token in tokens[:-1]:
        if isinstance(current, dict):
            if token not in current:
                _refuse(
                    "catalog_authoring.invalid_patch",
                    f"JSON pointer parent {command.pointer!r} does not exist",
                )
            current = current[token]
        elif isinstance(current, list):
            current = current[_array_index(token, length=len(current), allow_end=False)]
        else:
            _refuse(
                "catalog_authoring.invalid_patch",
                f"JSON pointer parent {command.pointer!r} is not a container",
            )

    final = tokens[-1]
    if isinstance(current, dict):
        exists = final in current
        if command.operation == "add":
            if exists:
                _refuse(
                    "catalog_authoring.invalid_patch",
                    f"Add target {command.pointer!r} already exists",
                )
            current[final] = copy.deepcopy(command.value)
        elif command.operation == "replace":
            if not exists:
                _refuse(
                    "catalog_authoring.invalid_patch",
                    f"Replace target {command.pointer!r} does not exist",
                )
            current[final] = copy.deepcopy(command.value)
        else:
            if not exists:
                _refuse(
                    "catalog_authoring.invalid_patch",
                    f"Remove target {command.pointer!r} does not exist",
                )
            del current[final]
        return

    if not isinstance(current, list):
        _refuse(
            "catalog_authoring.invalid_patch",
            f"JSON pointer target {command.pointer!r} is not inside a container",
        )
    if command.operation == "add":
        if final == "-":
            current.append(copy.deepcopy(command.value))
            return
        index = _array_index(final, length=len(current), allow_end=True)
        current.insert(index, copy.deepcopy(command.value))
        return
    if final == "-":
        _refuse(
            "catalog_authoring.invalid_patch",
            "The '-' array token is accepted only for add commands",
        )
    index = _array_index(final, length=len(current), allow_end=False)
    if command.operation == "replace":
        current[index] = copy.deepcopy(command.value)
    else:
        current.pop(index)


def _runtime_features(
    family: CatalogComponentFamily,
    canonical: CanonicalConfigurationDocument,
) -> tuple[tuple[UnsupportedFeature, str], ...]:
    support = RuntimeSupport.earth_luna()
    model = catalog_family_spec(family).validate_document(canonical.canonical_json)
    findings: list[tuple[UnsupportedFeature, str]] = []
    if family == "orbits":
        feature = support.check_propagator(cast(Any, model).propagator)
        if feature is not None:
            findings.append((feature, "/orbit/propagator"))
    elif family == "payloads":
        feature = support.check_payloads(True)
        if feature is not None:
            findings.append((feature, "/payload"))
    elif family == "nodes":
        feature = support.check_payloads(bool(cast(Any, model).payloads))
        if feature is not None:
            findings.append((feature, "/node/payloads"))
    return tuple(findings)


def _structural_issues(
    family: CatalogComponentFamily,
    target_ref: CatalogRef,
    error: BaseException,
) -> tuple[CatalogDraftIssue, ...]:
    wrapper = _wrapper(family)
    if isinstance(error, ValidationError):
        return tuple(
            CatalogDraftIssue(
                code=f"catalog_draft.structural.{item['type']}",
                stage="structural",
                message=item["msg"],
                pointer=_pointer((wrapper, *item["loc"])),
                blocks=("save", "deploy"),
            )
            for item in error.errors(include_url=False)
        )
    return (
        CatalogDraftIssue(
            code="catalog_draft.structural.invalid_document",
            stage="structural",
            message=f"Catalog component {target_ref} is invalid: {error}",
            pointer=f"/{wrapper}",
            blocks=("save", "deploy"),
        ),
    )


def _analyze(
    family: CatalogComponentFamily,
    target_ref: CatalogRef,
    document: JsonDocument,
) -> tuple[CanonicalConfigurationDocument | None, tuple[CatalogDraftIssue, ...]]:
    try:
        canonical = canonicalize_persisted_configuration(target_ref, document)
    except (ValidationError, TypeError, ValueError) as error:
        return None, _structural_issues(family, target_ref, error)
    runtime_issues = tuple(
        CatalogDraftIssue(
            code=f"catalog_draft.runtime_support.{feature.category.value}.{feature.value}",
            stage="runtime_support",
            message=feature.message,
            pointer=pointer,
            blocks=("deploy",),
        )
        for feature, pointer in _runtime_features(family, canonical)
    )
    return canonical, runtime_issues


@dataclass(frozen=True, slots=True)
class _DraftCatalogReadView(CatalogReadView):
    snapshot: CatalogReadSnapshot
    target_ref: CatalogRef
    target: CanonicalConfigurationDocument

    def read(self, ref: CatalogRef) -> CatalogReadDocument:
        if ref == self.target_ref:
            return CatalogReadDocument(
                family=self.target.family,
                preserved_path=preserved_catalog_path(ref),
                yaml_bytes=self.target.yaml_bytes,
            )
        return self.snapshot.read(ref)


def _reference_issues(
    snapshot: CatalogReadSnapshot,
    target_ref: CatalogRef,
    canonical: CanonicalConfigurationDocument,
) -> tuple[CatalogDraftIssue, ...]:
    try:
        CatalogClosureCollector.collect_references(
            (target_ref,),
            _DraftCatalogReadView(snapshot, target_ref, canonical),
        )
    except CatalogClosureError as error:
        return (
            CatalogDraftIssue(
                code=f"catalog_draft.reference.{error.code.value}",
                stage="reference",
                message=error.evidence.message,
                pointer=f"/{_wrapper(cast(CatalogComponentFamily, canonical.family))}",
                blocks=("save", "deploy"),
            ),
        )
    return ()


def _envelope(
    *,
    draft_revision: int,
    family: CatalogComponentFamily,
    target_ref: CatalogRef,
    source_ref: CatalogRef | None,
    expected_source_revision: str | None,
    expected_target_revision: str | None,
    document: JsonDocument,
) -> CatalogComponentDraftEnvelope:
    _, issues = _analyze(family, target_ref, document)
    return CatalogComponentDraftEnvelope(
        draft_revision=draft_revision,
        family=family,
        target_ref=target_ref,
        source_ref=source_ref,
        expected_source_revision=expected_source_revision,
        expected_target_revision=expected_target_revision,
        document=document,
        issues=issues,
    )


def _snapshot(context: CatalogContext) -> CatalogReadSnapshot:
    try:
        return context.repository.snapshot(context.scope)
    except (CatalogRepositoryError, OSError) as error:
        _refuse(
            "catalog_authoring.persistence_failed",
            f"Catalog snapshot is unavailable: {error}",
            cause=error,
        )


def _get(snapshot: CatalogReadSnapshot, ref: CatalogRef) -> CatalogDocument:
    try:
        return snapshot.get(ref)
    except CatalogNotFoundError as error:
        _refuse(
            "catalog_authoring.not_found",
            f"Catalog document does not exist: {ref}",
            ref=ref,
            cause=error,
        )
    except (CatalogRepositoryError, OSError) as error:
        _refuse(
            "catalog_authoring.persistence_failed",
            f"Catalog document {ref} could not be read: {error}",
            ref=ref,
            cause=error,
        )


def _optional_revision(snapshot: CatalogReadSnapshot, ref: CatalogRef) -> str | None:
    try:
        return str(snapshot.get(ref).revision)
    except CatalogNotFoundError:
        return None
    except (CatalogRepositoryError, OSError) as error:
        _refuse(
            "catalog_authoring.persistence_failed",
            f"Catalog document {ref} could not be read: {error}",
            ref=ref,
            cause=error,
        )


def _canonical_source(document: CatalogDocument) -> JsonDocument:
    try:
        raw = load_configuration_yaml(document.content)
        if not isinstance(raw, dict):
            raise TypeError("catalog document root must be a mapping")
        return canonicalize_persisted_configuration(
            document.ref,
            cast(JsonDocument, raw),
        ).canonical_json
    except (UnicodeError, yaml.YAMLError, ValidationError, TypeError, ValueError) as error:
        _refuse(
            "catalog_authoring.invalid_document",
            f"Stored catalog document {document.ref} is invalid: {error}",
            ref=document.ref,
            current_revision=str(document.revision),
            cause=error,
        )


class BuilderCatalogDraftService:
    """Lossless component drafts over one opaque server-selected catalog scope."""

    def __init__(self, context: CatalogContext) -> None:
        if not isinstance(context, CatalogContext):
            raise TypeError("context must be a CatalogContext")
        self._context = context
        self._authoring = BuilderCatalogAuthoringService(context)

    def _assert_draft_revision(
        self,
        draft: CatalogComponentDraftEnvelope,
        expected: int,
    ) -> None:
        if expected != draft.draft_revision:
            _refuse(
                "catalog_authoring.stale_revision",
                "Component draft changed before the requested operation",
                ref=draft.target_ref,
                expected_revision=str(expected),
                current_revision=str(draft.draft_revision),
            )

    def _assert_catalog_revisions(
        self,
        draft: CatalogComponentDraftEnvelope,
        snapshot: CatalogReadSnapshot,
    ) -> None:
        if draft.source_ref is not None:
            current_source = _optional_revision(snapshot, draft.source_ref)
            if current_source != draft.expected_source_revision:
                _refuse(
                    "catalog_authoring.stale_revision",
                    f"Component draft source {draft.source_ref} changed after it was opened",
                    ref=draft.source_ref,
                    expected_revision=draft.expected_source_revision,
                    current_revision=current_source,
                )
        current_target = _optional_revision(snapshot, draft.target_ref)
        if current_target != draft.expected_target_revision:
            _refuse(
                "catalog_authoring.stale_revision",
                f"Component draft target {draft.target_ref} changed after it was opened",
                ref=draft.target_ref,
                expected_revision=draft.expected_target_revision,
                current_revision=current_target,
            )

    def new(self, request: CatalogDraftNewRequest) -> CatalogComponentDraftEnvelope:
        if not isinstance(request, CatalogDraftNewRequest):
            raise TypeError("request must be a CatalogDraftNewRequest")
        target_ref = CatalogRef(f"user:{request.family}/{request.object_id}.yaml")
        snapshot = _snapshot(self._context)
        if _optional_revision(snapshot, target_ref) is not None:
            _refuse(
                "catalog_authoring.conflict",
                f"Catalog component already exists: {target_ref}",
                ref=target_ref,
            )
        wrapper = _wrapper(request.family)
        return _envelope(
            draft_revision=0,
            family=request.family,
            target_ref=target_ref,
            source_ref=None,
            expected_source_revision=None,
            expected_target_revision=None,
            document={wrapper: {"id": request.object_id}},
        )

    def open(self, request: CatalogDraftOpenRequest) -> CatalogComponentDraftEnvelope:
        if not isinstance(request, CatalogDraftOpenRequest):
            raise TypeError("request must be a CatalogDraftOpenRequest")
        snapshot = _snapshot(self._context)
        source = _get(snapshot, request.source_ref)
        family = cast(CatalogComponentFamily, source.family)
        target_ref = request.target_ref
        if target_ref is None:
            target_ref = (
                request.source_ref
                if request.source_ref.namespace == "user"
                else CatalogRef(f"user:{request.source_ref.relative_path.as_posix()}")
            )
            if (
                request.source_ref.namespace == "nodalarc"
                and _optional_revision(snapshot, target_ref) is not None
            ):
                _refuse(
                    "catalog_authoring.conflict",
                    f"Default customization target already exists: {target_ref}; "
                    "choose an explicit new user: reference",
                    ref=target_ref,
                )
        document = copy.deepcopy(_canonical_source(source))
        wrapper = _wrapper(family)
        document[wrapper]["id"] = target_ref.relative_path.stem
        return _envelope(
            draft_revision=0,
            family=family,
            target_ref=target_ref,
            source_ref=request.source_ref,
            expected_source_revision=str(source.revision),
            expected_target_revision=_optional_revision(snapshot, target_ref),
            document=document,
        )

    def patch(self, request: CatalogDraftPatchRequest) -> CatalogComponentDraftEnvelope:
        if not isinstance(request, CatalogDraftPatchRequest):
            raise TypeError("request must be a CatalogDraftPatchRequest")
        self._assert_draft_revision(request.draft, request.expected_draft_revision)
        self._assert_catalog_revisions(request.draft, _snapshot(self._context))
        wrapper = _wrapper(request.draft.family)
        document = copy.deepcopy(request.draft.document)
        for command in request.commands:
            tokens = _decode_pointer(command.pointer)
            if tokens[0] != wrapper or len(tokens) == 1 or tokens[1] == "id":
                _refuse(
                    "catalog_authoring.invalid_patch",
                    "Component patches cannot change the family wrapper or object identity",
                    ref=request.draft.target_ref,
                )
            _apply_command(document, command)
        return _envelope(
            draft_revision=request.draft.draft_revision + 1,
            family=request.draft.family,
            target_ref=request.draft.target_ref,
            source_ref=request.draft.source_ref,
            expected_source_revision=request.draft.expected_source_revision,
            expected_target_revision=request.draft.expected_target_revision,
            document=document,
        )

    def add_site_node(
        self,
        request: CatalogDraftAddSiteNodeRequest,
    ) -> CatalogComponentDraftEnvelope:
        """Add one SiteNode whose persisted shape is derived by the backend."""

        if not isinstance(request, CatalogDraftAddSiteNodeRequest):
            raise TypeError("request must be a CatalogDraftAddSiteNodeRequest")
        self._assert_draft_revision(request.draft, request.expected_draft_revision)
        snapshot = _snapshot(self._context)
        self._assert_catalog_revisions(request.draft, snapshot)

        node_document = _get(snapshot, request.node_ref)
        try:
            node_model = catalog_family_spec("nodes").validate_document(
                _canonical_source(node_document)
            )
        except (ValidationError, TypeError, ValueError) as error:
            _refuse(
                "catalog_authoring.invalid_graph",
                f"Node reference {request.node_ref} is invalid: {error}",
                ref=request.draft.target_ref,
                cause=error,
            )
        if not isinstance(node_model, Node):
            _refuse(
                "catalog_authoring.invalid_graph",
                f"Node reference {request.node_ref} does not resolve to a node component",
                ref=request.draft.target_ref,
            )

        document = copy.deepcopy(request.draft.document)
        site = document[_wrapper("sites")]
        if not isinstance(site, dict):
            _refuse(
                "catalog_authoring.invalid_document",
                "Site component root must be an object before a node can be added",
                ref=request.draft.target_ref,
            )
        nodes = site.get("nodes", [])
        if not isinstance(nodes, list):
            _refuse(
                "catalog_authoring.invalid_document",
                "Site nodes must be an array before a node can be added",
                ref=request.draft.target_ref,
            )
        node_ids = {
            node.get("id")
            for node in nodes
            if isinstance(node, dict) and isinstance(node.get("id"), str)
        }
        if request.node_id in node_ids:
            _refuse(
                "catalog_authoring.conflict",
                f"Site node id already exists: {request.node_id}",
                ref=request.draft.target_ref,
            )

        terminals: JsonDocument = {}
        for mount in node_model.terminals:
            installation: JsonDocument = {"installed_count": mount.count}
            if mount.role == "access":
                installation["capabilities"] = {"boresight": {"mode": "local_vertical"}}
            terminals[mount.id] = installation
        nodes.append(
            {
                "id": request.node_id,
                "model": str(request.node_ref),
                "payloads": {},
                "terminals": terminals,
                "interfaces": {
                    "lo0": {"ipv4": ""},
                    "terr0": {"ipv4": ""},
                },
            }
        )
        site["nodes"] = nodes
        return _envelope(
            draft_revision=request.draft.draft_revision + 1,
            family=request.draft.family,
            target_ref=request.draft.target_ref,
            source_ref=request.draft.source_ref,
            expected_source_revision=request.draft.expected_source_revision,
            expected_target_revision=request.draft.expected_target_revision,
            document=document,
        )

    def add_node_terminal_mount(
        self,
        request: CatalogDraftAddNodeTerminalMountRequest,
    ) -> CatalogComponentDraftEnvelope:
        """Add one complete node mount from explicit terminal and role intent."""

        if not isinstance(request, CatalogDraftAddNodeTerminalMountRequest):
            raise TypeError("request must be a CatalogDraftAddNodeTerminalMountRequest")
        self._assert_draft_revision(request.draft, request.expected_draft_revision)
        snapshot = _snapshot(self._context)
        self._assert_catalog_revisions(request.draft, snapshot)

        terminal_document = _get(snapshot, request.terminal_ref)
        try:
            terminal_model = catalog_family_spec("terminals").validate_document(
                _canonical_source(terminal_document)
            )
        except (ValidationError, TypeError, ValueError) as error:
            _refuse(
                "catalog_authoring.invalid_graph",
                f"Terminal reference {request.terminal_ref} is invalid: {error}",
                ref=request.draft.target_ref,
                cause=error,
            )
        if not isinstance(terminal_model, Terminal):
            _refuse(
                "catalog_authoring.invalid_graph",
                f"Terminal reference {request.terminal_ref} does not resolve to a terminal component",
                ref=request.draft.target_ref,
            )

        document = copy.deepcopy(request.draft.document)
        node = document[_wrapper("nodes")]
        if not isinstance(node, dict):
            _refuse(
                "catalog_authoring.invalid_document",
                "Node component root must be an object before a terminal mount can be added",
                ref=request.draft.target_ref,
            )
        mounts = node.get("terminals", [])
        if not isinstance(mounts, list):
            _refuse(
                "catalog_authoring.invalid_document",
                "Node terminals must be an array before a terminal mount can be added",
                ref=request.draft.target_ref,
            )
        mount_ids = {
            mount.get("id")
            for mount in mounts
            if isinstance(mount, dict) and isinstance(mount.get("id"), str)
        }
        mount_index = 0
        mount_id = f"{request.role}_{mount_index}"
        while mount_id in mount_ids:
            mount_index += 1
            mount_id = f"{request.role}_{mount_index}"
        mounts.append(
            {
                "id": mount_id,
                "role": request.role,
                "terminal": str(request.terminal_ref),
                "count": DEFAULT_TERMINAL_MOUNT_COUNT,
                **({"boresight": {"mode": "nadir"}} if request.role == "access" else {}),
            }
        )
        node["terminals"] = mounts
        return _envelope(
            draft_revision=request.draft.draft_revision + 1,
            family=request.draft.family,
            target_ref=request.draft.target_ref,
            source_ref=request.draft.source_ref,
            expected_source_revision=request.draft.expected_source_revision,
            expected_target_revision=request.draft.expected_target_revision,
            document=document,
        )

    def add_node_ethernet_port(
        self,
        request: CatalogDraftAddNodeEthernetPortRequest,
    ) -> CatalogComponentDraftEnvelope:
        """Add one uniquely identified Ethernet port to a node draft."""

        if not isinstance(request, CatalogDraftAddNodeEthernetPortRequest):
            raise TypeError("request must be a CatalogDraftAddNodeEthernetPortRequest")
        self._assert_draft_revision(request.draft, request.expected_draft_revision)
        snapshot = _snapshot(self._context)
        self._assert_catalog_revisions(request.draft, snapshot)

        document = copy.deepcopy(request.draft.document)
        node = document[_wrapper("nodes")]
        if not isinstance(node, dict):
            _refuse(
                "catalog_authoring.invalid_document",
                "Node component root must be an object before an Ethernet port can be added",
                ref=request.draft.target_ref,
            )
        ethernet = node.get("ethernet", [])
        if not isinstance(ethernet, list):
            _refuse(
                "catalog_authoring.invalid_document",
                "Node ethernet must be an array before an Ethernet port can be added",
                ref=request.draft.target_ref,
            )
        port_ids = {
            port.get("id")
            for port in ethernet
            if isinstance(port, dict) and isinstance(port.get("id"), str)
        }
        port_index = 0
        port_id = f"terr{port_index}"
        while port_id in port_ids:
            port_index += 1
            port_id = f"terr{port_index}"
        ethernet.append({"id": port_id})
        node["ethernet"] = ethernet
        return _envelope(
            draft_revision=request.draft.draft_revision + 1,
            family=request.draft.family,
            target_ref=request.draft.target_ref,
            source_ref=request.draft.source_ref,
            expected_source_revision=request.draft.expected_source_revision,
            expected_target_revision=request.draft.expected_target_revision,
            document=document,
        )

    def replace_object(
        self,
        request: CatalogDraftReplaceObjectRequest,
    ) -> CatalogComponentDraftEnvelope:
        """Parse advanced JSON and replace only the fixed component wrapper."""

        if not isinstance(request, CatalogDraftReplaceObjectRequest):
            raise TypeError("request must be a CatalogDraftReplaceObjectRequest")
        self._assert_draft_revision(request.draft, request.expected_draft_revision)
        self._assert_catalog_revisions(request.draft, _snapshot(self._context))
        try:
            parsed = json.loads(
                request.raw_object_json,
                parse_constant=_reject_json_constant,
                object_pairs_hook=_unique_json_object,
            )
        except (json.JSONDecodeError, RecursionError, ValueError) as error:
            _refuse(
                "catalog_authoring.invalid_document",
                f"Advanced component JSON is invalid: {error}",
                ref=request.draft.target_ref,
                cause=error,
            )
        if not isinstance(parsed, dict):
            _refuse(
                "catalog_authoring.invalid_document",
                "Advanced component JSON must contain one object",
                ref=request.draft.target_ref,
            )
        wrapper = _wrapper(request.draft.family)
        expected_id = request.draft.target_ref.relative_path.stem
        if parsed.get("id") != expected_id:
            _refuse(
                "catalog_authoring.invalid_document",
                "Component identity is fixed by its user: reference",
                ref=request.draft.target_ref,
            )
        document = copy.deepcopy(request.draft.document)
        document[wrapper] = parsed
        return _envelope(
            draft_revision=request.draft.draft_revision + 1,
            family=request.draft.family,
            target_ref=request.draft.target_ref,
            source_ref=request.draft.source_ref,
            expected_source_revision=request.draft.expected_source_revision,
            expected_target_revision=request.draft.expected_target_revision,
            document=document,
        )

    def _compile_at_snapshot(
        self,
        request: CatalogDraftCompileRequest,
        snapshot: CatalogReadSnapshot,
    ) -> CatalogDraftCompileResult:
        self._assert_draft_revision(request.draft, request.expected_draft_revision)
        self._assert_catalog_revisions(request.draft, snapshot)
        canonical, issues = _analyze(
            request.draft.family,
            request.draft.target_ref,
            request.draft.document,
        )
        if canonical is not None:
            issues = (
                *issues,
                *_reference_issues(snapshot, request.draft.target_ref, canonical),
            )
        draft = CatalogComponentDraftEnvelope(
            **{
                **request.draft.model_dump(mode="python"),
                "issues": issues,
            }
        )
        return CatalogDraftCompileResult(
            draft=draft,
            save_allowed=canonical is not None
            and not any("save" in issue.blocks for issue in issues),
            runtime_supported=not any("deploy" in issue.blocks for issue in issues),
            canonical_yaml=(canonical.yaml_bytes.decode("utf-8") if canonical else None),
            canonical_json=(canonical.canonical_json if canonical else None),
            content_digest=(canonical.document_digest if canonical else None),
            issues=issues,
        )

    def compile(self, request: CatalogDraftCompileRequest) -> CatalogDraftCompileResult:
        if not isinstance(request, CatalogDraftCompileRequest):
            raise TypeError("request must be a CatalogDraftCompileRequest")
        return self._compile_at_snapshot(request, _snapshot(self._context))

    def save(self, request: CatalogDraftSaveRequest) -> CatalogDraftSaveResult:
        if not isinstance(request, CatalogDraftSaveRequest):
            raise TypeError("request must be a CatalogDraftSaveRequest")
        snapshot = _snapshot(self._context)
        compiled = self._compile_at_snapshot(
            CatalogDraftCompileRequest(
                draft=request.draft,
                expected_draft_revision=request.expected_draft_revision,
            ),
            snapshot,
        )
        if not compiled.save_allowed or compiled.canonical_json is None:
            first = compiled.issues[0]
            _refuse(
                (
                    "catalog_authoring.invalid_graph"
                    if first.stage == "reference"
                    else "catalog_authoring.invalid_document"
                ),
                f"Catalog component cannot be saved: {first.message}",
                ref=request.draft.target_ref,
            )
        result = self._authoring.save_component_at_snapshot(
            CatalogDocumentWriteRequest(
                ref=request.draft.target_ref,
                document=compiled.canonical_json,
                expected_revision=request.draft.expected_target_revision,
            ),
            snapshot,
        )
        revision = result.document.revision
        saved_draft = _envelope(
            draft_revision=request.draft.draft_revision,
            family=request.draft.family,
            target_ref=request.draft.target_ref,
            source_ref=request.draft.target_ref,
            expected_source_revision=revision,
            expected_target_revision=revision,
            document=result.document.canonical_json,
        )
        saved_compile = CatalogDraftCompileResult(
            draft=saved_draft,
            save_allowed=True,
            runtime_supported=compiled.runtime_supported,
            canonical_yaml=result.document.canonical_yaml,
            canonical_json=result.document.canonical_json,
            content_digest=result.document.content_digest,
            issues=saved_draft.issues,
        )
        return CatalogDraftSaveResult(
            draft=saved_draft,
            result=result,
            compile_result=saved_compile,
        )
