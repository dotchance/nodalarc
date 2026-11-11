# Copyright 2024-2026 .chance (dotchance)
# SPDX-License-Identifier: AGPL-3.0-only
# Additional terms under AGPL-3.0 Section 7 are in the NOTICE file.
"""Workload preparation: adapter render failures, and the image each container runs."""

from __future__ import annotations

import json
from pathlib import Path

import nodalarc_operator.workloads.preparation as preparation
import pytest
from nodalarc.resolve_session import load_session_resolution_from_file
from nodalarc.workloads.adapter import AdapterRenderRefusal
from nodalarc_operator.workloads.images import (
    WORKLOAD_IMAGES_ENV,
    WorkloadImageMapError,
    is_logical_reference,
    load_workload_image_map,
)
from nodalarc_operator.workloads.preparation import (
    WorkloadPreparationError,
    prepare_session_workloads,
)

from tests.catalog_session_fixtures import shipped_read_view, shipped_workload_images

ROOT = Path(__file__).resolve().parents[3]


class _FailingAdapter:
    def __init__(self, failure: Exception) -> None:
        self._failure = failure

    def render_node(self, resolved_node, session_context):
        raise self._failure


def _prepare_with(monkeypatch: pytest.MonkeyPatch, failure: Exception) -> None:
    monkeypatch.setattr(preparation, "adapter_named", lambda _name: _FailingAdapter(failure))
    resolution = load_session_resolution_from_file(
        ROOT / "catalog/nodalarc/sessions/earth-leo-simple.yaml", catalog=shipped_read_view()
    )
    prepare_session_workloads(resolution, namespace="nodalarc", owner_ref={"uid": "test-uid"})


def test_an_adapter_refusal_fails_preparation_with_its_reason(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    refusal = AdapterRenderRefusal("no metric for isl0")

    with pytest.raises(
        WorkloadPreparationError, match=r"^adapter 'frr' cannot render '[a-z0-9-]+': no metric"
    ) as failed:
        _prepare_with(monkeypatch, refusal)

    assert failed.value.__cause__ is refusal


def test_any_other_render_failure_fails_preparation_with_its_type_and_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    failure = KeyError("isl9")

    with pytest.raises(
        WorkloadPreparationError,
        match=r"^adapter 'frr' failed to render '[a-z0-9-]+': KeyError: 'isl9'$",
    ) as failed:
        _prepare_with(monkeypatch, failure)

    assert failed.value.__cause__ is failure


def _prepare_earth_leo(monkeypatch: pytest.MonkeyPatch, image_map: dict[str, str]):
    resolution = load_session_resolution_from_file(
        ROOT / "catalog/nodalarc/sessions/earth-leo-simple.yaml", catalog=shipped_read_view()
    )
    monkeypatch.setenv(WORKLOAD_IMAGES_ENV, json.dumps(image_map))
    monkeypatch.setenv("IMAGE_PULL_POLICY", "Always")
    monkeypatch.setenv("WIRING_GATE_IMAGE", "registry.test/nodalarc/base:gate")
    return resolution, prepare_session_workloads(
        resolution, namespace="nodalarc", owner_ref={"uid": "test-uid"}
    )


def test_every_container_runs_the_image_the_map_names(monkeypatch: pytest.MonkeyPatch) -> None:
    image_map = shipped_workload_images()
    resolution, prepared = _prepare_earth_leo(monkeypatch, image_map)

    frr = next(iter(resolution.workload_profiles.values()))
    assert prepared.composed
    for workload in prepared.composed.values():
        primary = next(
            c
            for c in workload.composition.containers
            if c.name == workload.composition.primary_container
        )
        assert primary.image == image_map[f"{frr.registry}/{frr.image}"]
        assert primary.image_pull_policy == "Always"
        for container in (*workload.composition.init_containers, *workload.composition.containers):
            assert not is_logical_reference(container.image), container.image


def test_an_image_that_is_not_logical_runs_as_written(monkeypatch: pytest.MonkeyPatch) -> None:
    """The DTN profiles name the upstream uD3TN image directly, by its real digest."""
    resolution = load_session_resolution_from_file(
        ROOT / "catalog/nodalarc/sessions/earth-luna-dtn.yaml", catalog=shipped_read_view()
    )
    monkeypatch.setenv(WORKLOAD_IMAGES_ENV, json.dumps(shipped_workload_images()))
    monkeypatch.setenv("IMAGE_PULL_POLICY", "Always")
    prepared = prepare_session_workloads(resolution, namespace="nodalarc", owner_ref={"uid": "u"})

    images = {
        container.image
        for workload in prepared.composed.values()
        for container in (*workload.composition.init_containers, *workload.composition.containers)
    }
    upstream = {image for image in images if image.startswith("registry.gitlab.com/")}
    assert upstream
    assert all("@sha256:" in image and not is_logical_reference(image) for image in upstream)


def test_a_logical_image_the_map_does_not_name_is_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    image_map = {
        key: value for key, value in shipped_workload_images().items() if "/frr@" not in key
    }
    with pytest.raises(
        WorkloadPreparationError,
        match=r"^node '[a-z0-9-]+': profile nodalarc:profiles/frr-router.yaml names "
        r"registry.example/nodalarc/frr@sha256:0{64}, which the workload image map "
        r"\(WORKLOAD_IMAGES\) does not resolve$",
    ):
        _prepare_earth_leo(monkeypatch, image_map)


def test_a_new_image_is_a_new_workload_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    first = shipped_workload_images()
    _, prepared = _prepare_earth_leo(monkeypatch, first)
    _, again = _prepare_earth_leo(monkeypatch, first)
    _, moved = _prepare_earth_leo(monkeypatch, shipped_workload_images(registry="registry.other"))

    assert prepared.identity == again.identity
    assert moved.identity != prepared.identity


@pytest.mark.parametrize(
    ("raw", "reason"),
    [
        ("{not json", "is not valid JSON"),
        ('["a list"]', "must be a JSON object"),
        ('{"registry.test/nodalarc/frr:1": "registry.test/nodalarc/frr:2"}', "is not a logical"),
        (
            json.dumps({f"registry.example/nodalarc/frr@sha256:{'0' * 64}": ""}),
            "empty or malformed",
        ),
        (
            json.dumps(
                {
                    f"registry.example/nodalarc/frr@sha256:{'0' * 64}": (
                        f"registry.example/nodalarc/base@sha256:{'0' * 64}"
                    )
                }
            ),
            "another logical reference",
        ),
    ],
)
def test_a_malformed_map_is_refused(monkeypatch: pytest.MonkeyPatch, raw: str, reason: str) -> None:
    monkeypatch.setenv(WORKLOAD_IMAGES_ENV, raw)
    with pytest.raises(WorkloadImageMapError, match=reason):
        load_workload_image_map()
    resolution = load_session_resolution_from_file(
        ROOT / "catalog/nodalarc/sessions/earth-leo-simple.yaml", catalog=shipped_read_view()
    )
    with pytest.raises(WorkloadPreparationError, match=reason):
        prepare_session_workloads(resolution, namespace="nodalarc", owner_ref={"uid": "u"})
