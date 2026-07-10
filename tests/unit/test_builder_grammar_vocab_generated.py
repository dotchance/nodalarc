"""Guard: the generated frontend grammar vocabulary stays in sync with the
authoritative Python grammar enums.

Fails if `scripts/gen_builder_grammar_vocab.py` would produce different bytes
than the committed `frontend/src/builder/generated/grammarVocab.ts` — i.e. a
grammar Literal changed but the generated TS was not regenerated.

This aligns shared enums/vocabulary; it does NOT make the browser serializer
authoritative and does NOT prove full grammar parity.
"""

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "gen_builder_grammar_vocab.py"
GENERATED = ROOT / "frontend" / "src" / "builder" / "generated" / "grammarVocab.ts"


def test_generated_grammar_vocab_is_fresh():
    assert SCRIPT.exists(), "codegen script missing"
    assert GENERATED.exists(), "generated grammarVocab.ts missing — run the codegen"
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--check"],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        "generated/grammarVocab.ts is stale vs the Python grammar enums. "
        "Regenerate: uv run python scripts/gen_builder_grammar_vocab.py\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )
