"""The Builder fixtures the frontend tests render are what VS-API answers today.

`frontend/src/builder/__tests__/fixtures/shipped/<session>.json` holds, per shipped session, the
visual draft the Builder receives on open and the world the backend resolves for it. The
frontend test `shippedSessionAnatomy.test.tsx` checks the page's claims against that world.
This test opens and compiles every shipped session in-process and refuses a stale file.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
GENERATOR = ROOT / "scripts/gen_builder_shipped_drafts.py"


def _generator():
    spec = importlib.util.spec_from_file_location("gen_builder_shipped_drafts", GENERATOR)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize(
    "session_path", _generator().shipped_sessions(), ids=lambda path: path.stem
)
def test_the_shipped_session_fixture_is_what_the_builder_answers(
    session_path: Path, tmp_path: Path
) -> None:
    generator = _generator()
    output = generator.OUTPUT_DIR / f"{session_path.stem}.json"
    assert output.exists(), f"{output.relative_to(ROOT)} is missing; run {GENERATOR.name}"
    assert output.read_text() == generator.render(session_path, tmp_path / "user"), (
        f"{output.relative_to(ROOT)} is stale; run {GENERATOR.relative_to(ROOT)}"
    )


def test_no_fixture_outlives_its_shipped_session() -> None:
    generator = _generator()
    shipped = {path.stem for path in generator.shipped_sessions()}
    written = {path.stem for path in generator.OUTPUT_DIR.glob("*.json")}
    assert written == shipped, f"fixtures without a shipped session: {sorted(written - shipped)}"
