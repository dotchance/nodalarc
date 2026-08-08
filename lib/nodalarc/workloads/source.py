# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""Workload package loading: one immutable snapshot per explicit selection.

A package source receives an explicit binding reference and returns one
immutable loaded package: the admitted binding, every referenced profile,
and the exact bytes of each document and declared package file. The loaded
package computes the one content digest that identifies the selection.
Loading never falls back: a reference this source cannot serve, a missing or
invalid document, a file that does not match its declaration, or content
resolving outside the configured root is a typed refusal.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from types import MappingProxyType
from typing import Protocol

import yaml

from nodalarc.catalog_refs import CatalogNamespace
from nodalarc.configuration_yaml import load_configuration_yaml
from nodalarc.content_identity import (
    SHA256_DIGEST_PATTERN,
    canonical_json_bytes,
    sha256_digest,
)
from nodalarc.workloads.admission import (
    AdmissionEvidence,
    admit_binding,
    admit_profile,
)
from nodalarc.workloads.binding import ImplementationBinding
from nodalarc.workloads.profile import NodeWorkloadProfile
from nodalarc.workloads.refs import ImplementationBindingRef, ProfileRef


class PackageLoadCode(StrEnum):
    PACKAGE_REF_INVALID = "PACKAGE_REF_INVALID"
    PACKAGE_DOCUMENT_MISSING = "PACKAGE_DOCUMENT_MISSING"
    PACKAGE_DOCUMENT_INVALID = "PACKAGE_DOCUMENT_INVALID"
    PACKAGE_FILE_MISSING = "PACKAGE_FILE_MISSING"
    PACKAGE_FILE_MISMATCH = "PACKAGE_FILE_MISMATCH"
    PACKAGE_PATH_ESCAPE = "PACKAGE_PATH_ESCAPE"


class PackageLoadError(ValueError):
    """Typed refusal for a selection this source cannot honestly serve."""

    def __init__(
        self,
        code: PackageLoadCode,
        detail: str,
        *,
        rejections: tuple[AdmissionEvidence, ...] = (),
    ) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail
        self.rejections = rejections


def _decode_document(content: bytes, *, what: str) -> object:
    try:
        return load_configuration_yaml(content)
    except (yaml.YAMLError, UnicodeError, ValueError, TypeError) as error:
        raise ValueError(f"{what} bytes are not valid YAML: {error}") from error


def _derive_profile(content: bytes, ref: ProfileRef) -> NodeWorkloadProfile:
    """Admit the profile a byte sequence actually contains at a reference."""
    admission = admit_profile(
        _decode_document(content, what="profile document"), object_ref=str(ref)
    )
    if admission.profile is None:
        details = "; ".join(rejection.detail for rejection in admission.rejections)
        raise ValueError(f"profile document bytes do not admit at {ref}: {details}")
    return admission.profile


def _derive_binding(content: bytes, ref: ImplementationBindingRef) -> ImplementationBinding:
    """Admit the binding a byte sequence actually contains at a reference."""
    admission = admit_binding(
        _decode_document(content, what="binding document"), object_ref=str(ref)
    )
    if admission.binding is None:
        details = "; ".join(rejection.detail for rejection in admission.rejections)
        raise ValueError(f"binding document bytes do not admit at {ref}: {details}")
    return admission.binding


@dataclass(frozen=True, slots=True)
class LoadedProfile:
    """One admitted profile with its exact document and file bytes.

    The admitted model must equal the model the retained bytes derive; a
    record pairing bytes with a model from anywhere else is unconstructible.
    """

    ref: ProfileRef
    profile: NodeWorkloadProfile
    document_bytes: bytes
    files: Mapping[str, bytes]

    def __post_init__(self) -> None:
        if not isinstance(self.ref, ProfileRef):
            raise TypeError("ref must be a ProfileRef")
        if not isinstance(self.profile, NodeWorkloadProfile):
            raise TypeError("profile must be an admitted NodeWorkloadProfile")
        if not isinstance(self.document_bytes, bytes):
            raise TypeError("document_bytes must be bytes")
        if self.profile != _derive_profile(self.document_bytes, self.ref):
            raise ValueError(
                f"profile model for {self.ref} does not derive from the retained document bytes"
            )
        frozen = MappingProxyType(dict(self.files))
        object.__setattr__(self, "files", frozen)
        for path, content in frozen.items():
            if not isinstance(content, bytes):
                raise TypeError(f"package file {path!r} must be bytes")
        declared = {
            artifact.file: f"sha256:{artifact.sha256}" for artifact in self.profile.artifacts.static
        }
        observed = {path: sha256_digest(content) for path, content in frozen.items()}
        if observed != declared:
            raise ValueError(
                "loaded package files must match the profile's declarations exactly "
                f"(declared={sorted(declared)}, loaded={sorted(observed)})"
            )


@dataclass(frozen=True, slots=True)
class LoadedPackage:
    """The admitted binding, its profiles, and the one selection digest.

    Immutable by construction: the admitted binding must equal the model the
    retained bytes derive, the profile mapping is defensively copied and
    frozen, keys equal each profile's own reference, and the profile set
    equals exactly the binding's referenced set. The package digest is
    computed here, from the loaded bytes, and nowhere else.
    """

    binding_ref: ImplementationBindingRef
    binding: ImplementationBinding
    document_bytes: bytes
    profiles: Mapping[str, LoadedProfile] = field(hash=False)
    package_digest: str = field(init=False)

    def __post_init__(self) -> None:
        if not isinstance(self.binding_ref, ImplementationBindingRef):
            raise TypeError("binding_ref must be an ImplementationBindingRef")
        if not isinstance(self.binding, ImplementationBinding):
            raise TypeError("binding must be an admitted ImplementationBinding")
        if not isinstance(self.document_bytes, bytes):
            raise TypeError("document_bytes must be bytes")
        if self.binding != _derive_binding(self.document_bytes, self.binding_ref):
            raise ValueError(
                f"binding model for {self.binding_ref} does not derive from the "
                "retained document bytes"
            )
        frozen = MappingProxyType(dict(self.profiles))
        object.__setattr__(self, "profiles", frozen)
        for key, loaded in frozen.items():
            if not isinstance(loaded, LoadedProfile):
                raise TypeError("profiles must map to LoadedProfile records")
            if key != str(loaded.ref):
                raise ValueError(
                    f"profile key {key!r} must equal its record's reference {loaded.ref}"
                )
        referenced = {str(entry.profile) for entry in self.binding.entries}
        if set(frozen) != referenced:
            missing = sorted(referenced.difference(frozen))
            extra = sorted(set(frozen).difference(referenced))
            raise ValueError(
                "package profiles must equal the binding's referenced set exactly "
                f"(missing={missing}, extra={extra})"
            )
        canonical = {
            "binding": {
                "ref": str(self.binding_ref),
                "sha256": sha256_digest(self.document_bytes),
            },
            "profiles": [
                {
                    "ref": key,
                    "sha256": sha256_digest(frozen[key].document_bytes),
                    "files": [
                        {"path": path, "sha256": sha256_digest(frozen[key].files[path])}
                        for path in sorted(frozen[key].files)
                    ],
                }
                for key in sorted(frozen)
            ],
        }
        object.__setattr__(self, "package_digest", sha256_digest(canonical_json_bytes(canonical)))
        if not re.fullmatch(SHA256_DIGEST_PATTERN, self.package_digest):
            raise ValueError("package digest must be sha256:<64 lowercase hex>")


class WorkloadPackageSource(Protocol):
    """Serve one explicit binding reference as one immutable loaded package."""

    def load(self, binding_ref: ImplementationBindingRef) -> LoadedPackage: ...


class DirectoryPackageSource:
    """Serve packages from one explicitly configured directory root.

    The root is handed in whole; nothing is inferred by scanning. Documents
    live under their family directories (``bindings/``, ``profiles/``) and a
    profile's declared files resolve relative to the profile document's own
    directory.
    """

    def __init__(self, root: Path, *, namespace: CatalogNamespace = "nodalarc") -> None:
        self._root = root
        self._namespace = namespace

    def _contained_read(self, relative: Path, *, what: str, missing_code: PackageLoadCode) -> bytes:
        """The one read path: every byte served must resolve inside the root."""
        try:
            resolved_root = self._root.resolve()
            resolved_target = (self._root / relative).resolve()
        except OSError as error:
            raise PackageLoadError(
                missing_code,
                f"{what} {relative.as_posix()!r} is unreadable: {error}",
            ) from error
        if not resolved_target.is_relative_to(resolved_root):
            raise PackageLoadError(
                PackageLoadCode.PACKAGE_PATH_ESCAPE,
                f"{what} {relative.as_posix()!r} escapes the configured package root",
            )
        try:
            return resolved_target.read_bytes()
        except FileNotFoundError:
            raise PackageLoadError(
                missing_code,
                f"{what} {relative.as_posix()!r} does not exist in this package source",
            ) from None
        except OSError as error:
            raise PackageLoadError(
                missing_code,
                f"{what} {relative.as_posix()!r} is unreadable: {error}",
            ) from error

    def _decode(self, content: bytes, *, what: str) -> object:
        try:
            return load_configuration_yaml(content)
        except (yaml.YAMLError, UnicodeError, ValueError, TypeError) as error:
            raise PackageLoadError(
                PackageLoadCode.PACKAGE_DOCUMENT_INVALID,
                f"{what} is not valid YAML: {error}",
            ) from error

    def _load_profile(self, ref: ProfileRef) -> LoadedProfile:
        if ref.namespace != self._namespace:
            raise PackageLoadError(
                PackageLoadCode.PACKAGE_REF_INVALID,
                f"profile reference {ref} is outside this source's {self._namespace!r} namespace",
            )
        document_bytes = self._contained_read(
            ref.relative_path,
            what="profile document",
            missing_code=PackageLoadCode.PACKAGE_DOCUMENT_MISSING,
        )
        document = self._decode(document_bytes, what=f"profile document {ref}")
        admission = admit_profile(document, object_ref=str(ref))
        if admission.profile is None:
            raise PackageLoadError(
                PackageLoadCode.PACKAGE_DOCUMENT_INVALID,
                f"profile document {ref} was rejected by admission",
                rejections=admission.rejections,
            )
        profile = admission.profile
        files: dict[str, bytes] = {}
        profile_dir = ref.relative_path.parent
        for artifact in profile.artifacts.static:
            content = self._contained_read(
                profile_dir / artifact.file,
                what=f"profile {ref} file",
                missing_code=PackageLoadCode.PACKAGE_FILE_MISSING,
            )
            observed = sha256_digest(content)
            if observed != f"sha256:{artifact.sha256}":
                raise PackageLoadError(
                    PackageLoadCode.PACKAGE_FILE_MISMATCH,
                    f"profile {ref} file {artifact.file!r} digest {observed} does not "
                    f"match its declaration sha256:{artifact.sha256}",
                )
            files[artifact.file] = content
        return LoadedProfile(
            ref=ref,
            profile=profile,
            document_bytes=document_bytes,
            files=files,
        )

    def load(self, binding_ref: ImplementationBindingRef) -> LoadedPackage:
        if not isinstance(binding_ref, ImplementationBindingRef):
            raise PackageLoadError(
                PackageLoadCode.PACKAGE_REF_INVALID,
                f"binding reference {binding_ref!r} is not a typed binding reference",
            )
        if binding_ref.namespace != self._namespace:
            raise PackageLoadError(
                PackageLoadCode.PACKAGE_REF_INVALID,
                f"binding reference {binding_ref} is outside this source's "
                f"{self._namespace!r} namespace",
            )
        document_bytes = self._contained_read(
            binding_ref.relative_path,
            what="binding document",
            missing_code=PackageLoadCode.PACKAGE_DOCUMENT_MISSING,
        )
        document = self._decode(document_bytes, what=f"binding document {binding_ref}")
        admission = admit_binding(document, object_ref=str(binding_ref))
        if admission.binding is None:
            raise PackageLoadError(
                PackageLoadCode.PACKAGE_DOCUMENT_INVALID,
                f"binding document {binding_ref} was rejected by admission",
                rejections=admission.rejections,
            )
        binding = admission.binding
        profiles: dict[str, LoadedProfile] = {}
        for entry in binding.entries:
            key = str(entry.profile)
            if key not in profiles:
                profiles[key] = self._load_profile(entry.profile)
        return LoadedPackage(
            binding_ref=binding_ref,
            binding=binding,
            document_bytes=document_bytes,
            profiles=profiles,
        )
