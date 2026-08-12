"""Storage-neutral application service for Builder catalog authoring."""

from __future__ import annotations

import base64
import copy
import hashlib
import hmac
import json
import secrets
from collections import deque
from collections.abc import Mapping
from contextlib import suppress
from dataclasses import dataclass
from typing import Any, NoReturn, cast

import yaml
from nodalarc.catalog_closure import (
    CatalogClosureCollector,
    CatalogClosureError,
    CatalogReadDocument,
    catalog_document_references,
    preserved_catalog_path,
)
from nodalarc.catalog_refs import CatalogFamily, CatalogRef, SessionRef
from nodalarc.catalog_registry import (
    CATALOG_FAMILY_REGISTRY,
    CATALOG_WRAPPER_TO_FAMILY,
    catalog_family_spec,
    validate_catalog_document,
    validate_referenced_configuration_document,
)
from nodalarc.catalog_repository import (
    CatalogConflictError,
    CatalogDocument,
    CatalogNotFoundError,
    CatalogReadOnlyError,
    CatalogReadSnapshot,
    CatalogRepositoryError,
    CatalogValidationError,
)
from nodalarc.catalog_upload import DEFAULT_CATALOG_UPLOAD_LIMITS
from nodalarc.configuration_yaml import load_configuration_yaml
from nodalarc.content_identity import canonical_json_bytes, sha256_digest
from nodalarc.models.builder_api import BuilderCatalogDocument, JsonDocument
from nodalarc.models.builder_catalog_api import (
    BuilderCatalogBootstrap,
    BuilderCatalogCapabilities,
    BuilderVisualAuthoringFacts,
    BuilderVisualBoundaryAdapterMetadata,
    BuilderVisualForwardingClassMetadata,
    BuilderVisualLinkMediumMetadata,
    BuilderVisualMountRoleMetadata,
    BuilderVisualOrbitPropagatorMetadata,
    BuilderVisualOrbitShapeMetadata,
    BuilderVisualPhasingModeMetadata,
    BuilderVisualRoutingProtocolMetadata,
    BuilderVisualSchedulingPresetMetadata,
    BuilderVisualTopologyModeMetadata,
    CatalogDeleteRequest,
    CatalogDeleteResult,
    CatalogDependencyImpact,
    CatalogDependent,
    CatalogDependentsRequest,
    CatalogDocumentSummary,
    CatalogDocumentWriteRequest,
    CatalogFamilyMetadata,
    CatalogForkRequest,
    CatalogForkResult,
    CatalogGetRequest,
    CatalogListPage,
    CatalogListRequest,
    CatalogMutationResult,
    CatalogOperationRefusal,
    CatalogSessionYamlExport,
    CatalogSessionYamlExportRequest,
    CatalogSessionYamlImportRequest,
    CatalogSessionYamlImportResult,
    CatalogYamlFile,
    CatalogYamlImportCollision,
    CatalogYamlImportFile,
    CatalogYamlImportWrite,
)
from nodalarc.models.builder_visual_api import (
    BuilderVisualGroundBoresight,
    BuilderVisualNode,
    BuilderVisualSpaceBoresight,
)
from nodalarc.runtime_support import RuntimeSupport
from pydantic import BaseModel, ValidationError

from .builder_compiler import canonicalize_persisted_configuration
from .builder_visual_defaults import (
    BOUNDARY_ADAPTER_LABELS,
    DEFAULT_BODY_REF,
    DEFAULT_COMPONENT_IDS,
    DEFAULT_MOUNT_ROLE,
    DEFAULT_PHASING_MODE,
    DEFAULT_SCHEDULING_PRESET,
    FORWARDING_CLASS_LABELS,
    LINK_MEDIUM_LABELS,
    MOUNT_ROLE_LABELS,
    ORBIT_PROPAGATOR_LABELS,
    ORBIT_SHAPE_LABELS,
    PHASING_MODE_LABELS,
    ROUTING_PROTOCOL_LABELS,
    SCHEDULING_PRESET_LABELS,
    SINGLE_PLANE_PHASING_MODE,
    TOPOLOGY_MODE_LABELS,
)
from .catalog_context import CatalogContext

_DEFAULT_GRAMMAR_HREF = "/docs/ops/configuration-grammar.md"
_PAGE_TOKEN_VERSION = 1
_PAGE_TOKEN_PREFIX = "nacp1"
_IMPORT_PROPOSAL_TOKEN_PREFIX = "naip1"
_IMPACT_SCHEMA = "nodalarc.catalog-dependency-impact.v1"
_PROCESS_PAGE_TOKEN_SECRET = secrets.token_bytes(32)


class CatalogAuthoringError(RuntimeError):
    """Typed application refusal suitable for a transport-layer error mapper."""

    def __init__(self, refusal: CatalogOperationRefusal) -> None:
        super().__init__(refusal.message)
        self.refusal = refusal

    @property
    def code(self) -> str:
        return self.refusal.code


def _refuse(
    code: str,
    message: str,
    *,
    ref: str | CatalogRef | None = None,
    expected_revision: str | None = None,
    current_revision: str | None = None,
    impact: CatalogDependencyImpact | None = None,
    collisions: tuple[CatalogYamlImportCollision, ...] = (),
    cause: BaseException | None = None,
) -> NoReturn:
    raise CatalogAuthoringError(
        CatalogOperationRefusal(
            code=code,
            message=message,
            ref=str(ref) if ref is not None else None,
            expected_revision=expected_revision,
            current_revision=current_revision,
            impact=impact,
            collisions=collisions,
            cause_type=type(cause).__name__ if cause is not None else None,
        )
    ) from cause


def _validated_exact_document(ref: CatalogRef, content: bytes) -> BaseModel:
    try:
        data = load_configuration_yaml(content)
        _wrapper, model = validate_referenced_configuration_document(ref, data)
    except (UnicodeError, yaml.YAMLError, ValidationError, TypeError, ValueError) as error:
        _refuse(
            "catalog_authoring.invalid_document",
            f"Catalog document {ref} is invalid: {error}",
            ref=ref,
            cause=error,
        )
    return model


def _canonical_import_bytes(ref: CatalogRef, content: bytes) -> bytes:
    """Normalize user-owned import bytes through the persisted grammar authority."""

    _validated_exact_document(ref, content)
    if ref.namespace == "nodalarc":
        return content
    try:
        document = cast(JsonDocument, load_configuration_yaml(content))
        return canonicalize_persisted_configuration(ref, document).yaml_bytes
    except (UnicodeError, yaml.YAMLError, ValidationError, TypeError, ValueError) as error:
        _refuse(
            "catalog_authoring.invalid_document",
            f"Catalog document {ref} cannot be canonicalized: {error}",
            ref=ref,
            cause=error,
        )


@dataclass(frozen=True, slots=True)
class _UnplacedCatalogYaml:
    position: int
    family: CatalogFamily
    identity: str
    content: bytes
    hinted_ref: CatalogRef | None


@dataclass(frozen=True, slots=True)
class _ParsedYamlImport:
    root_ref: SessionRef
    root_content: bytes
    root_ref_hinted: bool
    components: Mapping[tuple[CatalogFamily, str], tuple[_UnplacedCatalogYaml, ...]]


def _hinted_catalog_ref(
    yaml_file: CatalogYamlImportFile,
    *,
    family: CatalogFamily,
    identity: str,
) -> CatalogRef | None:
    hint = yaml_file.logical_path_hint
    if hint is None:
        return None
    parts = hint.split("/")
    if len(parts) < 4 or parts[0] != "catalog":
        _refuse(
            "catalog_authoring.import_incomplete",
            f"Catalog YAML path hint must have the form catalog/<namespace>/<family>/...: {hint}",
        )
    try:
        ref = CatalogRef(f"{parts[1]}:{'/'.join(parts[2:])}")
    except ValueError as error:
        _refuse(
            "catalog_authoring.import_incomplete",
            f"Catalog YAML path hint is not a valid catalog reference: {hint}",
            cause=error,
        )
    if ref.family != family or ref.relative_path.stem != identity:
        _refuse(
            "catalog_authoring.import_incomplete",
            f"Catalog YAML path hint {hint} does not match document identity {family}/{identity}",
            ref=ref,
        )
    return ref


def _hinted_session_ref(
    yaml_file: CatalogYamlImportFile,
    *,
    session_name: str,
) -> SessionRef | None:
    hint = yaml_file.logical_path_hint
    if hint is None:
        return None
    parts = hint.split("/")
    if len(parts) < 4 or parts[0] != "catalog":
        _refuse(
            "catalog_authoring.import_incomplete",
            f"Session YAML path hint must have the form catalog/<namespace>/sessions/...: {hint}",
        )
    try:
        source_ref = SessionRef(f"{parts[1]}:{'/'.join(parts[2:])}")
    except ValueError as error:
        _refuse(
            "catalog_authoring.import_incomplete",
            f"Session YAML path hint is not a valid session reference: {hint}",
            cause=error,
        )
    if source_ref.relative_path.stem != session_name:
        _refuse(
            "catalog_authoring.import_incomplete",
            f"Session YAML path hint {hint} does not match session.name {session_name!r}",
            ref=source_ref,
        )
    if source_ref.namespace == "nodalarc":
        return SessionRef(f"user:{source_ref.relative_path.as_posix()}")
    return source_ref


def _parse_yaml_import(
    yaml_files: tuple[CatalogYamlImportFile, ...],
) -> _ParsedYamlImport:
    roots: list[tuple[SessionRef, bytes, bool]] = []
    components: dict[tuple[CatalogFamily, str], list[_UnplacedCatalogYaml]] = {}
    hinted_refs: set[CatalogRef] = set()
    session_spec = catalog_family_spec("sessions")

    for position, yaml_file in enumerate(yaml_files, start=1):
        content = yaml_file.yaml_text.encode("utf-8")
        try:
            data = load_configuration_yaml(content)
        except (UnicodeError, yaml.YAMLError) as error:
            _refuse(
                "catalog_authoring.invalid_document",
                f"Imported YAML file {position} cannot be parsed: {error}",
                cause=error,
            )

        try:
            session_model = session_spec.validate_document(data)
        except (ValidationError, TypeError, ValueError) as session_error:
            try:
                wrapper, component_model = validate_catalog_document(data)
                family = CATALOG_WRAPPER_TO_FAMILY[wrapper]
                identity = str(component_model.id)
            except (ValidationError, TypeError, ValueError) as component_error:
                _refuse(
                    "catalog_authoring.invalid_document",
                    f"Imported YAML file {position} is neither a valid session nor a valid "
                    f"catalog component: session={session_error}; component={component_error}",
                    cause=component_error,
                )
            key = (family, identity)
            hinted_ref = _hinted_catalog_ref(
                yaml_file,
                family=family,
                identity=identity,
            )
            if hinted_ref is not None and hinted_ref in hinted_refs:
                _refuse(
                    "catalog_authoring.import_incomplete",
                    f"Imported YAML files contain duplicate path hint {yaml_file.logical_path_hint}",
                    ref=hinted_ref,
                )
            if hinted_ref is not None:
                hinted_refs.add(hinted_ref)
            components.setdefault(key, []).append(
                _UnplacedCatalogYaml(
                    position=position,
                    family=family,
                    identity=identity,
                    content=content,
                    hinted_ref=hinted_ref,
                )
            )
            continue

        hinted_ref = _hinted_session_ref(
            yaml_file,
            session_name=session_model.session.name,
        )
        root_ref = hinted_ref or SessionRef(f"user:sessions/{session_model.session.name}.yaml")
        _validated_exact_document(root_ref, content)
        roots.append((root_ref, content, hinted_ref is not None))

    if len(roots) != 1:
        _refuse(
            "catalog_authoring.import_incomplete",
            f"YAML import requires exactly one session document; found {len(roots)}",
        )
    root_ref, root_content, root_ref_hinted = roots[0]
    return _ParsedYamlImport(
        root_ref=root_ref,
        root_content=root_content,
        root_ref_hinted=root_ref_hinted,
        components={key: tuple(value) for key, value in components.items()},
    )


def _derive_import_session_ref(
    snapshot: CatalogReadSnapshot,
    parsed_ref: SessionRef,
) -> SessionRef:
    try:
        matches = tuple(
            entry.ref
            for entry in snapshot.list(namespace="user", family="sessions")
            if entry.ref.relative_path.stem == parsed_ref.relative_path.stem
        )
    except (CatalogRepositoryError, OSError) as error:
        _refuse(
            "catalog_authoring.persistence_failed",
            f"Session identity lookup failed: {error}",
            ref=parsed_ref,
            cause=error,
        )
    if len(matches) > 1:
        _refuse(
            "catalog_authoring.import_incomplete",
            f"Session identity {parsed_ref.relative_path.stem!r} already exists at multiple "
            f"user catalog refs: {', '.join(sorted(map(str, matches)))}",
            ref=parsed_ref,
        )
    return SessionRef(str(matches[0])) if matches else parsed_ref


def _canonical_document(document: CatalogDocument) -> BuilderCatalogDocument:
    try:
        data = cast(JsonDocument, load_configuration_yaml(document.content))
        canonical = canonicalize_persisted_configuration(document.ref, data)
    except (UnicodeError, yaml.YAMLError, ValidationError, TypeError, ValueError) as error:
        _refuse(
            "catalog_authoring.invalid_document",
            f"Stored catalog document {document.ref} cannot be canonicalized: {error}",
            ref=document.ref,
            current_revision=str(document.revision),
            cause=error,
        )
    return BuilderCatalogDocument(
        ref=document.ref,
        family=document.family,
        canonical_yaml=canonical.yaml_bytes.decode("utf-8"),
        canonical_json=canonical.canonical_json,
        content_digest=canonical.document_digest,
        revision=str(document.revision),
    )


def _presentation_metadata(document: CatalogDocument) -> tuple[str, str | None]:
    model = _validated_exact_document(document.ref, document.content)
    subject = model.session if document.family == "sessions" else model
    display_name = getattr(subject, "display_name", None)
    if not isinstance(display_name, str) or not display_name.strip():
        display_name = document.ref.relative_path.stem
    summary = getattr(subject, "description", None) or getattr(subject, "notes", None)
    if isinstance(summary, str):
        summary = " ".join(summary.split())
        if len(summary) > 512:
            summary = summary[:509].rstrip() + "..."
    else:
        summary = None
    return display_name.strip(), summary or None


def _repository_document(snapshot: CatalogReadSnapshot, ref: CatalogRef) -> CatalogDocument:
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


def _user_owned(ref: CatalogRef) -> None:
    if ref.namespace != "user":
        _refuse(
            "catalog_authoring.read_only",
            f"Shipped catalog document {ref} is read-only",
            ref=ref,
        )


@dataclass(frozen=True, slots=True)
class _GraphState:
    documents: Mapping[CatalogRef, CatalogDocument]
    dependencies: Mapping[CatalogRef, frozenset[CatalogRef]]


class _ImportPlacementError(ValueError):
    """Raised when YAML identity cannot determine one referenced catalog path."""


@dataclass(slots=True)
class _IdentityImportReadView:
    snapshot: CatalogReadSnapshot
    components: Mapping[tuple[CatalogFamily, str], tuple[_UnplacedCatalogYaml, ...]]
    assigned_refs: dict[int, CatalogRef]
    assigned_documents: dict[CatalogRef, bytes]

    def read(self, ref: CatalogRef) -> CatalogReadDocument:
        family = cast(CatalogFamily, ref.family)
        key = (family, ref.relative_path.stem)
        candidates = self.components.get(key, ())
        exact = tuple(component for component in candidates if component.hinted_ref == ref)
        unhinted = tuple(
            component
            for component in candidates
            if component.hinted_ref is None and component.position not in self.assigned_refs
        )
        if len(exact) == 1:
            component = exact[0]
        elif len(exact) > 1:
            raise _ImportPlacementError(f"Multiple imported YAML files claim {ref}")
        elif len(unhinted) == 1:
            component = unhinted[0]
        elif len(unhinted) > 1:
            raise _ImportPlacementError(
                f"Multiple imported YAML files have identity {family}/{ref.relative_path.stem}; "
                f"supply their exported catalog paths to disambiguate {ref}"
            )
        else:
            assigned = tuple(
                self.assigned_refs[component.position]
                for component in candidates
                if component.position in self.assigned_refs
            )
            if assigned:
                raise _ImportPlacementError(
                    f"Catalog YAML identity {family}/{ref.relative_path.stem} is referenced by "
                    f"both {assigned[0]} and {ref}; placement is ambiguous without exported "
                    "catalog paths"
                )
            if ref.namespace == "user":
                raise _ImportPlacementError(
                    f"Session references {ref}; expected a supplied YAML file with path hint "
                    f"{preserved_catalog_path(ref)} or an unambiguous {family}/"
                    f"{ref.relative_path.stem} document"
                )
            try:
                return self.snapshot.read(ref)
            except CatalogNotFoundError as error:
                raise FileNotFoundError(str(ref)) from error

        assigned_ref = self.assigned_refs.get(component.position)
        if assigned_ref is not None and assigned_ref != ref:
            raise _ImportPlacementError(
                f"Catalog YAML identity {family}/{component.identity} is referenced by both "
                f"{assigned_ref} and {ref}; supply its exported catalog path to disambiguate it"
            )
        _validated_exact_document(ref, component.content)
        self.assigned_refs[component.position] = ref
        self.assigned_documents[ref] = component.content
        return CatalogReadDocument(
            family=family,
            preserved_path=preserved_catalog_path(ref),
            yaml_bytes=component.content,
        )


@dataclass(frozen=True, slots=True)
class _OverlayImportReadView:
    snapshot: CatalogReadSnapshot
    documents: Mapping[CatalogRef, bytes]

    def read(self, ref: CatalogRef) -> CatalogReadDocument:
        content = self.documents.get(ref)
        if content is None:
            try:
                return self.snapshot.read(ref)
            except CatalogNotFoundError as error:
                raise FileNotFoundError(str(ref)) from error
        return CatalogReadDocument(
            family=cast(CatalogFamily, ref.family),
            preserved_path=preserved_catalog_path(ref),
            yaml_bytes=content,
        )


class BuilderCatalogAuthoringService:
    """Catalog authoring operations over one server-selected request context."""

    def __init__(
        self,
        context: CatalogContext,
        *,
        page_token_secret: bytes | None = None,
        public_grammar_href: str = _DEFAULT_GRAMMAR_HREF,
    ) -> None:
        if not isinstance(context, CatalogContext):
            raise TypeError("context must be a CatalogContext")
        secret = _PROCESS_PAGE_TOKEN_SECRET if page_token_secret is None else page_token_secret
        if not isinstance(secret, bytes) or len(secret) < 32:
            raise ValueError("page_token_secret must contain at least 32 bytes")
        scope_binding = f"{id(context.repository)}:{id(context.scope)}".encode("ascii")
        self._page_key = hmac.new(secret, scope_binding, hashlib.sha256).digest()
        self._import_proposal_key = hmac.new(
            secret,
            b"catalog-import\0" + context.scope_binding.encode("utf-8") + b"\0" + scope_binding,
            hashlib.sha256,
        ).digest()
        self._context = context
        self._public_grammar_href = public_grammar_href
        self._graph_cache: tuple[str, _GraphState] | None = None

    def _snapshot(self) -> CatalogReadSnapshot:
        try:
            return self._context.repository.snapshot(self._context.scope)
        except (CatalogRepositoryError, OSError) as error:
            _refuse(
                "catalog_authoring.persistence_failed",
                f"Catalog snapshot is unavailable: {error}",
                cause=error,
            )

    def bootstrap(self) -> BuilderCatalogBootstrap:
        """Return generated-family metadata and factual backend capabilities."""

        families = tuple(
            CatalogFamilyMetadata(
                family=family,
                wrapper=spec.wrapper,
                direct_user_write=family != "sessions",
                component_fork=family != "sessions",
                session_draft_save=family == "sessions",
                suggested_object_id=(
                    None if family == "sessions" else DEFAULT_COMPONENT_IDS[family]
                ),
            )
            for family, spec in sorted(CATALOG_FAMILY_REGISTRY.items())
        )
        runtime = RuntimeSupport.earth_luna()
        return BuilderCatalogBootstrap(
            authoring_context_binding=self._context.scope_binding,
            public_grammar_href=self._public_grammar_href,
            capabilities=BuilderCatalogCapabilities(),
            families=families,
            scheduling_presets=tuple(
                BuilderVisualSchedulingPresetMetadata(id=preset, label=label)
                for preset, label in SCHEDULING_PRESET_LABELS
            ),
            authoring=BuilderVisualAuthoringFacts(
                default_phasing_mode=DEFAULT_PHASING_MODE,
                single_plane_phasing_mode=SINGLE_PLANE_PHASING_MODE,
                default_scheduling_preset=DEFAULT_SCHEDULING_PRESET,
                default_mount_role=DEFAULT_MOUNT_ROLE,
                default_terminal_mount_count=1,
                default_body_ref=DEFAULT_BODY_REF,
                default_node=BuilderVisualNode(
                    id="my-node",
                    display_name="My node",
                    forwarding=None,
                    ethernet=(),
                    terminals=(),
                ),
                space_access_boresight=BuilderVisualSpaceBoresight(mode="nadir"),
                ground_access_boresight=BuilderVisualGroundBoresight(mode="local_vertical"),
                mount_roles=tuple(
                    BuilderVisualMountRoleMetadata(
                        id=identifier,
                        label=label,
                        description=description,
                    )
                    for identifier, label, description in MOUNT_ROLE_LABELS
                ),
                link_media=tuple(
                    BuilderVisualLinkMediumMetadata(
                        id=identifier,
                        label=label,
                        signal_seed=signal_seed,
                    )
                    for identifier, label, signal_seed in LINK_MEDIUM_LABELS
                ),
                forwarding_classes=tuple(
                    BuilderVisualForwardingClassMetadata(id=identifier, label=label)
                    for identifier, label in FORWARDING_CLASS_LABELS
                ),
                routing_protocols=tuple(
                    BuilderVisualRoutingProtocolMetadata(
                        id=identifier,
                        label=label,
                        runtime_supported=(issue := runtime.check_routing_protocol(identifier))
                        is None,
                        support_note=issue.support_note if issue is not None else None,
                        timer_fields=identifier in {"isis", "ospf"},
                    )
                    for identifier, label in ROUTING_PROTOCOL_LABELS
                ),
                boundary_adapters=tuple(
                    BuilderVisualBoundaryAdapterMetadata(
                        id=identifier,
                        label=label,
                        runtime_supported=(issue := runtime.check_protocol_adapter(identifier))
                        is None,
                        support_note=issue.support_note if issue is not None else None,
                    )
                    for identifier, label in BOUNDARY_ADAPTER_LABELS
                ),
                phasing_modes=tuple(
                    BuilderVisualPhasingModeMetadata(id=identifier, label=label)
                    for identifier, label in PHASING_MODE_LABELS
                ),
                orbit_shapes=tuple(
                    BuilderVisualOrbitShapeMetadata(id=identifier, label=label)
                    for identifier, label in ORBIT_SHAPE_LABELS
                ),
                orbit_propagators=tuple(
                    BuilderVisualOrbitPropagatorMetadata(
                        id=identifier,
                        label=label,
                        runtime_supported=(issue := runtime.check_propagator(identifier)) is None,
                        support_note=issue.support_note if issue is not None else None,
                    )
                    for identifier, label in ORBIT_PROPAGATOR_LABELS
                ),
                topology_modes=tuple(
                    BuilderVisualTopologyModeMetadata(
                        id=identifier,
                        label=label,
                        runtime_supported=(issue := runtime.check_link_topology(identifier))
                        is None,
                        support_note=issue.support_note if issue is not None else None,
                        requires_n=identifier == "nearest_n",
                    )
                    for identifier, label in TOPOLOGY_MODE_LABELS
                ),
            ),
        )

    def _encode_page_token(
        self,
        *,
        generation: str,
        family: CatalogFamily | None,
        namespace: str | None,
        cursor: str,
    ) -> str:
        payload = canonical_json_bytes(
            {
                "cursor": cursor,
                "family": family,
                "generation": generation,
                "namespace": namespace,
                "version": _PAGE_TOKEN_VERSION,
            }
        )
        encoded = base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")
        signature = hmac.new(self._page_key, payload, hashlib.sha256).hexdigest()
        return f"{_PAGE_TOKEN_PREFIX}.{encoded}.{signature}"

    def _decode_page_token(self, token: str) -> dict[str, Any]:
        try:
            prefix, encoded, signature = token.split(".", 2)
            if prefix != _PAGE_TOKEN_PREFIX:
                raise ValueError("unsupported token version")
            padding = "=" * (-len(encoded) % 4)
            payload = base64.b64decode(encoded + padding, altchars=b"-_", validate=True)
            expected = hmac.new(self._page_key, payload, hashlib.sha256).hexdigest()
            if not hmac.compare_digest(signature, expected):
                raise ValueError("signature mismatch")
            decoded = json.loads(payload)
            if not isinstance(decoded, dict):
                raise ValueError("token payload must be an object")
            expected_keys = {"cursor", "family", "generation", "namespace", "version"}
            if set(decoded) != expected_keys or decoded["version"] != _PAGE_TOKEN_VERSION:
                raise ValueError("token payload shape is invalid")
            if not all(
                decoded[key] is None or isinstance(decoded[key], str)
                for key in ("family", "namespace")
            ):
                raise ValueError("token filters are invalid")
            if not isinstance(decoded["cursor"], str) or not isinstance(decoded["generation"], str):
                raise ValueError("token cursor or generation is invalid")
            return decoded
        except (ValueError, UnicodeError, json.JSONDecodeError) as error:
            _refuse(
                "catalog_authoring.invalid_page_token",
                "Catalog page token is invalid",
                cause=error,
            )

    def list_catalog(self, request: CatalogListRequest) -> CatalogListPage:
        """List a deterministic page without mixing repository generations."""

        if not isinstance(request, CatalogListRequest):
            raise TypeError("request must be a CatalogListRequest")
        snapshot = self._snapshot()
        cursor: str | None = None
        if request.page_token is not None:
            token = self._decode_page_token(request.page_token)
            if token["family"] != request.family or token["namespace"] != request.namespace:
                _refuse(
                    "catalog_authoring.invalid_page_token",
                    "Catalog page token filters do not match the request",
                )
            if token["generation"] != str(snapshot.generation):
                _refuse(
                    "catalog_authoring.stale_page_token",
                    "Catalog changed while paging; restart listing from the first page",
                )
            cursor = token["cursor"]

        try:
            entries = snapshot.list(namespace=request.namespace, family=request.family)
        except (CatalogRepositoryError, OSError) as error:
            _refuse(
                "catalog_authoring.persistence_failed",
                f"Catalog listing failed: {error}",
                cause=error,
            )
        start = 0
        if cursor is not None:
            refs = [str(entry.ref) for entry in entries]
            try:
                start = refs.index(cursor) + 1
            except ValueError as error:
                _refuse(
                    "catalog_authoring.invalid_page_token",
                    "Catalog page token cursor is not present in its snapshot",
                    cause=error,
                )
        selected = entries[start : start + request.page_size]
        next_page_token = None
        if start + len(selected) < len(entries):
            next_page_token = self._encode_page_token(
                generation=str(snapshot.generation),
                family=request.family,
                namespace=request.namespace,
                cursor=str(selected[-1].ref),
            )
        return CatalogListPage(
            generation=str(snapshot.generation),
            items=tuple(
                CatalogDocumentSummary(
                    ref=entry.ref,
                    namespace=entry.namespace,
                    family=entry.family,
                    revision=str(entry.revision),
                    size_bytes=entry.size_bytes,
                    display_name=presentation[0],
                    summary=presentation[1],
                )
                for entry in selected
                for presentation in (_presentation_metadata(snapshot.get(entry.ref)),)
            ),
            next_page_token=next_page_token,
        )

    def get_catalog(self, request: CatalogGetRequest) -> BuilderCatalogDocument:
        """Return backend-canonical YAML/JSON and the exact stored revision."""

        if not isinstance(request, CatalogGetRequest):
            raise TypeError("request must be a CatalogGetRequest")
        return _canonical_document(_repository_document(self._snapshot(), request.ref))

    def _graph(self, snapshot: CatalogReadSnapshot) -> _GraphState:
        generation = str(snapshot.generation)
        if self._graph_cache is not None and self._graph_cache[0] == generation:
            return self._graph_cache[1]
        documents: dict[CatalogRef, CatalogDocument] = {}
        dependencies: dict[CatalogRef, frozenset[CatalogRef]] = {}
        try:
            entries = snapshot.list()
        except (CatalogRepositoryError, OSError) as error:
            _refuse(
                "catalog_authoring.persistence_failed",
                f"Catalog dependency graph could not be listed: {error}",
                cause=error,
            )
        for entry in entries:
            document = _repository_document(snapshot, entry.ref)
            model = _validated_exact_document(document.ref, document.content)
            documents[document.ref] = document
            dependencies[document.ref] = frozenset(catalog_document_references(model))
        graph = _GraphState(documents=documents, dependencies=dependencies)
        self._graph_cache = (generation, graph)
        return graph

    def _impact(
        self,
        snapshot: CatalogReadSnapshot,
        target: CatalogDocument,
    ) -> CatalogDependencyImpact:
        graph = self._graph(snapshot)
        reverse: dict[CatalogRef, set[CatalogRef]] = {}
        for dependent, dependencies in graph.dependencies.items():
            for dependency in dependencies:
                reverse.setdefault(dependency, set()).add(dependent)

        depths: dict[CatalogRef, int] = {}
        queue: deque[tuple[CatalogRef, int]] = deque([(target.ref, 0)])
        while queue:
            current, depth = queue.popleft()
            for dependent in sorted(reverse.get(current, ()), key=str):
                candidate_depth = depth + 1
                previous = depths.get(dependent)
                if previous is None or candidate_depth < previous:
                    depths[dependent] = candidate_depth
                    queue.append((dependent, candidate_depth))

        dependents = tuple(
            CatalogDependent(
                ref=ref,
                family=graph.documents[ref].family,
                revision=str(graph.documents[ref].revision),
                minimum_depth=depths[ref],
            )
            for ref in sorted(depths, key=str)
        )
        direct = tuple(item for item in dependents if item.minimum_depth == 1)
        acknowledgement = sha256_digest(
            canonical_json_bytes(
                {
                    "schema": _IMPACT_SCHEMA,
                    "target": {
                        "ref": str(target.ref),
                        "revision": str(target.revision),
                    },
                    "dependents": [
                        {
                            "ref": str(item.ref),
                            "revision": item.revision,
                            "minimum_depth": item.minimum_depth,
                        }
                        for item in dependents
                    ],
                }
            )
        )
        return CatalogDependencyImpact(
            target_ref=target.ref,
            target_revision=str(target.revision),
            direct_dependents=direct,
            transitive_dependents=dependents,
            overwrite_affects_dependents=bool(dependents),
            delete_allowed=not dependents,
            acknowledgement=acknowledgement,
        )

    def dependents(self, request: CatalogDependentsRequest) -> CatalogDependencyImpact:
        """Return typed direct/transitive reverse edges from one pinned snapshot."""

        if not isinstance(request, CatalogDependentsRequest):
            raise TypeError("request must be a CatalogDependentsRequest")
        snapshot = self._snapshot()
        return self._impact(snapshot, _repository_document(snapshot, request.ref))

    def save_component_at_snapshot(
        self,
        request: CatalogDocumentWriteRequest,
        snapshot: CatalogReadSnapshot,
    ) -> CatalogMutationResult:
        """Save against the exact snapshot used to derive or validate the document."""

        if not isinstance(request, CatalogDocumentWriteRequest):
            raise TypeError("request must be a CatalogDocumentWriteRequest")
        if not isinstance(snapshot, CatalogReadSnapshot):
            raise TypeError("snapshot must be a CatalogReadSnapshot")
        if snapshot.scope is not self._context.scope:
            raise ValueError("catalog snapshot does not belong to this server-selected scope")
        _user_owned(request.ref)
        try:
            canonical = canonicalize_persisted_configuration(request.ref, request.document)
        except (ValidationError, TypeError, ValueError) as error:
            _refuse(
                "catalog_authoring.invalid_document",
                f"Catalog document {request.ref} is invalid: {error}",
                ref=request.ref,
                cause=error,
            )

        try:
            transaction = self._context.repository.begin(
                self._context.scope,
                base_generation=snapshot.generation,
            )
            transaction.write_bytes(
                request.ref,
                canonical.yaml_bytes,
                expected_revision=request.expected_revision,
            )
            committed = transaction.commit()
        except CatalogReadOnlyError as error:
            _refuse(
                "catalog_authoring.read_only",
                str(error),
                ref=request.ref,
                cause=error,
            )
        except CatalogConflictError as error:
            current_revision = None
            with suppress(CatalogRepositoryError):
                current_revision = str(self._snapshot().get(request.ref).revision)
            _refuse(
                "catalog_authoring.stale_revision",
                str(error),
                ref=request.ref,
                expected_revision=request.expected_revision,
                current_revision=current_revision,
                cause=error,
            )
        except CatalogValidationError as error:
            _refuse(
                "catalog_authoring.invalid_graph",
                str(error),
                ref=request.ref,
                cause=error,
            )
        except (CatalogRepositoryError, OSError) as error:
            _refuse(
                "catalog_authoring.persistence_failed",
                f"Catalog write failed: {error}",
                ref=request.ref,
                cause=error,
            )

        saved = _repository_document(committed, request.ref)
        return CatalogMutationResult(
            document=_canonical_document(saved),
            impact=self._impact(committed, saved),
        )

    def save_component(self, request: CatalogDocumentWriteRequest) -> CatalogMutationResult:
        """Strictly canonicalize and atomically create or CAS-replace a component."""

        if not isinstance(request, CatalogDocumentWriteRequest):
            raise TypeError("request must be a CatalogDocumentWriteRequest")
        return self.save_component_at_snapshot(request, self._snapshot())

    def fork_component(self, request: CatalogForkRequest) -> CatalogForkResult:
        """Fork a full source model while changing only wrapper identity/ownership."""

        if not isinstance(request, CatalogForkRequest):
            raise TypeError("request must be a CatalogForkRequest")
        snapshot = self._snapshot()
        source = _repository_document(snapshot, request.source_ref)
        if request.expected_source_revision is not None and request.expected_source_revision != str(
            source.revision
        ):
            _refuse(
                "catalog_authoring.stale_revision",
                f"Fork source {request.source_ref} changed before it was copied",
                ref=request.source_ref,
                expected_revision=request.expected_source_revision,
                current_revision=str(source.revision),
            )
        canonical_source = _canonical_document(source)
        family = cast(CatalogFamily, request.source_ref.family)
        wrapper = catalog_family_spec(family).wrapper
        if wrapper is None:
            _refuse(
                "catalog_authoring.invalid_document",
                "Sessions are saved through Builder drafts and cannot be forked as components",
                ref=request.source_ref,
            )
        forked_json = copy.deepcopy(canonical_source.canonical_json)
        value = forked_json.get(wrapper)
        if not isinstance(value, dict):
            _refuse(
                "catalog_authoring.invalid_document",
                f"Canonical source {request.source_ref} omitted wrapper {wrapper!r}",
                ref=request.source_ref,
            )
        value["id"] = request.target_ref.relative_path.stem
        result = self.save_component_at_snapshot(
            CatalogDocumentWriteRequest(
                ref=request.target_ref,
                document=forked_json,
            ),
            snapshot,
        )
        return CatalogForkResult(source_ref=request.source_ref, result=result)

    def delete_catalog(self, request: CatalogDeleteRequest) -> CatalogDeleteResult:
        """Delete only an unreferenced user document after exact impact review."""

        if not isinstance(request, CatalogDeleteRequest):
            raise TypeError("request must be a CatalogDeleteRequest")
        _user_owned(request.ref)
        snapshot = self._snapshot()
        target = _repository_document(snapshot, request.ref)
        if str(target.revision) != request.expected_revision:
            _refuse(
                "catalog_authoring.stale_revision",
                f"Catalog document {request.ref} changed before deletion",
                ref=request.ref,
                expected_revision=request.expected_revision,
                current_revision=str(target.revision),
            )
        impact = self._impact(snapshot, target)
        if request.impact_acknowledgement != impact.acknowledgement:
            _refuse(
                "catalog_authoring.impact_mismatch",
                f"Catalog dependency impact changed before deletion of {request.ref}",
                ref=request.ref,
                expected_revision=request.expected_revision,
                current_revision=str(target.revision),
                impact=impact,
            )
        if not impact.delete_allowed:
            _refuse(
                "catalog_authoring.dependents_exist",
                f"Catalog document {request.ref} is still referenced",
                ref=request.ref,
                expected_revision=request.expected_revision,
                current_revision=str(target.revision),
                impact=impact,
            )
        try:
            transaction = self._context.repository.begin(
                self._context.scope,
                base_generation=snapshot.generation,
            )
            transaction.delete(request.ref, expected_revision=request.expected_revision)
            committed = transaction.commit()
        except CatalogConflictError as error:
            _refuse(
                "catalog_authoring.stale_revision",
                str(error),
                ref=request.ref,
                expected_revision=request.expected_revision,
                cause=error,
            )
        except CatalogValidationError as error:
            _refuse(
                "catalog_authoring.invalid_graph",
                str(error),
                ref=request.ref,
                impact=impact,
                cause=error,
            )
        except (CatalogRepositoryError, OSError) as error:
            _refuse(
                "catalog_authoring.persistence_failed",
                f"Catalog delete failed: {error}",
                ref=request.ref,
                cause=error,
            )
        return CatalogDeleteResult(
            deleted_ref=request.ref,
            deleted_revision=request.expected_revision,
            impact_acknowledgement=request.impact_acknowledgement,
            generation=str(committed.generation),
        )

    def export_session_yaml(
        self,
        request: CatalogSessionYamlExportRequest,
    ) -> CatalogSessionYamlExport:
        """Return ordinary root and dependency YAML files for browser writing."""

        if not isinstance(request, CatalogSessionYamlExportRequest):
            raise TypeError("request must be a CatalogSessionYamlExportRequest")
        snapshot = self._snapshot()
        session = _repository_document(snapshot, request.session_ref)
        if (
            request.expected_session_revision is not None
            and request.expected_session_revision != str(session.revision)
        ):
            _refuse(
                "catalog_authoring.stale_revision",
                f"Session {request.session_ref} changed before export",
                ref=request.session_ref,
                expected_revision=request.expected_session_revision,
                current_revision=str(session.revision),
            )
        try:
            closure = CatalogClosureCollector.collect(session.content, snapshot)
        except CatalogClosureError as error:
            _refuse(
                "catalog_authoring.invalid_graph",
                str(error),
                ref=request.session_ref,
                cause=error,
            )

        files = (
            CatalogYamlFile(
                logical_path=preserved_catalog_path(request.session_ref),
                yaml_text=session.content.decode("utf-8"),
            ),
            *(
                CatalogYamlFile(
                    logical_path=entry.preserved_path,
                    yaml_text=entry.yaml_bytes.decode("utf-8"),
                )
                for entry in closure.entries
            ),
        )
        return CatalogSessionYamlExport(
            session_ref=request.session_ref,
            session_revision=str(session.revision),
            files=files,
        )

    def _validate_import_bounds(
        self,
        request: CatalogSessionYamlImportRequest,
    ) -> tuple[bytes, ...]:
        limits = DEFAULT_CATALOG_UPLOAD_LIMITS
        contents = tuple(yaml_file.yaml_text.encode("utf-8") for yaml_file in request.yaml_files)
        checks = (
            ("max_file_count", len(contents), limits.max_file_count),
            (
                "max_aggregate_bytes",
                sum(map(len, contents)),
                limits.max_aggregate_bytes,
            ),
        )
        for name, actual, maximum in checks:
            if actual > maximum:
                _refuse(
                    "catalog_authoring.import_limit",
                    f"Import exceeds {name}: {actual} > {maximum}",
                )
        for position, content in enumerate(contents, start=1):
            if len(content) > limits.max_file_bytes:
                _refuse(
                    "catalog_authoring.import_limit",
                    f"Imported YAML file {position} exceeds max_file_bytes: "
                    f"{len(content)} > {limits.max_file_bytes}",
                )
        return contents

    def _import_proposal_token(
        self,
        *,
        snapshot: CatalogReadSnapshot,
        root_ref: SessionRef,
        proposed_writes: tuple[CatalogYamlImportWrite, ...],
        identical_refs: tuple[CatalogRef, ...],
        collisions: tuple[CatalogYamlImportCollision, ...],
    ) -> str:
        payload = canonical_json_bytes(
            {
                "collisions": [
                    {
                        "existing_revision": item.existing_revision,
                        "reason": item.reason,
                        "ref": str(item.ref),
                    }
                    for item in collisions
                ],
                "generation": str(snapshot.generation),
                "identical_refs": [str(ref) for ref in identical_refs],
                "root_ref": str(root_ref),
                "version": 1,
                "writes": [
                    {
                        "canonicalization_changed": item.canonicalization_changed,
                        "content_digest": sha256_digest(item.canonical_yaml.encode("utf-8")),
                        "family": item.family,
                        "logical_path": item.logical_path,
                        "ref": str(item.ref),
                    }
                    for item in proposed_writes
                ],
            }
        )
        signature = hmac.new(
            self._import_proposal_key,
            payload,
            hashlib.sha256,
        ).hexdigest()
        return f"{_IMPORT_PROPOSAL_TOKEN_PREFIX}.{signature}"

    def import_session_yaml(
        self,
        request: CatalogSessionYamlImportRequest,
    ) -> CatalogSessionYamlImportResult:
        """Derive YAML placement from typed identity and import atomically."""

        if not isinstance(request, CatalogSessionYamlImportRequest):
            raise TypeError("request must be a CatalogSessionYamlImportRequest")
        self._validate_import_bounds(request)
        parsed = _parse_yaml_import(request.yaml_files)
        limits = DEFAULT_CATALOG_UPLOAD_LIMITS
        if len(parsed.root_content) > limits.max_root_yaml_bytes:
            _refuse(
                "catalog_authoring.import_limit",
                "Imported session exceeds max_root_yaml_bytes: "
                f"{len(parsed.root_content)} > {limits.max_root_yaml_bytes}",
                ref=parsed.root_ref,
            )

        snapshot = self._snapshot()
        root_ref = (
            parsed.root_ref
            if parsed.root_ref_hinted
            else _derive_import_session_ref(snapshot, parsed.root_ref)
        )
        _validated_exact_document(root_ref, parsed.root_content)
        read_view = _IdentityImportReadView(
            snapshot=snapshot,
            components=parsed.components,
            assigned_refs={},
            assigned_documents={},
        )
        try:
            CatalogClosureCollector.collect(parsed.root_content, read_view)
        except _ImportPlacementError as error:
            _refuse(
                "catalog_authoring.import_incomplete",
                str(error),
                ref=root_ref,
                cause=error,
            )
        except CatalogClosureError as error:
            _refuse(
                "catalog_authoring.invalid_graph",
                str(error),
                ref=root_ref,
                cause=error,
            )

        unused = sorted(
            str(component.hinted_ref)
            if component.hinted_ref is not None
            else f"{component.family}/{component.identity} (file {component.position})"
            for components in parsed.components.values()
            for component in components
            if component.position not in read_view.assigned_refs
        )
        if unused:
            _refuse(
                "catalog_authoring.import_incomplete",
                "Imported catalog YAML files are not referenced by the session graph: "
                + ", ".join(unused),
                ref=root_ref,
            )

        incoming = {
            ref: _canonical_import_bytes(ref, content)
            for ref, content in read_view.assigned_documents.items()
        }
        canonical_root_bytes = _canonical_import_bytes(
            root_ref,
            parsed.root_content,
        )
        try:
            CatalogClosureCollector.collect(
                canonical_root_bytes,
                _OverlayImportReadView(snapshot=snapshot, documents=incoming),
            )
        except CatalogClosureError as error:
            _refuse(
                "catalog_authoring.invalid_graph",
                f"Canonicalized YAML import is invalid: {error}",
                ref=root_ref,
                cause=error,
            )

        all_documents = {**incoming, root_ref: canonical_root_bytes}
        source_documents = {
            **read_view.assigned_documents,
            root_ref: parsed.root_content,
        }
        proposed: list[CatalogYamlImportWrite] = []
        identical: list[CatalogRef] = []
        collisions: list[CatalogYamlImportCollision] = []
        for ref, content in sorted(all_documents.items(), key=lambda item: str(item[0])):
            try:
                existing = snapshot.get(ref)
            except CatalogNotFoundError:
                existing = None
            except (CatalogRepositoryError, OSError) as error:
                _refuse(
                    "catalog_authoring.persistence_failed",
                    f"Import collision check failed for {ref}: {error}",
                    ref=ref,
                    cause=error,
                )

            if existing is None:
                if ref.namespace == "nodalarc":
                    collisions.append(
                        CatalogYamlImportCollision(
                            ref=ref,
                            reason="shipped_missing",
                        )
                    )
                else:
                    proposed.append(
                        CatalogYamlImportWrite(
                            ref=ref,
                            family=cast(CatalogFamily, ref.family),
                            logical_path=preserved_catalog_path(ref),
                            canonical_yaml=content.decode("utf-8"),
                            canonicalization_changed=(source_documents[ref] != content),
                        )
                    )
                continue
            if existing.content == content:
                identical.append(ref)
                continue
            collisions.append(
                CatalogYamlImportCollision(
                    ref=ref,
                    reason=(
                        "shipped_content_mismatch"
                        if ref.namespace == "nodalarc"
                        else "user_content_mismatch"
                    ),
                    existing_revision=str(existing.revision),
                )
            )

        proposed_writes = tuple(proposed)
        identical_refs = tuple(sorted(identical, key=str))
        collision_result = tuple(collisions)
        proposal_token = self._import_proposal_token(
            snapshot=snapshot,
            root_ref=root_ref,
            proposed_writes=proposed_writes,
            identical_refs=identical_refs,
            collisions=collision_result,
        )
        if request.commit and not hmac.compare_digest(
            cast(str, request.proposal_token),
            proposal_token,
        ):
            _refuse(
                "catalog_authoring.stale_import_proposal",
                "The reviewed YAML import proposal is stale; review the current write set again",
                ref=root_ref,
                expected_revision=request.proposal_token,
                current_revision=proposal_token,
            )
        result_fields = {
            "root_ref": root_ref,
            "generation": str(snapshot.generation),
            "proposed_writes": proposed_writes,
            "identical_refs": identical_refs,
        }
        if collision_result:
            return CatalogSessionYamlImportResult(
                outcome="blocked",
                collisions=collision_result,
                **result_fields,
            )
        if not proposed_writes:
            return CatalogSessionYamlImportResult(
                outcome="unchanged",
                collisions=(),
                **result_fields,
            )
        if not request.commit:
            return CatalogSessionYamlImportResult(
                outcome="proposed",
                proposal_token=proposal_token,
                collisions=(),
                **result_fields,
            )

        ordered_writes = sorted(
            proposed_writes,
            key=lambda item: (item.family == "sessions", str(item.ref)),
        )
        try:
            transaction = self._context.repository.begin(
                self._context.scope,
                base_generation=snapshot.generation,
            )
            for item in ordered_writes:
                transaction.write_bytes(
                    item.ref,
                    item.canonical_yaml.encode("utf-8"),
                    expected_revision=None,
                )
            committed = transaction.commit()
        except CatalogConflictError as error:
            _refuse(
                "catalog_authoring.conflict",
                f"Catalog changed while importing YAML files: {error}",
                ref=root_ref,
                cause=error,
            )
        except CatalogValidationError as error:
            _refuse(
                "catalog_authoring.invalid_graph",
                str(error),
                ref=root_ref,
                cause=error,
            )
        except (CatalogRepositoryError, OSError) as error:
            _refuse(
                "catalog_authoring.persistence_failed",
                f"Catalog import failed: {error}",
                ref=root_ref,
                cause=error,
            )
        for item in proposed_writes:
            stored = _repository_document(committed, item.ref)
            if stored.content != item.canonical_yaml.encode("utf-8"):
                _refuse(
                    "catalog_authoring.persistence_failed",
                    f"Imported document {item.ref} failed exact-byte verification",
                    ref=item.ref,
                )
        return CatalogSessionYamlImportResult(
            root_ref=root_ref,
            outcome="committed",
            generation=str(committed.generation),
            proposed_writes=proposed_writes,
            identical_refs=identical_refs,
            collisions=(),
        )
