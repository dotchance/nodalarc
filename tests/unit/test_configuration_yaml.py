"""Strict decoding and runtime-required persisted configuration semantics."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest
import yaml
from nodalarc.catalog_closure import (
    CatalogClosureCollector,
    CatalogClosureError,
    CatalogClosureErrorCode,
)
from nodalarc.configuration_yaml import load_configuration_yaml
from nodalarc.models.segment_session import (
    EphemerisKernel,
    SegmentSessionConfig,
    TimeConfig,
)
from nodalarc.resolve_session import SessionResolutionError, resolve_session
from nodalarc.runtime_support import FeatureCategory, UnsupportedFeatureError
from pydantic import ValidationError

ROOT = Path(__file__).resolve().parents[2]
SIMPLE_SESSION = ROOT / "catalog" / "nodalarc" / "sessions" / "earth-leo-simple.yaml"


class _NoReads:
    def read(self, ref):
        raise AssertionError(f"duplicate-key root must fail before dependency read: {ref}")


def _simple_session() -> dict:
    return yaml.safe_load(SIMPLE_SESSION.read_bytes())


def _ground_segment(session: dict) -> dict:
    return next(segment for segment in session["segments"] if "placement" in segment)


def test_configuration_loader_rejects_duplicate_keys_at_every_depth() -> None:
    with pytest.raises(yaml.constructor.ConstructorError, match="duplicate key 'name'"):
        load_configuration_yaml("session:\n  name: first\n  name: second\nsegments: []\n")


def test_catalog_closure_classifies_duplicate_root_key_as_invalid_yaml() -> None:
    root = b"session:\n  name: first\n  name: second\nsegments: []\n"

    with pytest.raises(CatalogClosureError) as caught:
        CatalogClosureCollector.collect(root, _NoReads())

    assert caught.value.code == CatalogClosureErrorCode.INVALID_ROOT_YAML
    assert caught.value.evidence.cause_type == "ConstructorError"


@pytest.mark.parametrize("field", ("step_seconds", "compression"))
@pytest.mark.parametrize(
    "value",
    (0, -1, float("inf"), float("-inf"), float("nan"), True, "1"),
)
def test_time_numeric_fields_require_finite_positive_values(
    field: str, value: float | bool | str
) -> None:
    document = {
        "start_time": "2026-06-08T00:00:00Z",
        "step_seconds": 1,
        "compression": 1,
    }
    document[field] = value

    with pytest.raises(ValidationError):
        TimeConfig.model_validate(document)


def test_persisted_session_requires_explicit_time() -> None:
    document = _simple_session()
    del document["time"]

    with pytest.raises(ValidationError, match="time"):
        SegmentSessionConfig.model_validate(document)


@pytest.mark.parametrize(
    "path",
    (
        "/absolute/kernel.bsp",
        "C:/absolute/kernel.bsp",
        "configs//kernel.bsp",
        "configs/kernel.bsp/",
        "./configs/kernel.bsp",
        "configs/./kernel.bsp",
        "configs/../kernel.bsp",
        "configs\\kernel.bsp",
        "..",
    ),
)
def test_asset_paths_reject_noncanonical_or_uncontained_forms(path: str) -> None:
    with pytest.raises(ValidationError):
        EphemerisKernel.model_validate(
            {
                "id": "kernel",
                "path": path,
                "targets": ["nodalarc:bodies/earth.yaml"],
                "frame": "gcrs",
            }
        )


def test_asset_path_accepts_canonical_relative_form() -> None:
    kernel = EphemerisKernel.model_validate(
        {
            "id": "kernel",
            "path": "configs/ephemerides/kernel.bsp",
            "targets": ["nodalarc:bodies/earth.yaml"],
            "frame": "gcrs",
        }
    )

    assert kernel.path == "configs/ephemerides/kernel.bsp"


def test_access_candidate_requires_complete_explicit_ground_scheduling() -> None:
    document = _simple_session()
    scheduling = _ground_segment(document)["apply"]["scheduling"]
    del scheduling["ranking_order"]

    with pytest.raises(SessionResolutionError, match="incomplete ground scheduling.*ranking_order"):
        resolve_session(document)


def test_ground_without_access_candidates_does_not_require_scheduling() -> None:
    document = _simple_session()
    ground = _ground_segment(document)
    del ground["apply"]["scheduling"]
    document["link_rules"] = [rule for rule in document["link_rules"] if rule["id"] != "leo_access"]

    resolved = resolve_session(document)

    assert resolved.ground_candidate_satellites_by_gs() == {}
    assert any(
        node.kind == "ground_station" and node.ground_scheduling is None for node in resolved.nodes
    )


@pytest.mark.parametrize(
    ("field", "value", "feature_value"),
    (
        ("handover_concurrency", "all_at_once", "handover_concurrency:all_at_once"),
        ("mbb_reserve", 2, "mbb_reserve:2"),
        ("bbm_acquire_timeout_ticks", 2, "bbm_acquire_timeout_ticks:2"),
    ),
)
def test_future_ground_scheduling_is_typed_runtime_unsupported(
    field: str, value: object, feature_value: str
) -> None:
    document = _simple_session()
    scheduling = _ground_segment(document)["apply"]["scheduling"]
    scheduling[field] = value

    with pytest.raises(UnsupportedFeatureError) as caught:
        resolve_session(deepcopy(document))

    assert any(
        feature.category == FeatureCategory.GROUND_SCHEDULING and feature.value == feature_value
        for feature in caught.value.features
    )
