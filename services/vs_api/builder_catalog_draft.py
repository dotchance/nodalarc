"""Backend-owned, lossless catalog component draft authoring."""

from __future__ import annotations

import copy
from typing import Any, Literal, cast

import yaml
from nodalarc.catalog_closure import (
    CatalogClosureCollector,
    CatalogClosureError,
    CatalogDocumentNotFound,
    CatalogReadError,
    CatalogReadView,
    load_catalog_object,
)
from nodalarc.catalog_refs import CatalogRef
from nodalarc.catalog_registry import catalog_family_spec
from nodalarc.catalog_repository import (
    CatalogNotFoundError,
    CatalogReadSnapshot,
    CatalogRepositoryError,
)
from nodalarc.document_errors import document_errors
from nodalarc.models.builder_api import (
    BuilderIssue,
    BuilderProposedCatalogDocument,
    JsonDocument,
)
from nodalarc.models.builder_catalog_api import (
    CatalogComponentDraftEnvelope,
    CatalogComponentDraftState,
    CatalogComponentFamily,
    CatalogDocumentWriteRequest,
    CatalogDraftAddNodeEthernetPortRequest,
    CatalogDraftAddNodeTerminalMountRequest,
    CatalogDraftAddSiteNodeRequest,
    CatalogDraftApplyYamlRequest,
    CatalogDraftApplyYamlResult,
    CatalogDraftCompileRequest,
    CatalogDraftCompileResult,
    CatalogDraftControlMutationRequest,
    CatalogDraftNewRequest,
    CatalogDraftOpenRequest,
    CatalogDraftSaveRequest,
    CatalogDraftSaveResult,
)
from nodalarc.models.catalog import (
    Node,
    Terminal,
)
from nodalarc.runtime_support import RuntimeSupport
from pydantic import BaseModel, TypeAdapter, ValidationError

from .builder_catalog_service import (
    BuilderCatalogAuthoringService,
    catalog_snapshot,
    refuse,
)
from .builder_compiler import (
    CanonicalConfigurationDocument,
    OverlayCatalogReadView,
    ReachableProposalValidationError,
    canonicalize_persisted_configuration,
)
from .builder_control_mutation import (
    BuilderControlMutationError,
    apply_builder_control_mutations,
)
from .builder_control_tree import BuilderControlTreeBuild, build_model_control_tree
from .builder_visual_defaults import DEFAULT_TERMINAL_MOUNT_COUNT
from .builder_yaml import parse_yaml_application
from .catalog_context import CatalogContext

_MAX_POINTER_DEPTH = 32
_FORBIDDEN_POINTER_TOKENS = frozenset({".", "..", "__proto__", "constructor", "prototype"})


_JSON_DOCUMENT = TypeAdapter(JsonDocument)


def _wrapper(family: CatalogComponentFamily) -> str:
    wrapper = catalog_family_spec(family).wrapper
    if wrapper is None:
        raise AssertionError("component draft families must use catalog object wrappers")
    return wrapper


def _decode_pointer(value: str) -> tuple[str, ...]:
    if not value.startswith("/"):
        refuse("catalog_authoring.invalid_patch", "JSON pointers must start with '/'")
    raw_tokens = value.split("/")[1:]
    if not raw_tokens or len(raw_tokens) > _MAX_POINTER_DEPTH:
        refuse(
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
                refuse(
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
            refuse(
                "catalog_authoring.invalid_patch",
                f"JSON pointer {value!r} contains a forbidden token",
            )
        tokens.append(token)
    return tuple(tokens)


def _component_issue(
    code: str,
    stage: Literal["structural", "reference", "runtime_support"],
    message: str,
    *,
    target_ref: CatalogRef,
    json_pointer: str,
) -> BuilderIssue:
    """A component finding: structure and references block save and deploy;
    runtime support blocks deploy only."""
    return BuilderIssue(
        code=code,
        stage=stage,
        severity="error",
        message=message,
        blocks=("deploy",) if stage == "runtime_support" else ("save", "deploy"),
        source_ref=str(target_ref),
        json_pointer=json_pointer,
    )


def _structural_issues(
    family: CatalogComponentFamily,
    target_ref: CatalogRef,
    document: JsonDocument,
    error: BaseException,
) -> tuple[BuilderIssue, ...]:
    if isinstance(error, ValidationError):
        return tuple(
            _component_issue(
                f"builder.structural.{item.type}",
                "structural",
                item.message,
                target_ref=target_ref,
                json_pointer=item.pointer or "/",
            )
            for item in document_errors(error, document)
        )
    return (
        _component_issue(
            "builder.structural.invalid_document",
            "structural",
            f"Catalog component {target_ref} is invalid: {error}",
            target_ref=target_ref,
            json_pointer=f"/{_wrapper(family)}",
        ),
    )


def _analyze(
    family: CatalogComponentFamily,
    target_ref: CatalogRef,
    document: JsonDocument,
    *,
    view: CatalogReadView,
) -> tuple[CanonicalConfigurationDocument | None, tuple[BuilderIssue, ...]]:
    try:
        canonical = canonicalize_persisted_configuration(target_ref, document)
    except (ValidationError, TypeError, ValueError) as error:
        return None, _structural_issues(family, target_ref, document, error)
    model = catalog_family_spec(family).validate_document(canonical.canonical_json)
    try:
        findings = RuntimeSupport.earth_luna().check_catalog_object(
            model,
            body_id=lambda ref: str(cast(Any, load_catalog_object(CatalogRef(ref), view)[1]).id),
        )
    except CatalogReadError, ValidationError, ValueError:
        # A body reference that does not read is the reference check's refusal,
        # which blocks save and deploy; its runtime support has nothing to judge.
        findings = ()
    runtime_issues = tuple(
        _component_issue(
            f"builder.runtime_support.{feature.category.value}.{feature.value}",
            "runtime_support",
            feature.message,
            target_ref=target_ref,
            json_pointer=pointer,
        )
        for feature, pointer in findings
    )
    return canonical, runtime_issues


def _reference_issues(
    snapshot: CatalogReadSnapshot,
    target_ref: CatalogRef,
    canonical: CanonicalConfigurationDocument,
) -> tuple[BuilderIssue, ...]:
    draft = BuilderProposedCatalogDocument(
        ref=target_ref, document=canonical.canonical_json, origin="customized"
    )
    try:
        CatalogClosureCollector.collect_references(
            (target_ref,),
            OverlayCatalogReadView(snapshot, {target_ref: (0, draft)}),
        )
    except CatalogClosureError as error:
        return (
            _component_issue(
                f"builder.reference.{error.code.value}",
                "reference",
                error.evidence.message,
                target_ref=target_ref,
                json_pointer=f"/{_wrapper(cast(CatalogComponentFamily, canonical.family))}",
            ),
        )
    return ()


def _control_build(
    family: CatalogComponentFamily,
    document: JsonDocument,
    draft_revision: int,
) -> BuilderControlTreeBuild:
    spec = catalog_family_spec(family)
    if spec.wrapper is None:
        raise AssertionError("catalog component controls require a wrapped family")
    return build_model_control_tree(
        spec.document_model_type,
        document,
        projection_revision=draft_revision,
        root_label=f"{spec.wrapper.replace('_', ' ').title()} component",
        specialized_fields={(spec.model_type, "id")},
    )


def _envelope(
    state: CatalogComponentDraftState, *, view: CatalogReadView
) -> CatalogComponentDraftEnvelope:
    canonical, issues = _analyze(state.family, state.target_ref, state.document, view=view)
    projected_yaml = (
        canonical.yaml_bytes.decode("utf-8")
        if canonical is not None
        else yaml.safe_dump(
            state.document,
            allow_unicode=True,
            default_flow_style=False,
            sort_keys=True,
        )
    )
    control_build = _control_build(state.family, state.document, state.draft_revision)
    return CatalogComponentDraftEnvelope(
        state=state,
        projected_yaml=projected_yaml,
        control_tree=control_build.tree,
        issues=issues,
    )


def _next_state(
    state: CatalogComponentDraftState,
    document: JsonDocument,
) -> CatalogComponentDraftState:
    """The draft one revision after `state`, holding `document`."""

    return CatalogComponentDraftState(
        draft_revision=state.draft_revision + 1,
        family=state.family,
        target_ref=state.target_ref,
        source_ref=state.source_ref,
        expected_source_revision=state.expected_source_revision,
        expected_target_revision=state.expected_target_revision,
        document=document,
    )


def _referenced[T: BaseModel](
    ref: CatalogRef,
    view: CatalogReadView,
    expected: type[T],
    *,
    draft_ref: CatalogRef,
) -> T:
    """The object a gesture references, read through the request's view.

    A ref the view cannot read is refused as missing or unreadable; an object
    that does not validate, or is not the expected kind, is refused as an
    invalid graph naming the ref.
    """
    try:
        _, model = load_catalog_object(ref, view)
    except CatalogDocumentNotFound as error:
        refuse(
            "catalog_authoring.not_found",
            f"Catalog document does not exist: {ref}",
            ref=ref,
            cause=error,
        )
    except CatalogReadError as error:
        refuse(
            "catalog_authoring.persistence_failed",
            f"Catalog document {ref} could not be read: {error}",
            ref=ref,
            cause=error,
        )
    except (ReachableProposalValidationError, ValidationError, TypeError, ValueError) as error:
        refuse(
            "catalog_authoring.invalid_graph",
            f"Referenced {ref} is invalid: {error}",
            ref=draft_ref,
            cause=error,
        )
    if not isinstance(model, expected):
        refuse(
            "catalog_authoring.invalid_graph",
            f"Reference {ref} does not resolve to a {expected.__name__.lower()} component",
            ref=draft_ref,
        )
    return model


def _optional_revision(snapshot: CatalogReadSnapshot, ref: CatalogRef) -> str | None:
    try:
        return str(snapshot.get(ref).revision)
    except CatalogNotFoundError:
        return None
    except (CatalogRepositoryError, OSError) as error:
        refuse(
            "catalog_authoring.persistence_failed",
            f"Catalog document {ref} could not be read: {error}",
            ref=ref,
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
        draft: CatalogComponentDraftState,
        expected: int,
    ) -> None:
        if expected != draft.draft_revision:
            refuse(
                "catalog_authoring.stale_revision",
                "Component draft changed before the requested operation",
                ref=draft.target_ref,
                expected_revision=str(expected),
                current_revision=str(draft.draft_revision),
            )

    def _assert_catalog_revisions(
        self,
        draft: CatalogComponentDraftState,
        snapshot: CatalogReadSnapshot,
    ) -> None:
        self._assert_ownership_mode(draft)
        if draft.source_ref is not None:
            current_source = _optional_revision(snapshot, draft.source_ref)
            if current_source != draft.expected_source_revision:
                refuse(
                    "catalog_authoring.stale_revision",
                    f"Component draft source {draft.source_ref} changed after it was opened",
                    ref=draft.source_ref,
                    expected_revision=draft.expected_source_revision,
                    current_revision=current_source,
                )
        current_target = _optional_revision(snapshot, draft.target_ref)
        if current_target != draft.expected_target_revision:
            refuse(
                "catalog_authoring.stale_revision",
                f"Component draft target {draft.target_ref} changed after it was opened",
                ref=draft.target_ref,
                expected_revision=draft.expected_target_revision,
                current_revision=current_target,
            )

    @staticmethod
    def _assert_ownership_mode(draft: CatalogComponentDraftState) -> None:
        if draft.source_ref is None:
            if (
                draft.expected_source_revision is not None
                or draft.expected_target_revision is not None
            ):
                refuse(
                    "catalog_authoring.invalid_graph",
                    "New component drafts cannot carry persisted catalog revisions",
                    ref=draft.target_ref,
                )
            return
        if draft.source_ref == draft.target_ref:
            if (
                draft.source_ref.namespace != "user"
                or draft.expected_source_revision is None
                or draft.expected_target_revision != draft.expected_source_revision
            ):
                refuse(
                    "catalog_authoring.invalid_graph",
                    "In-place component drafts require one matching user source and target revision",
                    ref=draft.target_ref,
                )
            return
        if draft.expected_source_revision is None:
            refuse(
                "catalog_authoring.invalid_graph",
                "Component forks require a pinned source revision",
                ref=draft.target_ref,
            )
        if draft.expected_target_revision is not None:
            refuse(
                "catalog_authoring.conflict",
                f"Component fork target already exists: {draft.target_ref}",
                ref=draft.target_ref,
                current_revision=draft.expected_target_revision,
            )

    def new(self, request: CatalogDraftNewRequest) -> CatalogComponentDraftEnvelope:
        if not isinstance(request, CatalogDraftNewRequest):
            raise TypeError("request must be a CatalogDraftNewRequest")
        target_ref = CatalogRef(f"user:{request.family}/{request.object_id}.yaml")
        snapshot = catalog_snapshot(self._context)
        if _optional_revision(snapshot, target_ref) is not None:
            refuse(
                "catalog_authoring.conflict",
                f"Catalog component already exists: {target_ref}",
                ref=target_ref,
            )
        wrapper = _wrapper(request.family)
        return _envelope(
            CatalogComponentDraftState(
                draft_revision=0,
                family=request.family,
                target_ref=target_ref,
                document={wrapper: {"id": request.object_id}},
            ),
            view=snapshot,
        )

    def open(self, request: CatalogDraftOpenRequest) -> CatalogComponentDraftEnvelope:
        if not isinstance(request, CatalogDraftOpenRequest):
            raise TypeError("request must be a CatalogDraftOpenRequest")
        snapshot = catalog_snapshot(self._context)
        return self._open(request, snapshot=snapshot, view=snapshot)

    def _open(
        self,
        request: CatalogDraftOpenRequest,
        *,
        snapshot: CatalogReadSnapshot,
        view: CatalogReadView,
    ) -> CatalogComponentDraftEnvelope:
        family = cast(CatalogComponentFamily, request.source_ref.family)
        wrapper = _wrapper(family)
        source = _referenced(
            request.source_ref,
            view,
            BaseModel,
            draft_ref=request.target_ref or request.source_ref,
        )
        target_ref = request.target_ref
        if target_ref is None:
            target_ref = (
                request.source_ref
                if request.source_ref.namespace == "user"
                else CatalogRef(f"user:{request.source_ref.relative_path.as_posix()}")
            )
        target_revision = _optional_revision(snapshot, target_ref)
        if target_ref == request.source_ref:
            if request.source_ref.namespace != "user":
                refuse(
                    "catalog_authoring.read_only",
                    f"Shipped catalog document {request.source_ref} is read-only",
                    ref=request.source_ref,
                )
        elif target_revision is not None:
            refuse(
                "catalog_authoring.conflict",
                f"Catalog customization target already exists: {target_ref}",
                ref=target_ref,
            )
        body = cast(JsonDocument, source.model_dump(mode="json", by_alias=True, exclude_none=True))
        body["id"] = target_ref.relative_path.stem
        document: JsonDocument = {wrapper: body}
        return _envelope(
            CatalogComponentDraftState(
                draft_revision=0,
                family=family,
                target_ref=target_ref,
                source_ref=request.source_ref,
                expected_source_revision=_optional_revision(snapshot, request.source_ref),
                expected_target_revision=target_revision,
                document=document,
            ),
            view=snapshot,
        )

    def mutate_controls(
        self,
        request: CatalogDraftControlMutationRequest,
    ) -> CatalogComponentDraftEnvelope:
        """Apply one atomic batch through backend-derived component controls."""

        if not isinstance(request, CatalogDraftControlMutationRequest):
            raise TypeError("request must be a CatalogDraftControlMutationRequest")
        self._assert_draft_revision(request.draft, request.expected_draft_revision)
        snapshot = catalog_snapshot(self._context)
        self._assert_catalog_revisions(request.draft, snapshot)
        control_build = _control_build(
            request.draft.family,
            request.draft.document,
            request.draft.draft_revision,
        )

        wrapper = _wrapper(request.draft.family)
        protected_pointers = {"", f"/{wrapper}", f"/{wrapper}/id"}
        for command in request.commands:
            binding = control_build.bindings.get(command.control_id)
            if binding is not None and binding.json_pointer in protected_pointers:
                refuse(
                    "catalog_authoring.invalid_patch",
                    "Graphical controls cannot change the family wrapper or object identity",
                    ref=request.draft.target_ref,
                )
        try:
            candidate = apply_builder_control_mutations(
                request.draft.document,
                control_build.bindings,
                request.commands,
            )
            document = _JSON_DOCUMENT.validate_python(candidate, strict=True)
        except (BuilderControlMutationError, ValidationError, TypeError, ValueError) as error:
            refuse(
                "catalog_authoring.invalid_patch",
                f"Component graphical mutation is invalid: {error}",
                ref=request.draft.target_ref,
                cause=error,
            )
        return _envelope(_next_state(request.draft, document), view=snapshot)

    def add_site_node(
        self,
        request: CatalogDraftAddSiteNodeRequest,
    ) -> CatalogComponentDraftEnvelope:
        """Add one SiteNode whose persisted shape is derived by the backend."""

        if not isinstance(request, CatalogDraftAddSiteNodeRequest):
            raise TypeError("request must be a CatalogDraftAddSiteNodeRequest")
        self._assert_draft_revision(request.draft, request.expected_draft_revision)
        snapshot = catalog_snapshot(self._context)
        self._assert_catalog_revisions(request.draft, snapshot)
        return self._add_site_node(request, snapshot=snapshot, view=snapshot)

    def _add_site_node(
        self,
        request: CatalogDraftAddSiteNodeRequest,
        *,
        snapshot: CatalogReadSnapshot,
        view: CatalogReadView,
    ) -> CatalogComponentDraftEnvelope:

        node_definition = _referenced(
            request.node_ref, view, Node, draft_ref=request.draft.target_ref
        )

        document = copy.deepcopy(request.draft.document)
        site = document[_wrapper("sites")]
        if not isinstance(site, dict):
            refuse(
                "catalog_authoring.invalid_document",
                "Site component root must be an object before a node can be added",
                ref=request.draft.target_ref,
            )
        nodes = site.get("nodes", [])
        if not isinstance(nodes, list):
            refuse(
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
            refuse(
                "catalog_authoring.conflict",
                f"Site node id already exists: {request.node_id}",
                ref=request.draft.target_ref,
            )

        terminals: JsonDocument = {}
        for mount in node_definition.terminals:
            installation: JsonDocument = {"installed_count": mount.count}
            if mount.role == "access":
                installation["capabilities"] = {"boresight": {"mode": "local_vertical"}}
            terminals[mount.id] = installation
        # Bind every port the node definition declares to the site's first
        # declared segment; the author rebinds per port as needed. Addresses
        # are resolver-allocated, so the scaffold carries no address fields.
        segments = site.get("ethernet", [])
        first_segment = (
            segments[0].get("id") if segments and isinstance(segments[0], dict) else None
        )
        if first_segment is None:
            # A bare draft has no segments yet; scaffold the first one so
            # the added node has something to bind to. The author renames
            # or extends the segment list before saving.
            first_segment = "lan0"
            site["ethernet"] = [{"id": first_segment}]
        nodes.append(
            {
                "id": request.node_id,
                "node": str(request.node_ref),
                "payloads": {},
                "terminals": terminals,
                "interfaces": {port.id: first_segment for port in node_definition.ethernet},
            }
        )
        site["nodes"] = nodes
        return _envelope(_next_state(request.draft, document), view=snapshot)

    def add_node_terminal_mount(
        self,
        request: CatalogDraftAddNodeTerminalMountRequest,
    ) -> CatalogComponentDraftEnvelope:
        """Add one complete node mount from explicit terminal and role intent."""

        if not isinstance(request, CatalogDraftAddNodeTerminalMountRequest):
            raise TypeError("request must be a CatalogDraftAddNodeTerminalMountRequest")
        self._assert_draft_revision(request.draft, request.expected_draft_revision)
        snapshot = catalog_snapshot(self._context)
        self._assert_catalog_revisions(request.draft, snapshot)
        return self._add_node_terminal_mount(request, snapshot=snapshot, view=snapshot)

    def _add_node_terminal_mount(
        self,
        request: CatalogDraftAddNodeTerminalMountRequest,
        *,
        snapshot: CatalogReadSnapshot,
        view: CatalogReadView,
    ) -> CatalogComponentDraftEnvelope:

        _referenced(request.terminal_ref, view, Terminal, draft_ref=request.draft.target_ref)

        document = copy.deepcopy(request.draft.document)
        node = document[_wrapper("nodes")]
        if not isinstance(node, dict):
            refuse(
                "catalog_authoring.invalid_document",
                "Node component root must be an object before a terminal mount can be added",
                ref=request.draft.target_ref,
            )
        mounts = node.get("terminals", [])
        if not isinstance(mounts, list):
            refuse(
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
                **(
                    {"boresight": request.boresight.model_dump(mode="json")}
                    if request.boresight is not None
                    else {}
                ),
            }
        )
        node["terminals"] = mounts
        return _envelope(_next_state(request.draft, document), view=snapshot)

    def add_node_ethernet_port(
        self,
        request: CatalogDraftAddNodeEthernetPortRequest,
    ) -> CatalogComponentDraftEnvelope:
        """Add one uniquely identified Ethernet port to a node draft."""

        if not isinstance(request, CatalogDraftAddNodeEthernetPortRequest):
            raise TypeError("request must be a CatalogDraftAddNodeEthernetPortRequest")
        self._assert_draft_revision(request.draft, request.expected_draft_revision)
        snapshot = catalog_snapshot(self._context)
        self._assert_catalog_revisions(request.draft, snapshot)

        document = copy.deepcopy(request.draft.document)
        node = document[_wrapper("nodes")]
        if not isinstance(node, dict):
            refuse(
                "catalog_authoring.invalid_document",
                "Node component root must be an object before an Ethernet port can be added",
                ref=request.draft.target_ref,
            )
        ethernet = node.get("ethernet", [])
        if not isinstance(ethernet, list):
            refuse(
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
        return _envelope(_next_state(request.draft, document), view=snapshot)

    def apply_yaml(
        self,
        request: CatalogDraftApplyYamlRequest,
    ) -> CatalogDraftApplyYamlResult:
        """Parse one exact YAML buffer and advance only a graphically safe draft."""

        if not isinstance(request, CatalogDraftApplyYamlRequest):
            raise TypeError("request must be a CatalogDraftApplyYamlRequest")
        self._assert_draft_revision(request.draft, request.expected_draft_revision)
        snapshot = catalog_snapshot(self._context)
        self._assert_catalog_revisions(request.draft, snapshot)
        application = parse_yaml_application(request.draft.target_ref, request.yaml_text)
        if application.canonical is None:
            # The page holds the refused text; the draft stays as it was.
            return CatalogDraftApplyYamlResult(
                draft=_envelope(request.draft, view=snapshot),
                yaml_text=request.yaml_text,
                applied=False,
                canonicalization_required=False,
                issues=application.issues,
            )
        applied = _envelope(
            _next_state(request.draft, application.canonical.canonical_json), view=snapshot
        )
        return CatalogDraftApplyYamlResult(
            draft=applied,
            yaml_text=request.yaml_text,
            applied=True,
            canonicalization_required=request.yaml_text != applied.projected_yaml,
            issues=applied.issues,
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
            view=snapshot,
        )
        if canonical is not None:
            issues = (
                *issues,
                *_reference_issues(snapshot, request.draft.target_ref, canonical),
            )
        draft = _envelope(request.draft, view=snapshot)
        if draft.issues != issues:
            draft = CatalogComponentDraftEnvelope(
                **{
                    **draft.model_dump(mode="python"),
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
        return self._compile_at_snapshot(request, catalog_snapshot(self._context))

    def save(self, request: CatalogDraftSaveRequest) -> CatalogDraftSaveResult:
        if not isinstance(request, CatalogDraftSaveRequest):
            raise TypeError("request must be a CatalogDraftSaveRequest")
        snapshot = catalog_snapshot(self._context)
        compiled = self._compile_at_snapshot(
            CatalogDraftCompileRequest(
                draft=request.draft,
                expected_draft_revision=request.expected_draft_revision,
            ),
            snapshot,
        )
        if not compiled.save_allowed or compiled.canonical_json is None:
            first = compiled.issues[0]
            refuse(
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
            CatalogComponentDraftState(
                draft_revision=request.draft.draft_revision,
                family=request.draft.family,
                target_ref=request.draft.target_ref,
                source_ref=request.draft.target_ref,
                expected_source_revision=revision,
                expected_target_revision=revision,
                document=result.document.canonical_json,
            ),
            view=snapshot,
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
