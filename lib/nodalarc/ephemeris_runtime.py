# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""Runtime ephemeris provider for common-frame body positions.

The session grammar owns the manifest. This module owns the runtime contract:
local files only, checksum verified, coverage checked, and no network fetch.
The first supported provider is a Skyfield/JPL BSP kernel. Earth is the common
frame origin; other body states are returned as Earth-relative GCRS vectors.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from nodalarc.frames import Vec3
from nodalarc.models.ephemeris import EphemerisConfig, EphemerisKernel

if TYPE_CHECKING:
    from nodalarc.models.resolved_session import ResolvedEphemeris
    from nodalarc.models.segment_session import TimeConfig


@dataclass(frozen=True)
class CommonBodyState:
    """A body origin in the session common frame."""

    body_id: str
    position_km: Vec3
    velocity_km_s: Vec3
    provider: str
    kernel_id: str
    quality_tier: str
    frame: str


class EphemerisValidationError(ValueError):
    """Raised when an ephemeris manifest cannot support the requested session."""


def session_epoch_unix(time_cfg: TimeConfig | None) -> float:
    """The single owner of catalog-session epoch derivation.

    Requires a declared, timezone-aware ``start_time``. There is no naive-
    datetime interpretation and no wall-clock fallback: an epoch that depends
    on the container timezone or process start moment is not a session fact.
    """
    if time_cfg is None:
        raise EphemerisValidationError("catalog session time with start_time is required")
    raw = time_cfg.start_time
    epoch = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    if epoch.tzinfo is None:
        raise EphemerisValidationError(f"catalog session start_time must include timezone: {raw!r}")
    return epoch.timestamp()


def runtime_config_from_resolved(ephemeris: ResolvedEphemeris) -> EphemerisConfig:
    """Convert a resolved ephemeris manifest into the runtime provider config.

    Single owner of the resolved-to-runtime manifest mapping; the resolver and
    OME input construction both call this so they can never disagree.
    """
    kernels: list[EphemerisKernel] = []
    for kernel in ephemeris.kernels:
        if kernel.sha256 is None:
            raise EphemerisValidationError(f"ephemeris kernel {kernel.id!r} requires sha256")
        if kernel.coverage_start is None or kernel.coverage_end is None:
            raise EphemerisValidationError(
                f"ephemeris kernel {kernel.id!r} requires coverage_start and coverage_end"
            )
        kernels.append(
            EphemerisKernel(
                id=kernel.id,
                path=kernel.path,
                checksum=kernel.sha256,
                targets=list(kernel.targets),
                frame=kernel.frame,
                coverage_start=datetime.fromisoformat(kernel.coverage_start.replace("Z", "+00:00")),
                coverage_end=datetime.fromisoformat(kernel.coverage_end.replace("Z", "+00:00")),
            )
        )
    return EphemerisConfig(
        provider=ephemeris.provider,
        quality_tier=ephemeris.quality_tier,
        kernels=kernels,
    )


def _repo_root() -> Path:
    p = Path(__file__).resolve().parent
    for _ in range(10):
        if (p / "configs").is_dir():
            return p
        p = p.parent
    raise EphemerisValidationError("cannot find repository root for ephemeris path resolution")


def _resolve_local_path(path: str, *, base_dir: Path | None = None) -> Path:
    raw = Path(path)
    if path.startswith(("http://", "https://")):
        raise EphemerisValidationError(
            f"ephemeris kernel path {path!r} is not local; runtime fetch is forbidden"
        )
    resolved = raw if raw.is_absolute() else (base_dir or _repo_root()) / raw
    try:
        return resolved.resolve(strict=True)
    except FileNotFoundError as exc:
        raise EphemerisValidationError(f"ephemeris kernel file does not exist: {path}") from exc


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _expected_checksum(value: str) -> str:
    expected = value.removeprefix("sha256:").strip().lower()
    if len(expected) != 64 or any(ch not in "0123456789abcdef" for ch in expected):
        raise EphemerisValidationError(
            "ephemeris kernel checksum must be a sha256 hex digest or sha256:<digest>"
        )
    return expected


def _epoch_datetime(epoch_unix: float) -> datetime:
    return datetime.fromtimestamp(epoch_unix, UTC)


def validate_ephemeris_manifest(
    config: EphemerisConfig,
    *,
    required_bodies: set[str],
    epoch_unix: float,
    end_epoch_unix: float | None = None,
    base_dir: Path | None = None,
) -> dict[str, Path]:
    """Validate a local ephemeris manifest and return kernel paths by ID."""

    if config.provider != "skyfield_bsp":
        raise EphemerisValidationError(
            f"ephemeris provider {config.provider!r} is structurally valid but not runtime-supported"
        )
    if len(config.kernels) != 1:
        raise EphemerisValidationError(
            "skyfield_bsp runtime support currently requires exactly one kernel; "
            "multi-kernel stacks are a future ephemeris capability"
        )

    epoch = _epoch_datetime(epoch_unix)
    end_epoch = _epoch_datetime(end_epoch_unix) if end_epoch_unix is not None else epoch
    if end_epoch < epoch:
        raise EphemerisValidationError(
            "ephemeris validation end_epoch_unix must be greater than or equal to epoch_unix"
        )
    paths: dict[str, Path] = {}
    available_bodies: set[str] = {"earth"}
    for kernel in config.kernels:
        path = _resolve_local_path(kernel.path, base_dir=base_dir)
        expected = _expected_checksum(kernel.checksum)
        actual = _sha256(path)
        if actual != expected:
            raise EphemerisValidationError(
                f"ephemeris kernel {kernel.id!r} checksum mismatch: expected {expected}, got {actual}"
            )
        if not (kernel.coverage_start <= epoch and end_epoch <= kernel.coverage_end):
            raise EphemerisValidationError(
                f"ephemeris kernel {kernel.id!r} does not cover required session window "
                f"{epoch.isoformat()}..{end_epoch.isoformat()}; coverage is "
                f"{kernel.coverage_start.isoformat()}..{kernel.coverage_end.isoformat()}"
            )
        paths[kernel.id] = path
        available_bodies.update(str(body) for body in kernel.targets)

    missing = sorted(required_bodies - available_bodies)
    if missing:
        raise EphemerisValidationError(
            "ephemeris manifest is missing required body target(s): " + ", ".join(missing)
        )
    return paths


class SkyfieldBspEphemeris:
    """Earth-relative common-frame ephemeris from a local JPL BSP file."""

    def __init__(
        self,
        config: EphemerisConfig,
        *,
        kernel_paths: dict[str, Path],
    ) -> None:
        if config.provider != "skyfield_bsp":
            raise EphemerisValidationError(f"unsupported ephemeris provider {config.provider!r}")
        if len(config.kernels) != 1:
            raise EphemerisValidationError(
                "skyfield_bsp runtime support currently requires exactly one kernel; "
                "multi-kernel stacks are a future ephemeris capability"
            )
        if not kernel_paths:
            raise EphemerisValidationError("skyfield_bsp ephemeris requires at least one kernel")
        from skyfield.api import load, load_file

        self._config = config
        self._kernel = config.kernels[0]
        self._path = kernel_paths[self._kernel.id]
        self._eph = load_file(str(self._path))
        self._ts = load.timescale()

    @classmethod
    def from_config(
        cls,
        config: EphemerisConfig,
        *,
        required_bodies: set[str],
        epoch_unix: float,
        end_epoch_unix: float | None = None,
        base_dir: Path | None = None,
    ) -> SkyfieldBspEphemeris:
        paths = validate_ephemeris_manifest(
            config,
            required_bodies=required_bodies,
            epoch_unix=epoch_unix,
            end_epoch_unix=end_epoch_unix,
            base_dir=base_dir,
        )
        return cls(config, kernel_paths=paths)

    def body_state(self, body_id: str, unix_timestamp: float) -> CommonBodyState:
        if body_id == "earth":
            return CommonBodyState(
                body_id="earth",
                position_km=Vec3(0.0, 0.0, 0.0),
                velocity_km_s=Vec3(0.0, 0.0, 0.0),
                provider=self._config.provider,
                kernel_id=self._kernel.id,
                quality_tier=self._config.quality_tier,
                frame=self._kernel.frame,
            )
        target = _skyfield_body_key(body_id)
        t = self._ts.from_datetime(datetime.fromtimestamp(unix_timestamp, UTC))
        try:
            earth = self._eph["earth"]
            body = self._eph[target]
        except KeyError as exc:
            raise EphemerisValidationError(
                f"ephemeris kernel {self._kernel.id!r} has no target for body {body_id!r}"
            ) from exc
        vector = body.at(t) - earth.at(t)
        return CommonBodyState(
            body_id=body_id,
            position_km=Vec3(*vector.position.km),
            velocity_km_s=Vec3(*vector.velocity.km_per_s),
            provider=self._config.provider,
            kernel_id=self._kernel.id,
            quality_tier=self._config.quality_tier,
            frame=self._kernel.frame,
        )


def _skyfield_body_key(body_id: str) -> str:
    if body_id == "luna":
        return "moon"
    if body_id == "mars":
        return "mars barycenter"
    raise EphemerisValidationError(f"unsupported ephemeris body {body_id!r}")


def body_states_at(
    provider: SkyfieldBspEphemeris | None,
    bodies: set[str],
    unix_timestamp: float,
) -> dict[str, CommonBodyState]:
    """Return common-frame states for the requested bodies."""
    if "earth" in bodies:
        states = {
            "earth": CommonBodyState(
                body_id="earth",
                position_km=Vec3(0.0, 0.0, 0.0),
                velocity_km_s=Vec3(0.0, 0.0, 0.0),
                provider="none",
                kernel_id="earth-origin",
                quality_tier="analytic",
                frame="gcrs-earth-origin",
            )
        }
    else:
        states = {}
    required = set(bodies) - {"earth"}
    if required and provider is None:
        raise EphemerisValidationError(
            "non-Earth body states require a validated ephemeris provider: "
            + ", ".join(sorted(required))
        )
    if provider is not None:
        for body in sorted(required):
            states[body] = provider.body_state(body, unix_timestamp)
    return states
