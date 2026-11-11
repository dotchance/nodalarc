# Copyright 2024-2026 .chance (dotchance)
# SPDX-License-Identifier: AGPL-3.0-only
# Additional terms under AGPL-3.0 Section 7 are in the NOTICE file.
"""Decide, for each release image, whether a release builds it or reuses the published one.

Usage: na-release-plan.py PREVIOUS_LIST NAME [NAME ...]

PREVIOUS_LIST is the release list (YAML) of the newest published release, or an
empty path for none. A component is reused when the previous release has it at
the same version (its VERSION file, build metadata aside) and its input files
are the same at the commit that version was built from as now. It is built when
its version is new. When its version is the same and its files changed, the
release stops: the component needs a new version.

Prints one line per component: NAME, then reuse VERSION IMAGE, or build, or stop
REASON, separated by tabs. Exits 1 when any component stops.
"""

from __future__ import annotations

import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class Decision:
    name: str
    action: str  # reuse, build or stop
    version: str = ""
    image: str = ""
    reason: str = ""


def decide(
    name: str,
    base_version: str,
    previous: dict | None,
    input_digest: Callable[[str, str], str],
    version_file: str,
) -> Decision:
    """The decision for one component; input_digest(name, commit) hashes its inputs."""
    if previous is None or "image" not in previous:
        return Decision(name, "build")
    published_base, _, commit = previous["version"].partition("+")
    if published_base != base_version:
        return Decision(name, "build")
    if not commit:
        return Decision(
            name, "stop", reason=f"published version {previous['version']} names no commit"
        )
    if input_digest(name, commit) != input_digest(name, "HEAD"):
        return Decision(
            name,
            "stop",
            reason=(
                f"its files changed since {previous['version']}, and {version_file} "
                f"still says {base_version}; give it a new version"
            ),
        )
    return Decision(name, "reuse", version=previous["version"], image=previous["image"])


def _images_sh(*arguments: str) -> str:
    result = subprocess.run(
        ["bash", str(ROOT / "scripts" / "na-images.sh"), *arguments],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise SystemExit(f"na-images.sh {' '.join(arguments)}: {result.stderr.strip()}")
    return result.stdout.strip()


def main() -> int:
    if len(sys.argv) < 3:
        print("usage: na-release-plan.py PREVIOUS_LIST NAME [NAME ...]", file=sys.stderr)
        return 2
    previous_path, names = sys.argv[1], sys.argv[2:]
    components: dict = {}
    if previous_path:
        components = (yaml.safe_load(Path(previous_path).read_text()) or {}).get("components", {})
    digests: dict[tuple[str, str], str] = {}

    def input_digest(name: str, commit: str) -> str:
        key = (name, commit)
        if key not in digests:
            digests[key] = _images_sh("component-input-digest", name, commit)
        return digests[key]

    stopped = False
    for name in names:
        base = _images_sh("component-version", name).partition("+")[0]
        version_file = _images_sh("component-version-file", name)
        decision = decide(name, base, components.get(name), input_digest, version_file)
        fields = [name, decision.action]
        if decision.action == "reuse":
            fields += [decision.version, decision.image]
        elif decision.action == "stop":
            fields.append(decision.reason)
            stopped = True
        print("\t".join(fields))
    return 1 if stopped else 0


if __name__ == "__main__":
    sys.exit(main())
