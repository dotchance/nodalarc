from pathlib import Path

import pytest
from nodalarc.catalog_repository import CatalogScope, UnknownCatalogScopeError
from nodalarc.filesystem_catalog_repository import FilesystemCatalogRepository
from vs_api import catalog_context as context_module
from vs_api.catalog_context import CatalogContext


@pytest.fixture(autouse=True)
def _reset_catalog_context() -> None:
    context_module.reset_catalog_context_for_testing()
    yield
    context_module.reset_catalog_context_for_testing()


def _context(tmp_path: Path, name: str = "context") -> CatalogContext:
    shipped_root = tmp_path / f"{name}-shipped"
    shipped_root.mkdir()
    return context_module.create_catalog_context(
        session_data_root=tmp_path / f"{name}-sessions",
        shipped_root=shipped_root,
    )


def test_local_context_uses_configured_dedicated_repository_root(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    shipped_root = tmp_path / "shipped"
    shipped_root.mkdir()
    session_data_root = tmp_path / "sessions"
    legacy_user_catalog = session_data_root / "user-catalog"
    legacy_user_catalog.mkdir(parents=True)
    legacy_marker = legacy_user_catalog / "legacy.yaml"
    legacy_marker.write_text("legacy: true\n")
    config = type("Config", (), {"session_data_root": str(session_data_root)})()
    monkeypatch.setattr(context_module, "get_platform_config", lambda: config)

    context = context_module.create_catalog_context(
        shipped_root=shipped_root,
    )

    repository_root = session_data_root / "catalog-repository" / "local-default"
    assert isinstance(context.repository, FilesystemCatalogRepository)
    assert context.repository.writer_coordination == "single-process"
    assert (repository_root / "CURRENT").is_file()
    assert (repository_root / "generations").is_dir()
    assert legacy_marker.read_text() == "legacy: true\n"
    assert repository_root != legacy_user_catalog


def test_context_scope_is_opaque_and_repository_isolated(tmp_path: Path) -> None:
    context = _context(tmp_path)

    assert repr(context.scope) == "<CatalogScope opaque>"
    context.repository.snapshot(context.scope)
    with pytest.raises(UnknownCatalogScopeError, match="was not injected"):
        context.repository.snapshot(CatalogScope())
