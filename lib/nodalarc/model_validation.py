# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""Shared scalar and collection validators for configuration models.

A selector or override list that is empty, contains duplicates, or holds an
invalid index is a "valid object that does nothing" — it silently matches
nothing or encodes ambiguous intent. Under the no-fallback rule these must fail
at parse time, not become no-op behavior the resolver has to interpret. Use
these as Pydantic ``AfterValidator``s on the field type.
"""

from __future__ import annotations

import ipaddress
import unicodedata
from datetime import datetime
from pathlib import PurePosixPath, PureWindowsPath
from typing import Annotated, Any, Literal

from pydantic import AfterValidator, BeforeValidator, Field, WithJsonSchema

# Generic primitives for strings that encode identity/reference rather than prose.
# Descriptions, notes, and labels may stay plain strings; node/station/terminal/path
# references must be present and cannot be whitespace.
NonEmptyString = Annotated[str, Field(min_length=1, pattern=r".*\S.*")]
NonEmptyReference = Annotated[str, Field(min_length=1, pattern=r"^\S+$")]
Identifier = Annotated[str, Field(pattern=r"^[a-z0-9][a-z0-9_-]*$")]
RuntimeNodeId = Annotated[str, Field(pattern=r"^[a-z0-9][a-z0-9-]*$")]
TerminalMedium = Literal["rf", "optical"]


def _configuration_integer(value: Any) -> Any:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("must be a YAML integer")
    return value


StrictInteger = Annotated[int, BeforeValidator(_configuration_integer)]
PositiveInteger = Annotated[StrictInteger, Field(gt=0)]
NonNegativeInteger = Annotated[StrictInteger, Field(ge=0)]


def _configuration_boolean(value: Any) -> Any:
    if not isinstance(value, bool):
        raise ValueError("must be a YAML boolean")
    return value


StrictBoolean = Annotated[bool, BeforeValidator(_configuration_boolean)]


def _configuration_number(value: Any) -> Any:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError("must be a YAML number")
    return value


FiniteFloat = Annotated[
    float,
    BeforeValidator(_configuration_number),
    Field(allow_inf_nan=False),
]
PositiveFiniteFloat = Annotated[
    float,
    BeforeValidator(_configuration_number),
    Field(gt=0, allow_inf_nan=False),
]
NonNegativeFiniteFloat = Annotated[
    float,
    BeforeValidator(_configuration_number),
    Field(ge=0, allow_inf_nan=False),
]


def _network(value: str, *, version: int) -> str:
    try:
        parsed = ipaddress.ip_network(value, strict=True)
    except ValueError as exc:
        raise ValueError(f"must be a canonical IPv{version} network in CIDR notation") from exc
    if parsed.version != version:
        raise ValueError(f"must be an IPv{version} network")
    return str(parsed)


def _interface(value: str, *, version: int) -> str:
    try:
        parsed = ipaddress.ip_interface(value)
    except ValueError as exc:
        raise ValueError(f"must be an IPv{version} interface in CIDR notation") from exc
    if parsed.version != version:
        raise ValueError(f"must be an IPv{version} interface")
    return str(parsed)


def _ipv4_network(value: str) -> str:
    return _network(value, version=4)


def _ipv6_network(value: str) -> str:
    return _network(value, version=6)


def _ipv4_interface(value: str) -> str:
    return _interface(value, version=4)


def _ipv6_interface(value: str) -> str:
    return _interface(value, version=6)


def _aware_timestamp(value: str) -> str:
    candidate = f"{value[:-1]}+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError as exc:
        raise ValueError("must be an ISO 8601 timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("must include an explicit UTC offset")
    return value


def _relative_asset_path(value: str) -> str:
    if "\\" in value:
        raise ValueError("must use forward-slash path separators")
    if value.startswith("/") or PureWindowsPath(value).is_absolute():
        raise ValueError("must be a relative path")
    if value.endswith("/"):
        raise ValueError("must not end with a path separator")
    if "//" in value:
        raise ValueError("must not contain repeated path separators")
    parts = value.split("/")
    if not parts or any(part in {"", ".", ".."} for part in parts):
        raise ValueError("must be a contained relative path")
    if PurePosixPath(value).as_posix() != value:
        raise ValueError("must be a canonical relative path")
    return value


Ipv4Network = Annotated[
    str,
    AfterValidator(_ipv4_network),
    WithJsonSchema({"type": "string", "format": "ipv4-network"}),
]
Ipv6Network = Annotated[
    str,
    AfterValidator(_ipv6_network),
    WithJsonSchema({"type": "string", "format": "ipv6-network"}),
]
Ipv4Interface = Annotated[
    str,
    AfterValidator(_ipv4_interface),
    WithJsonSchema({"type": "string", "format": "ipv4-interface"}),
]
Ipv6Interface = Annotated[
    str,
    AfterValidator(_ipv6_interface),
    WithJsonSchema({"type": "string", "format": "ipv6-interface"}),
]
AwareTimestamp = Annotated[
    str,
    AfterValidator(_aware_timestamp),
    WithJsonSchema({"type": "string", "format": "date-time"}),
]
Sha256Hex = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
RelativeAssetPath = Annotated[
    str,
    Field(min_length=1),
    AfterValidator(_relative_asset_path),
    WithJsonSchema({"type": "string", "format": "relative-path"}),
]

# Container trees the platform reserves; a profile mount may not land inside them.
_RESERVED_MOUNT_TREES = ("/proc", "/sys", "/dev", "/var/run/secrets")


def _mount_path(value: str) -> str:
    if "\x00" in value:
        raise ValueError("mount path must not contain NUL")
    if not value.startswith("/") or value == "/":
        raise ValueError(f"mount path must be absolute and not '/': {value!r}")
    if value.endswith("/"):
        raise ValueError(f"mount path must not end with '/': {value!r}")
    if "//" in value:
        raise ValueError(f"mount path must not contain '//': {value!r}")
    if not unicodedata.is_normalized("NFC", value):
        raise ValueError(f"mount path must be NFC-normalized: {value!r}")
    if any(segment in {".", ".."} for segment in value.split("/")[1:]):
        raise ValueError(f"mount path must not contain '.' or '..' segments: {value!r}")
    for reserved in _RESERVED_MOUNT_TREES:
        if value == reserved or value.startswith(f"{reserved}/"):
            raise ValueError(f"mount path is under a reserved tree: {value!r}")
    return value


MountPath = Annotated[
    str,
    Field(min_length=2),
    AfterValidator(_mount_path),
    WithJsonSchema({"type": "string", "format": "mount-path"}),
]
PinnedImage = Annotated[str, Field(pattern=r"^[a-z0-9][a-z0-9._/-]*@sha256:[0-9a-f]{64}$")]
RegistryHost = Annotated[str, Field(pattern=r"^[a-z0-9]([a-z0-9.-]*[a-z0-9])?(:[0-9]{1,5})?$")]
EnvName = Annotated[str, Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")]

# An Ethernet segment id doubles as a kernel interface name (IFNAMSIZ
# bounds usable names at 15 bytes).
SegmentId = Annotated[str, Field(pattern=r"^[a-z0-9][a-z0-9_-]*$", max_length=15)]

# One symbolic routing-origination entry: a declared segment id, or the
# default route.
OriginationTarget = Annotated[str, Field(pattern=r"^(default|[a-z0-9][a-z0-9_-]*)$", max_length=15)]


def nonempty(values: Any) -> Any:
    """A present sequence must be non-empty (``None`` is allowed = filter absent)."""
    if values is not None and len(values) == 0:
        raise ValueError("must not be empty")
    return values


def nonempty_unique(values: Any) -> Any:
    """A present sequence must be non-empty and free of duplicates."""
    if values is None:
        return values
    if len(values) == 0:
        raise ValueError("must not be empty")
    seen: set = set()
    dups: list = []
    for value in values:
        if value in seen:
            dups.append(value)
        seen.add(value)
    if dups:
        raise ValueError(f"must not contain duplicate entries: {dups}")
    return values
