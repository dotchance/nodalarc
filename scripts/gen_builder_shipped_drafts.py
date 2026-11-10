# Copyright 2024-2026 .chance (dotchance)
# SPDX-License-Identifier: AGPL-3.0-only
# Additional terms under AGPL-3.0 Section 7 are in the NOTICE file.
"""Write what the Builder receives when it opens and compiles each shipped session.

For every session in catalog/nodalarc/sessions the Builder's open and compile run in-process,
the way VS-API runs them, with no cluster. Each session gets one JSON file holding the parts of
the compile result the page's anatomy guide reads: the canonical session document and the
backend's resolved world for it (its session, nodes, segments and link rules; the ephemeris,
allocations and rule previews are left out). The frontend tests render the Builder's overview
from these documents and check what the page claims against what the backend resolved.

    uv run python scripts/gen_builder_shipped_drafts.py          # write
    uv run python scripts/gen_builder_shipped_drafts.py --check  # exit 1 when stale
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# The repository root carries the adapter packages that session resolution
# reads; lib and services take precedence over it.
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "lib"))
sys.path.insert(0, str(ROOT / "services"))

from nodalarc.catalog_repository import CatalogScope  # noqa: E402
from nodalarc.filesystem_catalog_repository import FilesystemCatalogRepository  # noqa: E402
from nodalarc.models.builder_visual_api import (  # noqa: E402
    BuilderVisualDraftCompileRequest,
    BuilderVisualDraftOpenRequest,
)
from nodalarc.platform_config import init_platform_config  # noqa: E402
from vs_api.builder_visual_draft import BuilderVisualDraftService  # noqa: E402
from vs_api.catalog_context import CatalogContext  # noqa: E402

SHIPPED_ROOT = ROOT / "catalog/nodalarc"
OUTPUT_DIR = ROOT / "frontend/src/builder/__tests__/fixtures/shipped"
WORLD_FIELDS = ("session", "nodes", "segments", "link_rules")
# The number of nodes the resolver may plan for; the dev cluster's figure.
AVAILABLE_NODE_COUNT = 1_000


def shipped_sessions() -> list[Path]:
    return sorted((SHIPPED_ROOT / "sessions").glob("*.yaml"))


def render(session_path: Path, user_root: Path) -> str:
    scope = CatalogScope()
    repository = FilesystemCatalogRepository(
        shipped_root=SHIPPED_ROOT, scope_roots={scope: user_root}
    )
    service = BuilderVisualDraftService(
        CatalogContext(repository=repository, scope=scope),
        clock=lambda: datetime(2026, 7, 10, 12, 34, 56, tzinfo=UTC),
    )
    source_ref = f"nodalarc:sessions/{session_path.name}"
    draft = service.open(BuilderVisualDraftOpenRequest(source_ref=source_ref))
    compile_result = service.compile(
        BuilderVisualDraftCompileRequest(draft=draft),
        available_node_count=AVAILABLE_NODE_COUNT,
    ).compile_result
    if compile_result.canonical_session_json is None or compile_result.resolved_preview is None:
        raise RuntimeError(f"{source_ref} compiled with no canonical session or resolved world")
    document = {
        "source_ref": source_ref,
        "compile": compile_result.model_dump(
            mode="json",
            include={"canonical_session_json": True, "resolved_preview": set(WORLD_FIELDS)},
        ),
    }
    return json.dumps(document, indent=2, sort_keys=True) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="exit 1 when any file is stale")
    arguments = parser.parse_args()
    init_platform_config(ROOT / "configs/platform.yaml")
    stale: list[str] = []
    with tempfile.TemporaryDirectory() as scratch:
        for session_path in shipped_sessions():
            output = OUTPUT_DIR / f"{session_path.stem}.json"
            rendered = render(session_path, Path(scratch) / session_path.stem)
            if arguments.check:
                current = output.read_text() if output.exists() else ""
                if current != rendered:
                    stale.append(str(output.relative_to(ROOT)))
                continue
            OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
            output.write_text(rendered)
            print(f"wrote {output.relative_to(ROOT)}")
    if stale:
        print(f"stale: {', '.join(stale)}; run {Path(__file__).name}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
