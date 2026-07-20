from types import SimpleNamespace

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
