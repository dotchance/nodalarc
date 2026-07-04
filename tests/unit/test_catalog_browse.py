# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""User-catalog library semantics: dual-root browse, validated writes,
shipped-immutability, and hermetic flattening of user references."""

from __future__ import annotations

import pytest
import yaml
from nodalarc.catalog_browse import (
    browse_catalog,
    delete_user_object,
    flatten_user_references,
    read_catalog_object,
    rehydrate_user_references,
    save_user_object,
)
from nodalarc.catalog_paths import CatalogRoots

_TERMINAL = {
    "terminal": {
        "id": "my-ka-terminal",
        "display_name": "My Ka terminal",
        "medium": "rf",
        "signal": {"band": "ka", "frequency_hz": 29.5e9},
        "bandwidth_mbps": {"transmit": 500.0, "receive": 500.0},
        "tracking_capacity": 1,
        "max_range_km": 2500.0,
        "limits": {
            "azimuth_deg": {"min": -180, "max": 180},
            "elevation_deg": {"min": 20, "max": 90},
            "max_tracking_rate_deg_s": 2.0,
        },
        "reference": "test",
    }
}


@pytest.fixture()
def roots(tmp_path) -> CatalogRoots:
    shipped = tmp_path / "catalog" / "nodalarc"
    (shipped / "terminals" / "rf").mkdir(parents=True)
    (shipped / "sessions").mkdir(parents=True)
    shipped_terminal = dict(_TERMINAL)
    (shipped / "terminals" / "rf" / "shipped-ka.yaml").write_text(
        yaml.dump({"terminal": {**_TERMINAL["terminal"], "id": "shipped-ka"}}),
        encoding="utf-8",
    )
    del shipped_terminal
    user = tmp_path / "data" / "user-catalog"
    user.mkdir(parents=True)
    return CatalogRoots.from_catalog_root(shipped, user_root=user)


def test_browse_lists_both_tiers_yours_first(roots):
    """The user's entries lead: burying them under the shipped vocabulary
    made a just-saved terminal read as missing."""
    save_user_object("terminals", _TERMINAL, roots=roots)
    entries = browse_catalog("terminals", roots=roots)
    refs = [entry.ref for entry in entries]
    assert refs == [
        "user:terminals/my-ka-terminal.yaml",
        "nodalarc:terminals/rf/shipped-ka.yaml",
    ]
    assert all(entry.error is None for entry in entries)


def test_terminal_summary_carries_planning_capabilities(roots):
    """The picker line a user plans links from: signal, range, rate, slew,
    tracks. A summary that silently comes back None (the broad best-effort
    except) is exactly the regression this pins."""
    save_user_object("terminals", _TERMINAL, roots=roots)
    entries = browse_catalog("terminals", roots=roots)
    by_ref = {entry.ref: entry for entry in entries}
    summary = by_ref["user:terminals/my-ka-terminal.yaml"].summary
    assert summary == "rf ka 29.5 GHz · 2500 km · 500 Mbps · slew 2°/s · tracks 1"


def test_save_validates_and_writes_canonical_grammar(roots):
    entry = save_user_object("terminals", _TERMINAL, roots=roots)
    assert entry.ref == "user:terminals/my-ka-terminal.yaml"
    wrapper, document = read_catalog_object(entry.ref, roots=roots)
    assert wrapper == "terminal"
    assert document["terminal"]["id"] == "my-ka-terminal"


def test_save_rejects_invalid_documents_and_wrong_family(roots):
    with pytest.raises(Exception):
        save_user_object("terminals", {"terminal": {"id": "broken"}}, roots=roots)
    with pytest.raises(ValueError, match="expected 'node'"):
        save_user_object("nodes", _TERMINAL, roots=roots)
    assert browse_catalog("terminals", roots=roots)[-1].ref.startswith("nodalarc:")


def test_save_requires_explicit_overwrite(roots):
    save_user_object("terminals", _TERMINAL, roots=roots)
    with pytest.raises(FileExistsError):
        save_user_object("terminals", _TERMINAL, roots=roots)
    entry = save_user_object("terminals", _TERMINAL, roots=roots, overwrite=True)
    assert entry.id == "my-ka-terminal"


def test_delete_only_reaches_user_entries(roots):
    entry = save_user_object("terminals", _TERMINAL, roots=roots)
    delete_user_object(entry.ref, roots=roots)
    assert [e.ref for e in browse_catalog("terminals", roots=roots)] == [
        "nodalarc:terminals/rf/shipped-ka.yaml"
    ]
    with pytest.raises(ValueError, match="only user catalog entries"):
        delete_user_object("nodalarc:terminals/rf/shipped-ka.yaml", roots=roots)


def test_flatten_replaces_user_refs_and_keeps_shipped_refs(roots):
    save_user_object("terminals", _TERMINAL, roots=roots)
    document = {
        "node": {
            "terminals": [
                {"terminal": "user:terminals/my-ka-terminal.yaml"},
                {"terminal": "nodalarc:terminals/rf/shipped-ka.yaml"},
            ]
        }
    }
    flattened = flatten_user_references(document, roots=roots)
    inline = flattened["node"]["terminals"][0]["terminal"]
    assert inline["terminal"]["id"] == "my-ka-terminal"
    assert flattened["node"]["terminals"][1]["terminal"] == "nodalarc:terminals/rf/shipped-ka.yaml"


def test_flatten_detects_reference_cycles(roots, tmp_path):
    # Hand-write a self-referencing user entry: the validator does not follow
    # refs, so the cycle only exists at flatten time.
    cyc = {
        "node": {
            "id": "cyclic",
            "forwarding": "routed",
            "ethernet": [],
            "terminals": [
                {
                    "id": "t0",
                    "role": "access",
                    "terminal": "user:nodes/cyclic.yaml",
                    "count": 1,
                }
            ],
            "payloads": [],
        }
    }
    nodes_dir = roots.user_root / "nodes"
    nodes_dir.mkdir(parents=True, exist_ok=True)
    (nodes_dir / "cyclic.yaml").write_text(yaml.dump(cyc), encoding="utf-8")
    with pytest.raises(ValueError, match="cycle"):
        flatten_user_references({"x": "user:nodes/cyclic.yaml"}, roots=roots)


def test_rehydrate_is_flatten_inverse_while_the_library_matches(roots):
    """A hermetic save inlines user refs; loading for editing re-references
    them — and only them. flatten(rehydrate(x)) == x, and rehydrate restores
    the exact refs the authoring form used."""
    save_user_object("terminals", _TERMINAL, roots=roots)
    document = {
        "node": {
            "terminals": [
                {"terminal": "user:terminals/my-ka-terminal.yaml"},
                {"terminal": "nodalarc:terminals/rf/shipped-ka.yaml"},
            ]
        }
    }
    flattened = flatten_user_references(document, roots=roots)
    rehydrated = rehydrate_user_references(flattened, roots=roots)
    assert rehydrated == document
    assert flatten_user_references(rehydrated, roots=roots) == flattened


def test_rehydrate_leaves_drifted_inline_objects_verbatim(roots):
    """An inline object that no longer matches the library is the file's
    truth — it must stay inline, never be re-referenced to different
    content."""
    save_user_object("terminals", _TERMINAL, roots=roots)
    document = {"terminal": "user:terminals/my-ka-terminal.yaml"}
    flattened = flatten_user_references(document, roots=roots)
    # The library entry drifts after the save.
    drifted = {"terminal": {**_TERMINAL["terminal"], "display_name": "Renamed since the save"}}
    save_user_object("terminals", drifted, roots=roots, overwrite=True)
    rehydrated = rehydrate_user_references(flattened, roots=roots)
    assert rehydrated == flattened
