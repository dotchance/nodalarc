from pathlib import Path

from services.vs_api import main as vs_api_main

ROOT = Path(__file__).resolve().parents[2]


def test_vs_api_image_contains_only_the_existing_public_grammar_asset() -> None:
    dockerfile = (ROOT / "services/vs_api/Dockerfile").read_text(encoding="utf-8")

    assert "docs/ops/configuration-grammar.md" in dockerfile
    assert "docs/ops/configuration-schema.json" not in dockerfile
    assert "docs/ops/configuration-contract.json" not in dockerfile


def test_vs_api_serves_builder_bootstrap_contract_links() -> None:
    route = next(
        route
        for route in vs_api_main.app.routes
        if getattr(route, "name", None) == "configuration-docs"
    )

    assert route.path == "/docs/ops"
    assert Path(route.app.directory) == ROOT / "docs/ops"
