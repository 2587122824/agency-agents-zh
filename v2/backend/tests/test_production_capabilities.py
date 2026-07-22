from types import SimpleNamespace

from v2.backend.app.production.planning_service import _route_feasibility_errors
from v2.backend.app.production.service import _validate_generation_capabilities


def workflow(*, tags: list[str], bindings: list[dict]):
    return SimpleNamespace(capability_tags=tags, node_info_list=bindings)


def shot(code: str, requirements: dict):
    return SimpleNamespace(id=f"id-{code}", shot_code=code, generation_requirements=requirements)


def test_generation_capabilities_report_exact_shots_without_route_replacement() -> None:
    requirements = {
        "reference_image_required": True,
        "multi_frame_required": True,
        "identity_consistency_required": True,
        "precise_text_required": True,
    }
    shots = [shot("SH-001", requirements)]
    keyframe = workflow(tags=["text_to_image"], bindings=[{"value_source": "shot.visual_prompt"}])
    video = workflow(tags=["i2v", "first_frame"], bindings=[])
    errors: list[dict] = []

    _validate_generation_capabilities(shots, {"id-SH-001": None}, keyframe, video, errors)

    assert {item["code"] for item in errors} == {
        "REFERENCE_IMAGE_CAPABILITY_MISSING",
        "REQUIRED_REFERENCE_IMAGE_MISSING",
        "MULTI_FRAME_CAPABILITY_MISSING",
        "IDENTITY_CONSISTENCY_CAPABILITY_MISSING",
        "PRECISE_TEXT_CAPABILITY_MISSING",
    }
    assert all(item["shot_code"] == "SH-001" for item in errors)
    assert keyframe.capability_tags == ["text_to_image"]
    assert video.capability_tags == ["i2v", "first_frame"]


def test_generation_capabilities_accept_explicit_declared_support() -> None:
    requirements = {
        "reference_image_required": True,
        "multi_frame_required": True,
        "identity_consistency_required": True,
        "precise_text_required": True,
    }
    current = shot("SH-001", requirements)
    keyframe = workflow(tags=["precise_text"], bindings=[{"value_source": "reference_image.primary"}])
    video = workflow(tags=["multi_frame"], bindings=[])
    errors: list[dict] = []

    _validate_generation_capabilities([current], {current.id: {"content_hash": "known"}}, keyframe, video, errors)

    assert errors == []


def route_workflow(
    workflow_id: str,
    *,
    operation_kind: str,
    tags: list[str],
    bindings: list[dict],
    video_spec_id: str = "spec-1",
):
    return SimpleNamespace(
        id=workflow_id,
        operation_kind=operation_kind,
        status="published",
        supported_video_spec_ids=[video_spec_id],
        capability_tags=tags,
        node_info_list=bindings,
    )


def test_route_preflight_blocks_precise_pixel_text_before_model_invocation() -> None:
    current = SimpleNamespace(
        shot_code="SH-001",
        primary_reference_entity_version_id=None,
        generation_requirements={
            "reference_image_required": False,
            "multi_frame_required": False,
            "identity_consistency_required": False,
            "precise_text_required": True,
        },
    )
    workflows = [
        route_workflow(
            "image-1",
            operation_kind="image_generation",
            tags=["text_to_image"],
            bindings=[{"value_source": "shot.visual_prompt"}],
        ),
        route_workflow(
            "video-1",
            operation_kind="video_generation",
            tags=["first_frame"],
            bindings=[{"value_source": "source_image"}],
        ),
    ]

    errors = _route_feasibility_errors(
        [current], workflows, SimpleNamespace(id="spec-1"),
    )

    assert errors == [{
        "code": "PRODUCTION_PLAN_NO_FEASIBLE_ROUTE",
        "shot_code": "SH-001",
        "causes": ["PRODUCTION_PLAN_PRECISE_TEXT_CAPABILITY_MISSING"],
    }]


def test_route_preflight_keeps_final_overlay_text_out_of_generation_capabilities() -> None:
    current = SimpleNamespace(
        shot_code="SH-001",
        primary_reference_entity_version_id=None,
        generation_requirements={
            "reference_image_required": False,
            "multi_frame_required": False,
            "identity_consistency_required": False,
            "precise_text_required": False,
        },
    )
    workflows = [
        route_workflow(
            "image-1",
            operation_kind="image_generation",
            tags=["text_to_image"],
            bindings=[{"value_source": "shot.visual_prompt"}],
        ),
        route_workflow(
            "video-1",
            operation_kind="video_generation",
            tags=["first_frame"],
            bindings=[{"value_source": "source_image"}],
        ),
    ]

    assert _route_feasibility_errors(
        [current], workflows, SimpleNamespace(id="spec-1"),
    ) == []
