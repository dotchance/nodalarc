"""The ConstellationSpec contract: one spec shape, one set of coordinates."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from nodalarc.catalog_upload import CatalogUploadSelection
from nodalarc.cr_runtime_config import (
    CONSTELLATION_SPEC_PHASES,
    CR_API_VERSION,
    CR_GROUP,
    CR_KIND,
    CR_NAME,
    CR_PLURAL,
    CR_VERSION,
    ConstellationSpecSpec,
    ConstellationSpecStatus,
    cr_status_observes_current_generation,
)
from nodalarc.runtime_config import RuntimeConfigProof
from pydantic import ValidationError

DIGEST = "sha256:" + "c" * 64
CRD_PATH = (
    Path(__file__).resolve().parents[2] / "deploy" / "helm" / "crds" / "constellationspec.yaml"
)


def _selection() -> CatalogUploadSelection:
    return CatalogUploadSelection(upload_id="upload-spec-test", closure_digest=DIGEST, file_count=3)


def test_spec_round_trips_through_its_cr_shape() -> None:
    spec = ConstellationSpecSpec.of(
        session_yaml="session:\n  name: x\n", catalog_upload=_selection()
    )

    written = spec.to_cr()

    assert written == {
        "sessionYaml": "session:\n  name: x\n",
        "catalogUpload": {
            "upload_id": "upload-spec-test",
            "closure_digest": DIGEST,
            "file_count": 3,
        },
    }
    assert ConstellationSpecSpec.from_cr(written) == spec


@pytest.mark.parametrize(
    ("spec", "fragment"),
    [
        (
            {
                "sessionYaml": "x",
                "catalogUpload": _selection().model_dump(mode="json"),
                "sessionFile": "old",
            },
            "Extra inputs are not permitted",
        ),
        ({"sessionYaml": "x"}, "catalogUpload"),
        ({"catalogUpload": _selection().model_dump(mode="json")}, "sessionYaml"),
        ({"sessionYaml": "", "catalogUpload": _selection().model_dump(mode="json")}, "sessionYaml"),
        ({"sessionYaml": "x", "catalogUpload": {"upload_id": "u"}}, "catalogUpload"),
        (
            {"session_yaml": "x", "catalog_upload": _selection().model_dump(mode="json")},
            "Extra inputs are not permitted",
        ),
    ],
)
def test_incomplete_or_widened_specs_are_refused(spec: dict, fragment: str) -> None:
    with pytest.raises(ValidationError) as raised:
        ConstellationSpecSpec.from_cr(spec)

    assert fragment in str(raised.value)


def test_coordinates_are_one_definition() -> None:
    assert (CR_GROUP, CR_VERSION, CR_PLURAL, CR_NAME) == (
        "nodalarc.io",
        "v1alpha1",
        "constellationspecs",
        "current-session",
    )
    assert CR_API_VERSION == "nodalarc.io/v1alpha1"
    assert CR_KIND == "ConstellationSpec"


def _crd_status_schema() -> dict:
    crd = yaml.safe_load(CRD_PATH.read_text(encoding="utf-8"))
    version = crd["spec"]["versions"][0]
    return version["schema"]["openAPIV3Schema"]["properties"]["status"]["properties"]


def test_status_phase_vocabulary_is_the_crd_enum() -> None:
    assert tuple(_crd_status_schema()["phase"]["enum"]) == CONSTELLATION_SPEC_PHASES


def test_status_keys_are_the_crd_properties() -> None:
    aliases = {field.alias or name for name, field in ConstellationSpecStatus.model_fields.items()}
    assert aliases == set(_crd_status_schema())


def test_status_patch_carries_only_the_fields_it_sets() -> None:
    patch = ConstellationSpecStatus.from_cr({"phase": "Wiring", "podCount": 7, "readyPods": 3})

    assert patch.to_patch() == {"phase": "Wiring", "podCount": 7, "readyPods": 3}
    assert patch.observed_generation is None
    # An explicit null is a deliberate merge-patch deletion and stays one.
    assert ConstellationSpecStatus.from_cr({"message": None}).to_patch() == {"message": None}


def test_absent_status_is_a_legitimate_empty_observation() -> None:
    observed = ConstellationSpecStatus.from_cr(None)

    assert observed.phase is None
    assert observed.to_patch() == {}
    assert observed.observes_generation(1) is False
    assert cr_status_observes_current_generation({"metadata": {"generation": 1}}) is False


def test_observes_generation_requires_the_same_positive_generation() -> None:
    observed = ConstellationSpecStatus.from_cr({"observedGeneration": 3})

    assert observed.observes_generation(3) is True
    assert observed.observes_generation(2) is False
    assert observed.observes_generation(0) is False
    assert observed.observes_generation(True) is False
    assert cr_status_observes_current_generation(
        {"metadata": {"generation": 3}, "status": {"observedGeneration": 3}}
    )


@pytest.mark.parametrize(
    ("status", "fragment"),
    [
        ({"phase": "Booting"}, "phase"),
        ({"phase": "Ready", "surprise": 1}, "Extra inputs are not permitted"),
        ({"observedGeneration": "1"}, "observedGeneration"),
        ({"podCount": "7"}, "podCount"),
    ],
)
def test_status_outside_the_contract_is_refused(status: dict, fragment: str) -> None:
    with pytest.raises(ValidationError) as raised:
        ConstellationSpecStatus.from_cr(status)

    assert fragment in str(raised.value)


@pytest.mark.parametrize(
    ("counts", "expected"),
    [
        ({"podCount": 3, "readyPods": 3, "wiredPods": 3}, 3),
        ({"podCount": 3, "readyPods": 2, "wiredPods": 3}, None),
        ({"podCount": 3, "readyPods": 3, "wiredPods": 2}, None),
        ({"podCount": 0, "readyPods": 0, "wiredPods": 0}, None),
        ({"podCount": 3, "readyPods": 3}, None),
        ({}, None),
    ],
)
def test_ready_pod_count_requires_equal_positive_counts(
    counts: dict[str, int], expected: int | None
) -> None:
    assert ConstellationSpecStatus.from_cr(counts).ready_pod_count() == expected


def test_carries_compares_only_the_fields_the_intended_status_sets() -> None:
    live = ConstellationSpecStatus.from_cr(
        {"phase": "Ready", "sessionName": "a", "sessionRunId": "run-1", "podCount": 4}
    )

    assert live.carries(ConstellationSpecStatus.from_cr({"sessionName": "a"}))
    assert live.carries(ConstellationSpecStatus.from_cr({"sessionName": "a", "podCount": 4}))
    assert not live.carries(ConstellationSpecStatus.from_cr({"sessionRunId": "run-2"}))
    assert not live.carries(ConstellationSpecStatus.from_cr({"wiredPods": 4}))


def test_runtime_mismatches_name_the_cr_keys() -> None:
    other = "sha256:" + "d" * 64
    proof = RuntimeConfigProof(
        source_origin="test.cr_runtime_config",
        run_id="run-1",
        upload_id="upload-1",
        document_digest=DIGEST,
        closure_digest=DIGEST,
        resolved_semantic_digest=DIGEST,
        file_count=1,
        total_bytes=1,
        resolved_node_count=1,
    )
    live = ConstellationSpecStatus.from_cr(
        {
            "documentDigest": DIGEST,
            "closureDigest": other,
            "resolvedSemanticDigest": DIGEST,
            "runtimeRelease": "0.7.1",
            "runtimeBuild": "abc",
        }
    )

    mismatches = live.runtime_mismatches(
        document_digest=proof.document_digest,
        closure_digest=proof.closure_digest,
        resolved_semantic_digest=proof.resolved_semantic_digest,
        release="0.7.1",
        build="xyz",
    )

    assert mismatches == ("closureDigest", "runtimeBuild")
