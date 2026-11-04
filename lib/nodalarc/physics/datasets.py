# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""Locating, verifying and converting the ephemeris data files.

ANISE reads NAIF SPK and binary PCK files directly, but reads NAIF text frame
kernels only after converting them to its own dataset format. The conversion
runs once, at image build time (``python -m nodalarc.physics``), so
runtime loading is local and reproducible. A manifest next to the converted
files records each source file's SHA-256 and the ANISE version that converted
it; a converted file whose source pin or converter version does not match is
refused, because the converted format belongs to the ANISE release that wrote
it.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from functools import lru_cache
from importlib.metadata import version
from pathlib import Path

from nodalarc.physics.ephemeris_table import FILES, EphemerisFile
from nodalarc.physics.errors import PhysicsDataError

MANIFEST_NAME = "manifest.json"


@dataclass(frozen=True, slots=True)
class EphemerisLocation:
    """Where the source files and the converted files live."""

    source_dir: Path
    converted_dir: Path


def production_location() -> EphemerisLocation:
    """The repository's ``configs/ephemerides`` and its converted subdirectory.

    Images copy ``configs/ephemerides/`` to ``/app/configs/ephemerides`` and
    convert into ``anise/`` beside it at build time; a source checkout runs
    the same conversion before use.
    """
    here = Path(__file__).resolve().parent
    for candidate in (here, *here.parents):
        source = candidate / "configs" / "ephemerides"
        if source.is_dir():
            return EphemerisLocation(source_dir=source, converted_dir=source / "anise")
    raise PhysicsDataError("the platform's ephemeris data directory configs/ephemerides is missing")


def _anise_version() -> str:
    return version("anise")


@lru_cache(maxsize=64)
def _file_sha256(path: Path, size: int, mtime_ns: int) -> str:
    # Keyed on size and modification time as well as path: a file replaced
    # after the first check is hashed again. Hashing de440s.bsp alone reads
    # 32 MB, and Builder previews build a context per request.
    del size, mtime_ns
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verified_path(directory: Path, filename: str, expected_sha256: str, *, label: str) -> Path:
    """The path of a data file whose content matches its pin, or a refusal."""
    # Refusals name the data file and never the host path or the operating
    # system's text; the chained cause keeps both for the server log.
    path = directory / filename
    try:
        stat = path.stat()
    except FileNotFoundError as exc:
        raise PhysicsDataError(f"ephemeris file {label!r} is missing: {filename}") from exc
    except OSError as exc:
        raise PhysicsDataError(
            f"ephemeris file {label!r} could not be read: {filename} ({type(exc).__name__})"
        ) from exc
    try:
        actual = _file_sha256(path, stat.st_size, stat.st_mtime_ns)
    except OSError as exc:
        raise PhysicsDataError(
            f"ephemeris file {label!r} could not be read: {filename} ({type(exc).__name__})"
        ) from exc
    if actual != expected_sha256:
        raise PhysicsDataError(
            f"ephemeris file {label!r} ({filename}) does not match its pin: "
            f"expected sha256 {expected_sha256}, found {actual}"
        )
    return path


def converted_filename(entry: EphemerisFile) -> str:
    """Frame kernels convert to ANISE ``.epa`` files, planetary constants to ``.pca`` files."""
    suffix = ".pca" if entry.kind == "tpc" else ".epa"
    return f"{Path(entry.filename).stem}{suffix}"


def convert_datasets(location: EphemerisLocation) -> None:
    """Convert every frame kernel and planetary-constants file in the table and write the
    manifest. Planetary constants convert together with the table's one gravity file, which
    ANISE's conversion requires."""
    from anise.utils import convert_fk, convert_tpc

    location.converted_dir.mkdir(parents=True, exist_ok=True)
    outputs: dict[str, dict[str, str]] = {}
    (gravity,) = (entry for entry in FILES.values() if entry.kind == "gm")
    for entry in FILES.values():
        if entry.kind not in ("fk", "tpc"):
            continue
        source = verified_path(
            location.source_dir, entry.filename, entry.sha256, label=entry.file_id
        )
        target = location.converted_dir / converted_filename(entry)
        if entry.kind == "fk":
            convert_fk(str(source), str(target), False, True)
        else:
            gm = verified_path(
                location.source_dir, gravity.filename, gravity.sha256, label=gravity.file_id
            )
            convert_tpc(str(source), str(gm), str(target), True)
        outputs[entry.file_id] = {
            "source_sha256": entry.sha256,
            "output": target.name,
            "output_sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
        }
    manifest = {"anise_version": _anise_version(), "outputs": outputs}
    (location.converted_dir / MANIFEST_NAME).write_text(json.dumps(manifest, indent=2) + "\n")


def converted_path(location: EphemerisLocation, entry: EphemerisFile) -> Path:
    """The converted dataset for a frame kernel, checked against the manifest."""
    manifest_path = location.converted_dir / MANIFEST_NAME
    try:
        manifest = json.loads(manifest_path.read_text())
    except FileNotFoundError as exc:
        raise PhysicsDataError(
            f"converted ephemeris datasets are missing in {location.converted_dir}; "
            "run python -m nodalarc.physics"
        ) from exc
    if manifest.get("anise_version") != _anise_version():
        raise PhysicsDataError(
            f"converted ephemeris datasets were written by anise {manifest.get('anise_version')!r} "
            f"and the installed anise is {_anise_version()!r}; convert them again"
        )
    record = manifest.get("outputs", {}).get(entry.file_id)
    if record is None or record.get("source_sha256") != entry.sha256:
        raise PhysicsDataError(
            f"converted dataset for {entry.file_id!r} does not come from the pinned source "
            f"{entry.filename}; convert the datasets again"
        )
    return verified_path(
        location.converted_dir,
        record["output"],
        record["output_sha256"],
        label=f"{entry.file_id} (converted)",
    )
