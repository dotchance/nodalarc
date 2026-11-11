# Copyright 2024-2026 .chance (dotchance)
# SPDX-License-Identifier: AGPL-3.0-only
# Additional terms under AGPL-3.0 Section 7 are in the NOTICE file.
"""Write a release chart's values.yaml from the chart's source values and a release record.

Usage: na-release-values.py SOURCE_VALUES RECORD_JSON OUTPUT_VALUES

The source values.yaml stays the one place the defaults and their comments
live; this edits only the keys a release fills (every image reference, the
workload image map, the release number) and refuses a source that no longer
has them in the expected form, so a change to values.yaml cannot silently
leave a release value unset.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

# Release record component name -> chart key under images:.
PLATFORM_KEYS = {
    "base": "base",
    "ome": "ome",
    "vf": "vf",
    "scheduler": "scheduler",
    "node-agent": "nodeAgent",
    "vs-api": "vsApi",
    "operator": "operator",
}
THIRD_PARTY_KEYS = {"nats": "nats", "nats-box": "natsBox"}
LOGICAL = "registry.example/nodalarc/{name}@sha256:" + "0" * 64


def fail(message: str) -> None:
    print(f"[release-values] ERROR: {message}", file=sys.stderr)
    sys.exit(1)


def replace_once(text: str, pattern: str, replacement: str) -> str:
    matches = re.findall(pattern, text, flags=re.MULTILINE)
    if len(matches) != 1:
        fail(f"values.yaml has {len(matches)} lines matching {pattern!r}; expected exactly one")
    return re.sub(pattern, lambda _match: replacement, text, count=1, flags=re.MULTILINE)


def main() -> None:
    if len(sys.argv) != 4:
        fail("usage: na-release-values.py SOURCE_VALUES RECORD_JSON OUTPUT_VALUES")
    source, record_path, output = (Path(argument) for argument in sys.argv[1:])
    record = json.loads(record_path.read_text())
    release = record["release"]
    components = record["components"]
    third_party = record["third_party"]

    text = source.read_text()
    for name, key in PLATFORM_KEYS.items():
        text = replace_once(text, rf"^  {key}: .*$", f"  {key}: {components[name]['image']}")
    operator_line = f"  operator: {components['operator']['image']}"
    third_party_lines = "".join(
        f"\n  {key}: {third_party[name]['image']}" for name, key in THIRD_PARTY_KEYS.items()
    )
    text = replace_once(text, rf"^{re.escape(operator_line)}$", operator_line + third_party_lines)

    session = {
        LOGICAL.format(name=name): components[name]["image"] for name in record["session_images"]
    }
    if session[LOGICAL.format(name="base")] != components["base"]["image"]:
        fail("the workload image map and images.base name different base images")
    map_json = json.dumps(session, sort_keys=True, separators=(",", ":"))
    text = replace_once(text, r'^workloadImages: ""$', f"workloadImages: '{map_json}'")
    text = replace_once(
        text,
        r'^buildTag: "dev"$',
        f'buildTag: "{release}"\n# The release this chart installs, written into each session\'s status.\n'
        f'runtimeRelease: "{release}"',
    )
    output.write_text(text)


if __name__ == "__main__":
    main()
