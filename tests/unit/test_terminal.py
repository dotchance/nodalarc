"""VS-API terminal proxy regressions."""

import base64
from types import SimpleNamespace
from unittest.mock import Mock

import vs_api.terminal as terminal_mod


def _secret(private_key: str, resource_version: str | None = None):
    return SimpleNamespace(
        data={"id_ed25519": base64.b64encode(private_key.encode()).decode()},
        metadata=SimpleNamespace(resource_version=resource_version),
    )


def _reset_key_cache() -> None:
    terminal_mod._ssh_key = None
    terminal_mod._ssh_key_cache_key = None


def test_load_ssh_key_reuses_current_secret_revision(monkeypatch):
    _reset_key_cache()
    key = object()
    client = Mock()
    client.read_namespaced_secret.return_value = _secret("private-one", "101")
    import_private_key = Mock(return_value=key)

    monkeypatch.setattr(terminal_mod, "_get_k8s_client", lambda: client)
    monkeypatch.setattr(terminal_mod.asyncssh, "import_private_key", import_private_key)

    assert terminal_mod._load_ssh_key("nodalarc") is key
    assert terminal_mod._load_ssh_key("nodalarc") is key

    assert client.read_namespaced_secret.call_count == 2
    import_private_key.assert_called_once_with("private-one")


def test_load_ssh_key_reloads_when_secret_revision_changes(monkeypatch):
    _reset_key_cache()
    key_one = object()
    key_two = object()
    client = Mock()
    client.read_namespaced_secret.side_effect = [
        _secret("private-one", "101"),
        _secret("private-two", "102"),
    ]
    import_private_key = Mock(side_effect=[key_one, key_two])

    monkeypatch.setattr(terminal_mod, "_get_k8s_client", lambda: client)
    monkeypatch.setattr(terminal_mod.asyncssh, "import_private_key", import_private_key)

    assert terminal_mod._load_ssh_key("nodalarc") is key_one
    assert terminal_mod._load_ssh_key("nodalarc") is key_two

    assert import_private_key.call_args_list[0].args == ("private-one",)
    assert import_private_key.call_args_list[1].args == ("private-two",)
