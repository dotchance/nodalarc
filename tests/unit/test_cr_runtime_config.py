"""The ConstellationSpec contract: one spec shape, one set of coordinates."""

from __future__ import annotations

import pytest
from nodalarc.catalog_upload import CatalogUploadSelection
from nodalarc.cr_runtime_config import (
    CR_API_VERSION,
    CR_GROUP,
    CR_KIND,
    CR_NAME,
    CR_PLURAL,
    CR_VERSION,
    ConstellationSpecSpec,
)
from pydantic import ValidationError

DIGEST = "sha256:" + "c" * 64


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
